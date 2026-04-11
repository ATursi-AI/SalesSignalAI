"""
Scout open data portals for new datasets using pluggable adapters.
Supports Socrata, ArcGIS, and CKAN portals.

Usage:
    python manage.py scout_datasets --state NY
    python manage.py scout_datasets --state CA --smart
    python manage.py scout_datasets --state NY --adapter arcgis
    python manage.py scout_datasets --all --limit 20
"""
import json
import logging
import re
import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from core.models.data_sources import DatasetCandidate, DatasetRegistry
from core.services.scout_adapters import get_portals_for_state, get_adapter, PORTAL_REGISTRY

logger = logging.getLogger('agents')

SEARCH_QUERIES = ['violations', 'permits', 'inspections', 'licenses', 'code enforcement', 'business filings']

PHONE_PATTERNS = ['phone', 'tel', 'mobile', 'contact_phone', 'owner_phone', 'business_phone', 'fax']
NAME_PATTERNS = ['name', 'owner', 'dba', 'business_name', 'respondent', 'applicant', 'corp_name', 'legalname', 'licensee', 'operator']
DATE_PATTERNS = ['date', 'issued', 'filed', 'inspection', 'created', 'received', 'recorded']
EMAIL_PATTERNS = ['email', 'e_mail', 'contact_email']


def _matches(field_name, patterns):
    return any(p in field_name.lower() for p in patterns)


def _analyze_columns(columns):
    phone, name, date, email, all_f = [], [], [], [], []
    for col in columns:
        fn = col.get('fieldName', '')
        if fn.startswith(':'):
            continue
        all_f.append(fn)
        if _matches(fn, PHONE_PATTERNS): phone.append(fn)
        if _matches(fn, NAME_PATTERNS): name.append(fn)
        if _matches(fn, DATE_PATTERNS): date.append(fn)
        if _matches(fn, EMAIL_PATTERNS): email.append(fn)
    return {
        'phone_fields': phone, 'name_fields': name, 'date_fields': date,
        'email_fields': email, 'all_fields': all_f,
        'has_phone': bool(phone), 'has_name': bool(name),
        'has_date': bool(date), 'has_email': bool(email),
    }


def _detect_phone_in_value(value):
    if not value or not isinstance(value, str):
        return False
    digits = re.sub(r'[\s\-\.\(\)\+]', '', str(value))
    return bool(re.match(r'^1?\d{10}$', digits))


def _scan_samples_for_phones(sample_records):
    phone_fields = {}
    for record in sample_records:
        for field, value in record.items():
            if _detect_phone_in_value(str(value) if value else ''):
                phone_fields[field] = phone_fields.get(field, 0) + 1
    total = max(len(sample_records), 1)
    return [f for f, count in phone_fields.items() if count >= total * 0.4]


def _relevance(a):
    if a['has_phone'] and a['has_date']: return 'HIGH'
    if a['has_name'] and a['has_date']: return 'MEDIUM'
    return 'LOW'


def _guess_type(name, desc):
    text = (name + ' ' + desc).lower()
    if any(w in text for w in ['violation', 'enforcement', 'complaint']): return 'violations'
    if any(w in text for w in ['permit', 'building permit', 'construction']): return 'permits'
    if any(w in text for w in ['inspection', 'health', 'food']): return 'health_inspections'
    if any(w in text for w in ['license', 'licensing', 'contractor']): return 'contractor_licenses'
    if any(w in text for w in ['business', 'filing', 'corporation']): return 'business_filings'
    return 'other'


# ── Gemini analysis ─────────────────────────────────────────────

def _call_gemini(prompt, max_tokens=2048):
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    model = 'gemini-2.0-flash'
    if not api_key:
        return None
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    body = {'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {'maxOutputTokens': max_tokens, 'temperature': 0.3}}
    try:
        r = requests.post(url, json=body, headers={'Content-Type': 'application/json'},
                          params={'key': api_key}, timeout=45)
        if r.status_code != 200:
            return None
        candidates = r.json().get('candidates', [])
        if candidates:
            parts = candidates[0].get('content', {}).get('parts', [])
            if parts:
                return parts[0].get('text', '')
    except Exception as e:
        logger.error(f'[Gemini] Error: {e}')
    return None


def _gemini_analyze(dataset_name, description, portal, columns, sample_data):
    cols_str = ', '.join(columns[:40])
    sample_str = json.dumps(sample_data[:3], indent=2, default=str)[:3000]
    prompt = f"""You are a lead intelligence analyst for a B2B lead generation company.

Dataset: {dataset_name}
Description: {description[:300]}
Portal: {portal}
Columns: {cols_str}

Sample records:
{sample_str}

Analyze for lead generation. Respond ONLY in JSON (no markdown):
{{"actual_phone_fields":[],"actual_name_fields":[],"lead_value_signals":[{{"pattern":"","meaning":"","value":""}}],"service_matches":[],"relevance":"HIGH","relevance_reasoning":"","recommended_filters":[],"insights":""}}"""

    text = _call_gemini(prompt)
    if not text:
        return None
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


# ── Main scout logic ────────────────────────────────────────────

def scout_portal(portal_url, portal_info, queries, limit, stdout, smart=False):
    """Scout a single portal using the appropriate adapter."""
    adapter_name = portal_info.get('adapter', 'socrata')
    try:
        adapter = get_adapter(adapter_name)
    except ValueError:
        stdout.write(f"    Unknown adapter: {adapter_name}")
        return []

    state = portal_info.get('state', '')
    city = portal_info.get('city', '')
    created = []
    seen = set()

    for query in queries:
        stdout.write(f"  [{adapter_name}] Searching {portal_url} for '{query}'...")
        results = adapter.search_catalog(portal_url, query, limit)
        stdout.write(f"    {len(results)} results")

        for ds in results:
            did = ds.get('id', '')
            if not did or did in seen:
                continue
            seen.add(did)

            name = ds.get('name', '')
            desc = ds.get('description', '')

            if DatasetRegistry.objects.filter(portal_domain=portal_url, dataset_id=did).exists():
                continue
            if DatasetCandidate.objects.filter(portal_domain=portal_url, dataset_id=did).exists():
                continue

            # Get metadata
            meta = adapter.get_metadata(portal_url, did)
            columns = meta.get('columns', [])
            analysis = _analyze_columns(columns)

            # Fetch samples for phone scanning + Gemini
            sample_data = []
            if smart or not analysis['has_phone']:
                sample_data = adapter.get_sample_records(portal_url, did)

            data_phone_fields = _scan_samples_for_phones(sample_data)
            if data_phone_fields:
                analysis['phone_fields'] = list(set(analysis['phone_fields'] + data_phone_fields))
                analysis['has_phone'] = True

            rel = _relevance(analysis)
            dtype = _guess_type(name, desc)
            row_count = ds.get('row_count', 0)
            api_url = adapter.build_api_url(portal_url, did)

            # Gemini analysis
            gemini_data = None
            if smart:
                stdout.write(f"    Gemini analyzing: {name[:50]}...")
                gemini_data = _gemini_analyze(name, desc, portal_url, analysis['all_fields'], sample_data)

            if gemini_data:
                if gemini_data.get('relevance') in ('HIGH', 'MEDIUM', 'LOW'):
                    rel = gemini_data['relevance']
                gp = gemini_data.get('actual_phone_fields', [])
                if gp:
                    analysis['phone_fields'] = list(set(analysis['phone_fields'] + gp))
                    analysis['has_phone'] = True

            candidate = DatasetCandidate.objects.create(
                name=name[:200], portal_domain=portal_url, dataset_id=did,
                api_url=api_url, state=state, city=city, data_type=dtype,
                description=desc[:500], total_records=row_count,
                has_phone_field=analysis['has_phone'], has_email_field=analysis['has_email'],
                has_name_field=analysis['has_name'],
                contact_fields_found=analysis['phone_fields'] + analysis['name_fields'] + analysis['email_fields'],
                all_fields=analysis['all_fields'][:50],
                sample_data=sample_data[:3],
                relevance=rel, status='new',
                discovered_by=f'scout ({adapter_name})' + (' smart' if smart else ''),
                service_matches=gemini_data.get('service_matches', []) if gemini_data else [],
                recommended_filters=gemini_data.get('recommended_filters', []) if gemini_data else [],
                lead_value_signals=gemini_data.get('lead_value_signals', []) if gemini_data else [],
                gemini_analyzed=bool(gemini_data),
                notes=((gemini_data.get('insights', '') + '\n' + gemini_data.get('relevance_reasoning', '')).strip()) if gemini_data else '',
            )
            created.append(candidate)

            if gemini_data:
                services = ', '.join(gemini_data.get('service_matches', [])[:5])
                stdout.write(f"    Found: {name[:50]} ({did}) - Gemini: {rel}")
                if services:
                    stdout.write(f"      Services: {services}")
                if data_phone_fields:
                    stdout.write(f"      Phone in data: {', '.join(data_phone_fields)}")
            else:
                stdout.write(f"    Found: {name[:60]} ({did}) - {rel}")
                if data_phone_fields:
                    stdout.write(f"      Phone in data: {', '.join(data_phone_fields)}")

        time.sleep(1)

    return created


class Command(BaseCommand):
    help = 'Scout open data portals for new datasets'

    def add_arguments(self, parser):
        parser.add_argument('--state', type=str, help='State code (NY, CA, TX, etc.)')
        parser.add_argument('--all', action='store_true', help='Scout all states')
        parser.add_argument('--keyword', type=str, default='', help='Optional keyword filter')
        parser.add_argument('--limit', type=int, default=30, help='Max results per query')
        parser.add_argument('--smart', action='store_true', help='Enable Gemini AI analysis')
        parser.add_argument('--adapter', type=str, default='', help='Test specific adapter (socrata, arcgis, ckan)')
        parser.add_argument('--portal', type=str, default='', help='Scout a specific portal URL')

    def handle(self, *args, **options):
        state = (options.get('state') or '').upper()
        scout_all = options.get('all')
        keyword = (options.get('keyword') or '').strip()
        limit = options['limit']
        smart = options.get('smart', False)
        adapter_filter = options.get('adapter', '').strip()
        portal_override = options.get('portal', '').strip()

        # Build portal list
        if portal_override:
            info = PORTAL_REGISTRY.get(portal_override, {'adapter': adapter_filter or 'socrata', 'state': state or '', 'city': ''})
            pairs = [(portal_override, info)]
        elif adapter_filter and not state and not scout_all:
            # Test a specific adapter on all portals of that type
            pairs = [(p, i) for p, i in PORTAL_REGISTRY.items() if i['adapter'] == adapter_filter][:3]
        elif scout_all:
            pairs = list(PORTAL_REGISTRY.items())
        elif state:
            pairs = get_portals_for_state(state)
        else:
            self.stdout.write(self.style.ERROR('Specify --state, --all, --portal, or --adapter'))
            return

        if adapter_filter:
            pairs = [(p, i) for p, i in pairs if i['adapter'] == adapter_filter]

        if not pairs:
            self.stdout.write(self.style.ERROR('No matching portals found.'))
            return

        queries = [keyword] if keyword else SEARCH_QUERIES
        mode = 'SMART (Gemini)' if smart else 'FAST'
        adapters_used = set(i['adapter'] for _, i in pairs)

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  AGENT SCOUT — Dataset Discovery [{mode}]")
        self.stdout.write(f"  Portals: {len(pairs)} | Adapters: {', '.join(adapters_used)}")
        self.stdout.write(f"  Queries: {len(queries)} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        if smart and not getattr(settings, 'GEMINI_API_KEY', ''):
            self.stdout.write(self.style.WARNING('  WARNING: GEMINI_API_KEY not set.'))

        all_created = []
        for portal_url, portal_info in pairs:
            adapter_name = portal_info.get('adapter', '?')
            self.stdout.write(f"\n--- {portal_info.get('state', '?')}: {portal_url} [{adapter_name}] ---")
            created = scout_portal(portal_url, portal_info, queries, limit, self.stdout, smart=smart)
            all_created.extend(created)

        high = sum(1 for c in all_created if c.relevance == 'HIGH')
        med = sum(1 for c in all_created if c.relevance == 'MEDIUM')
        low = sum(1 for c in all_created if c.relevance == 'LOW')
        phones = sum(1 for c in all_created if c.has_phone_field)
        gemini_count = sum(1 for c in all_created if c.gemini_analyzed)

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  Done. Found {len(all_created)} new datasets.")
        self.stdout.write(f"  {high} HIGH, {med} MEDIUM, {low} LOW. {phones} have phone fields.")
        if smart:
            self.stdout.write(f"  {gemini_count} analyzed by Gemini.")
        self.stdout.write(f"{'='*60}\n")
