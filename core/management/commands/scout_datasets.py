"""
Scout state open data portals for new datasets.
Searches SODA catalog APIs, analyzes field structures, saves DatasetCandidate records.

Usage:
    python manage.py scout_datasets --state NY
    python manage.py scout_datasets --state CA --smart
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
STATE_EXTRAS = {'NY': ['NYC']}
SEARCH_QUERIES = ['violations', 'permits', 'inspections', 'licenses', 'code enforcement', 'business filings']

PHONE_PATTERNS = ['phone', 'tel', 'mobile', 'contact_phone', 'owner_phone', 'business_phone', 'fax']
NAME_PATTERNS = ['name', 'owner', 'dba', 'business_name', 'respondent', 'applicant', 'corp_name', 'legalname', 'licensee', 'operator']
DATE_PATTERNS = ['date', 'issued', 'filed', 'inspection', 'created', 'received', 'recorded']
EMAIL_PATTERNS = ['email', 'e_mail', 'contact_email']


# ── Phone detection in actual data values ────────────────────────

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


# ── Column analysis ─────────────────────────────────────────────

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


# ── Gemini analysis ─────────────────────────────────────────────

def _call_gemini(prompt, max_tokens=2048):
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    model = 'gemini-2.0-flash'
    if not api_key:
        return None
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    body = {
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {'maxOutputTokens': max_tokens, 'temperature': 0.3},
    }
    try:
        r = requests.post(url, json=body, headers={'Content-Type': 'application/json'},
                          params={'key': api_key}, timeout=45)
        if r.status_code != 200:
            logger.warning(f'[Gemini] {r.status_code}: {r.text[:200]}')
            return None
        data = r.json()
        candidates = data.get('candidates', [])
        if candidates:
            parts = candidates[0].get('content', {}).get('parts', [])
            if parts:
                return parts[0].get('text', '')
    except Exception as e:
        logger.error(f'[Gemini] Error: {e}')
    return None


def _gemini_analyze(dataset_name, description, portal, columns, sample_data):
    """Call Gemini to deeply analyze a dataset for lead generation value."""
    cols_str = ', '.join(columns[:40])
    sample_str = json.dumps(sample_data[:3], indent=2, default=str)[:3000]

    prompt = f"""You are a lead intelligence analyst for a B2B lead generation company.

We found this dataset: {dataset_name}
Description: {description[:300]}
Portal: {portal}

Column names: {cols_str}

Sample records (first 3):
{sample_str}

Analyze this dataset for lead generation value:

1. CONTACT FIELDS: Which fields contain actual contact information? Look at data values, not just names.
2. PHONE DETECTION: Do any fields contain phone numbers? Look for 10-digit patterns, formatted numbers.
3. LEAD VALUE SIGNALS: What patterns make some records more valuable?
   - Permits WITHOUT a contractor = homeowner DIY = needs services
   - Health violations with low scores = urgent need
   - Recent dates = fresh leads
4. SERVICE MATCHES: What service businesses would want these leads?
5. RELEVANCE: Rate HIGH / MEDIUM / LOW for lead generation.
6. RECOMMENDED FILTERS: What $where clauses filter to best leads?

Respond ONLY in JSON (no markdown fences):
{{"actual_phone_fields": [], "actual_name_fields": [], "lead_value_signals": [{{"pattern": "", "meaning": "", "value": ""}}], "service_matches": [], "relevance": "HIGH", "relevance_reasoning": "", "recommended_filters": [], "insights": ""}}"""

    text = _call_gemini(prompt)
    if not text:
        return None

    # Parse JSON from response (handle markdown fences)
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        # Try to find JSON in the response
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    logger.warning(f'[Gemini] Could not parse response for {dataset_name}')
    return None


# ── Portal scouting ─────────────────────────────────────────────

def scout_portal(portal, state_code, queries, limit, stdout, smart=False):
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

            # Fetch sample data for phone scanning + Gemini
            sample_data = []
            if smart or not analysis['has_phone']:
                try:
                    sr = requests.get(f'https://{portal}/resource/{did}.json',
                                      params={'$limit': 5}, timeout=15)
                    if sr.status_code == 200:
                        sample_data = sr.json()
                except Exception:
                    pass

            # Scan sample data for hidden phone fields
            data_phone_fields = _scan_samples_for_phones(sample_data)
            if data_phone_fields:
                analysis['phone_fields'] = list(set(analysis['phone_fields'] + data_phone_fields))
                analysis['has_phone'] = True

            rel = _relevance(analysis)
            dtype = _guess_type(name, desc)
            row_count = res.get('page_views', {}).get('page_views_total', 0)
            actual_state = 'NY' if state_code == 'NYC' else state_code
            city = 'New York City' if state_code == 'NYC' else ''

            # Gemini smart analysis
            gemini_data = None
            if smart:
                stdout.write(f"    Analyzing with Gemini: {name[:50]}...")
                gemini_data = _gemini_analyze(name, desc, portal, analysis['all_fields'], sample_data)

            if gemini_data:
                # Override relevance with Gemini assessment
                if gemini_data.get('relevance') in ('HIGH', 'MEDIUM', 'LOW'):
                    rel = gemini_data['relevance']
                # Merge phone fields found by Gemini
                gp = gemini_data.get('actual_phone_fields', [])
                if gp:
                    analysis['phone_fields'] = list(set(analysis['phone_fields'] + gp))
                    analysis['has_phone'] = True

            candidate = DatasetCandidate.objects.create(
                name=name[:200], portal_domain=portal, dataset_id=did,
                api_url=f'https://{portal}/resource/{did}.json',
                state=actual_state, city=city, data_type=dtype, description=desc,
                total_records=row_count,
                has_phone_field=analysis['has_phone'], has_email_field=analysis['has_email'],
                has_name_field=analysis['has_name'],
                contact_fields_found=analysis['phone_fields'] + analysis['name_fields'] + analysis['email_fields'],
                all_fields=analysis['all_fields'][:50],
                sample_data=sample_data[:3],
                relevance=rel, status='new', discovered_by='scout_datasets' + (' (smart)' if smart else ''),
                service_matches=gemini_data.get('service_matches', []) if gemini_data else [],
                recommended_filters=gemini_data.get('recommended_filters', []) if gemini_data else [],
                lead_value_signals=gemini_data.get('lead_value_signals', []) if gemini_data else [],
                gemini_analyzed=bool(gemini_data),
                notes=(gemini_data.get('insights', '') + '\n' + gemini_data.get('relevance_reasoning', '')) if gemini_data else '',
            )
            created.append(candidate)

            if gemini_data:
                services = ', '.join(gemini_data.get('service_matches', [])[:5])
                filters = ', '.join(gemini_data.get('recommended_filters', [])[:3])
                stdout.write(f"    Found: {name[:50]} ({did}) - Gemini: {rel}")
                stdout.write(f"      Services: {services}")
                if filters:
                    stdout.write(f"      Filters: {filters}")
                if data_phone_fields:
                    stdout.write(f"      Phone in data: {', '.join(data_phone_fields)}")
            else:
                stdout.write(f"    Found: {name[:60]} ({did}) - {rel}")
                if data_phone_fields:
                    stdout.write(f"      Phone detected in data: {', '.join(data_phone_fields)}")

        time.sleep(1)

    return created


class Command(BaseCommand):
    help = 'Scout state open data portals for new datasets'

    def add_arguments(self, parser):
        parser.add_argument('--state', type=str, help='State code (NY, CA, TX, etc.)')
        parser.add_argument('--all', action='store_true', help='Scout all states')
        parser.add_argument('--keyword', type=str, default='', help='Optional keyword filter')
        parser.add_argument('--limit', type=int, default=30, help='Max results per query (default: 30)')
        parser.add_argument('--smart', action='store_true', help='Enable Gemini AI analysis of each dataset')

    def handle(self, *args, **options):
        state = (options.get('state') or '').upper()
        scout_all = options.get('all')
        keyword = (options.get('keyword') or '').strip()
        limit = options['limit']
        smart = options.get('smart', False)

        if not state and not scout_all:
            self.stdout.write(self.style.ERROR('Specify --state or --all'))
            return

        pairs = []
        if scout_all:
            for code, portal in PORTALS.items():
                pairs.append((code, portal))
        else:
            if state in PORTALS:
                pairs.append((state, PORTALS[state]))
            for ex in STATE_EXTRAS.get(state, []):
                if ex in PORTALS:
                    pairs.append((ex, PORTALS[ex]))

        if not pairs:
            self.stdout.write(self.style.ERROR(f'Unknown state: {state}'))
            return

        queries = [keyword] if keyword else SEARCH_QUERIES
        mode = 'SMART (Gemini)' if smart else 'FAST'

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  AGENT SCOUT — Dataset Discovery [{mode}]")
        self.stdout.write(f"  Portals: {len(pairs)} | Queries: {len(queries)} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        if smart and not getattr(settings, 'GEMINI_API_KEY', ''):
            self.stdout.write(self.style.WARNING('  WARNING: GEMINI_API_KEY not set. Smart mode will skip AI analysis.'))

        all_created = []
        for code, portal in pairs:
            self.stdout.write(f"\n--- {code}: {portal} ---")
            created = scout_portal(portal, code, queries, limit, self.stdout, smart=smart)
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
