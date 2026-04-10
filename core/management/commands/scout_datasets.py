"""
Scout state open data portals for new datasets.
Searches SODA catalog APIs, analyzes field structures, saves DatasetCandidate records.

Usage:
    python manage.py scout_datasets --state NY
    python manage.py scout_datasets --state CA --keyword plumbing
    python manage.py scout_datasets --all --limit 20
"""
import logging
import re
import time

import requests
from django.core.management.base import BaseCommand

from core.models.data_sources import DatasetCandidate, DatasetRegistry

logger = logging.getLogger('agents')

PORTALS = {
    'NYC': 'data.cityofnewyork.us',
    'NY': 'data.ny.gov',
    'CA': 'data.ca.gov',
    'TX': 'data.texas.gov',
    'FL': 'data.florida.gov',
    'IL': 'data.illinois.gov',
    'PA': 'data.pa.gov',
    'OH': 'data.ohio.gov',
    'GA': 'data.georgia.gov',
    'NC': 'data.nc.gov',
    'MI': 'data.michigan.gov',
    'WA': 'data.wa.gov',
    'CO': 'data.colorado.gov',
    'AZ': 'data.az.gov',
    'NV': 'data.nv.gov',
}

# States that also have a city portal
STATE_EXTRAS = {'NY': ['NYC']}

SEARCH_QUERIES = ['violations', 'permits', 'inspections', 'licenses', 'code enforcement', 'business filings']

PHONE_PATTERNS = ['phone', 'tel', 'mobile', 'contact_phone', 'owner_phone', 'business_phone', 'fax']
NAME_PATTERNS = ['name', 'owner', 'dba', 'business_name', 'respondent', 'applicant', 'corp_name', 'legalname', 'licensee', 'operator']
DATE_PATTERNS = ['date', 'issued', 'filed', 'inspection', 'created', 'received', 'recorded']
EMAIL_PATTERNS = ['email', 'e_mail', 'contact_email']


def _matches(field_name, patterns):
    fn = field_name.lower()
    return any(p in fn for p in patterns)


def _analyze_columns(columns):
    phone, name, date, email, all_f = [], [], [], [], []
    for col in columns:
        fn = col.get('fieldName', '')
        if fn.startswith(':'):
            continue
        all_f.append(fn)
        if _matches(fn, PHONE_PATTERNS):
            phone.append(fn)
        if _matches(fn, NAME_PATTERNS):
            name.append(fn)
        if _matches(fn, DATE_PATTERNS):
            date.append(fn)
        if _matches(fn, EMAIL_PATTERNS):
            email.append(fn)
    return {
        'phone_fields': phone, 'name_fields': name, 'date_fields': date,
        'email_fields': email, 'all_fields': all_f,
        'has_phone': bool(phone), 'has_name': bool(name),
        'has_date': bool(date), 'has_email': bool(email),
    }


def _relevance(a):
    if a['has_phone'] and a['has_date']:
        return 'HIGH'
    if a['has_name'] and a['has_date']:
        return 'MEDIUM'
    return 'LOW'


def _guess_type(name, desc):
    text = (name + ' ' + desc).lower()
    if any(w in text for w in ['violation', 'enforcement', 'complaint']):
        return 'violations'
    if any(w in text for w in ['permit', 'building permit', 'construction']):
        return 'permits'
    if any(w in text for w in ['inspection', 'health', 'food']):
        return 'health_inspections'
    if any(w in text for w in ['license', 'licensing', 'contractor']):
        return 'contractor_licenses'
    if any(w in text for w in ['business', 'filing', 'corporation']):
        return 'business_filings'
    return 'other'


def scout_portal(portal, state_code, queries, limit, stdout):
    """Scout a single portal. Returns list of created candidates."""
    created = []
    seen = set()

    for query in queries:
        stdout.write(f"  Searching {portal} for '{query}'...")
        try:
            r = requests.get(f'https://{portal}/api/catalog/v1',
                             params={'q': query, 'limit': limit}, timeout=20)
            if r.status_code != 200:
                stdout.write(f"    HTTP {r.status_code}")
                continue
            items = r.json().get('results', [])
            stdout.write(f"    {len(items)} results")
        except Exception as e:
            stdout.write(f"    Error: {e}")
            continue

        for item in items:
            res = item.get('resource', {})
            did = res.get('id', '')
            if not did or did in seen:
                continue
            seen.add(did)

            name = res.get('name', '')
            desc = (res.get('description', '') or '')[:500]
            if res.get('type', '') not in ('dataset', ''):
                continue

            if DatasetRegistry.objects.filter(portal_domain=portal, dataset_id=did).exists():
                continue
            if DatasetCandidate.objects.filter(portal_domain=portal, dataset_id=did).exists():
                continue

            # Fetch metadata
            try:
                meta = requests.get(f'https://{portal}/api/views/{did}.json', timeout=15)
                columns = meta.json().get('columns', []) if meta.status_code == 200 else []
            except Exception:
                columns = []

            analysis = _analyze_columns(columns)
            rel = _relevance(analysis)
            dtype = _guess_type(name, desc)
            row_count = res.get('page_views', {}).get('page_views_total', 0)

            actual_state = 'NY' if state_code == 'NYC' else state_code
            city = 'New York City' if state_code == 'NYC' else ''

            candidate = DatasetCandidate.objects.create(
                name=name[:200], portal_domain=portal, dataset_id=did,
                api_url=f'https://{portal}/resource/{did}.json',
                state=actual_state, city=city, data_type=dtype, description=desc,
                total_records=row_count,
                has_phone_field=analysis['has_phone'], has_email_field=analysis['has_email'],
                has_name_field=analysis['has_name'],
                contact_fields_found=analysis['phone_fields'] + analysis['name_fields'] + analysis['email_fields'],
                all_fields=analysis['all_fields'][:50],
                relevance=rel, status='new', discovered_by='scout_datasets',
            )
            created.append(candidate)
            stdout.write(f"    Found: {name[:60]} ({did}) - {rel}")

        time.sleep(1)

    return created


class Command(BaseCommand):
    help = 'Scout state open data portals for new datasets'

    def add_arguments(self, parser):
        parser.add_argument('--state', type=str, help='State code (NY, CA, TX, etc.)')
        parser.add_argument('--all', action='store_true', help='Scout all states')
        parser.add_argument('--keyword', type=str, default='', help='Optional keyword filter')
        parser.add_argument('--limit', type=int, default=30, help='Max results per query (default: 30)')

    def handle(self, *args, **options):
        state = options.get('state', '').upper()
        scout_all = options.get('all')
        keyword = options.get('keyword', '').strip()
        limit = options['limit']

        if not state and not scout_all:
            self.stdout.write(self.style.ERROR('Specify --state or --all'))
            return

        # Build list of (state_code, portal) pairs
        pairs = []
        if scout_all:
            for code, portal in PORTALS.items():
                pairs.append((code, portal))
        else:
            if state in PORTALS:
                pairs.append((state, PORTALS[state]))
            extras = STATE_EXTRAS.get(state, [])
            for ex in extras:
                if ex in PORTALS:
                    pairs.append((ex, PORTALS[ex]))

        if not pairs:
            self.stdout.write(self.style.ERROR(f'Unknown state: {state}'))
            return

        queries = [keyword] if keyword else SEARCH_QUERIES

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  AGENT SCOUT — Dataset Discovery")
        self.stdout.write(f"  Portals: {len(pairs)} | Queries: {len(queries)} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        all_created = []
        for code, portal in pairs:
            self.stdout.write(f"\n--- {code}: {portal} ---")
            created = scout_portal(portal, code, queries, limit, self.stdout)
            all_created.extend(created)

        high = sum(1 for c in all_created if c.relevance == 'HIGH')
        med = sum(1 for c in all_created if c.relevance == 'MEDIUM')
        low = sum(1 for c in all_created if c.relevance == 'LOW')
        phones = sum(1 for c in all_created if c.has_phone_field)

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  Done. Found {len(all_created)} new datasets.")
        self.stdout.write(f"  {high} HIGH, {med} MEDIUM, {low} LOW. {phones} have phone fields.")
        self.stdout.write(f"{'='*60}\n")
