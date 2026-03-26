"""
Tool library for agents. Each tool is a function with a description and JSON schema.
"""
import logging
import requests

logger = logging.getLogger('agents')


def search_nyc_dob(query_type="violations", borough="queens", days=7, limit=50):
    """Search NYC DOB for violations or permits."""
    datasets = {"violations": "3h2n-5cm9", "permits": "ic3t-wcy2"}
    did = datasets.get(query_type)
    if not did:
        return {"error": f"Unknown query type: {query_type}"}
    url = f"https://data.cityofnewyork.us/resource/{did}.json"
    boro_map = {'manhattan': '1', 'bronx': '2', 'brooklyn': '3', 'queens': '4', 'staten_island': '5'}
    params = {"$limit": limit, "$order": ":created_at DESC"}
    if borough.lower() in boro_map:
        params["boro"] = boro_map[borough.lower()]
    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 200:
            data = r.json()
            return {"count": len(data), "records": data[:20]}
        return {"error": f"API {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def search_data_gov(query, limit=10):
    """Search data.gov for open government datasets."""
    try:
        r = requests.get("https://catalog.data.gov/api/3/action/package_search",
                         params={"q": query, "rows": limit}, timeout=30)
        if r.status_code == 200:
            results = []
            for pkg in r.json().get("result", {}).get("results", []):
                resources = [{"url": res.get("url", ""), "format": res.get("format", ""), "name": res.get("name", "")}
                             for res in pkg.get("resources", [])[:3]]
                results.append({
                    "title": pkg.get("title", ""),
                    "description": (pkg.get("notes", "") or "")[:200],
                    "organization": pkg.get("organization", {}).get("title", ""),
                    "resources": resources,
                    "metadata_url": f"https://catalog.data.gov/dataset/{pkg.get('name', '')}",
                })
            return {"count": len(results), "datasets": results}
        return {"error": f"data.gov {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def search_socrata_portal(domain, query, limit=20):
    """Search any Socrata open data portal."""
    try:
        r = requests.get(f"https://{domain}/api/catalog/v1", params={"q": query, "limit": limit}, timeout=30)
        if r.status_code == 200:
            results = []
            for item in r.json().get("results", []):
                res = item.get("resource", {})
                results.append({
                    "name": res.get("name", ""), "description": (res.get("description", "") or "")[:200],
                    "id": res.get("id", ""), "type": res.get("type", ""), "domain": domain,
                    "api_url": f"https://{domain}/resource/{res.get('id', '')}.json",
                    "columns": res.get("columns_name", [])[:10],
                })
            return {"count": len(results), "datasets": results}
        return {"error": f"Portal {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def fetch_socrata_data(domain, dataset_id, limit=20, where_clause=""):
    """Fetch records from a Socrata dataset."""
    try:
        params = {"$limit": limit}
        if where_clause:
            params["$where"] = where_clause
        r = requests.get(f"https://{domain}/resource/{dataset_id}.json", params=params, timeout=30)
        if r.status_code == 200:
            data = r.json()
            return {"count": len(data), "records": data[:10], "total_fetched": len(data)}
        return {"error": f"Dataset {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def fetch_webpage(url):
    """Fetch a public webpage and extract text + data-related links."""
    import re
    from html.parser import HTMLParser
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; SalesSignalBot/1.0)"}, timeout=30)
        if r.status_code != 200:
            return {"error": f"URL {r.status_code}"}
        class _T(HTMLParser):
            def __init__(self):
                super().__init__(); self.text = []; self.skip = False
            def handle_starttag(self, tag, attrs):
                if tag in ('script', 'style'): self.skip = True
            def handle_endtag(self, tag):
                if tag in ('script', 'style'): self.skip = False
            def handle_data(self, data):
                if not self.skip and data.strip(): self.text.append(data.strip())
        t = _T(); t.feed(r.text)
        links = re.findall(r'href=["\']([^"\']+)["\']', r.text)
        data_links = [l for l in links if any(k in l.lower() for k in ['data', 'api', 'open', 'dataset', '.json', '.csv'])]
        return {"url": url, "text_preview": ' '.join(t.text)[:2000], "data_links": data_links[:20]}
    except Exception as e:
        return {"error": str(e)}


def save_lead_to_repository(platform="public_records", content="", author="", state="", region="",
                             source_type="", contact_name="", contact_business="",
                             contact_address="", contact_phone="", contact_email="", raw_data=None):
    """Save a qualified lead to the SalesSignal repository."""
    from core.utils.monitors.lead_processor import process_lead
    lead, created, assigned = process_lead(
        platform=platform, source_url="agent://scout", content=content, author=author,
        raw_data=raw_data or {}, state=state, region=region,
        source_group='public_records', source_type=source_type,
        contact_name=contact_name, contact_business=contact_business,
        contact_address=contact_address, contact_phone=contact_phone, contact_email=contact_email,
    )
    return {"saved": created, "lead_id": lead.id if lead else None, "assignments": assigned, "duplicate": not created and lead is not None}


def check_lead_count(state="", region="", source_type=""):
    """Check how many leads exist for given filters."""
    from core.models.leads import Lead
    qs = Lead.objects.all()
    if state: qs = qs.filter(state=state)
    if region: qs = qs.filter(region__icontains=region)
    if source_type: qs = qs.filter(source_type=source_type)
    return {"count": qs.count()}


def send_sms_notification(to_number, message):
    """Send an SMS via SignalWire."""
    from django.conf import settings
    try:
        from signalwire.rest import Client
        client = Client(settings.SIGNALWIRE_PROJECT_ID, settings.SIGNALWIRE_API_TOKEN,
                        signalwire_space_url=settings.SIGNALWIRE_SPACE_URL)
        msg = client.messages.create(from_=settings.SIGNALWIRE_SMS_NUMBER, to=to_number, body=message[:1600])
        return {"sent": True, "sid": msg.sid}
    except Exception as e:
        return {"error": str(e)}


# ── Tool definitions with schemas for Claude ────────────────────────

TOOL_DEFINITIONS = {
    "search_nyc_dob": {
        "function": search_nyc_dob,
        "description": "Search NYC DOB for violations or permits.",
        "schema": {
            "type": "object",
            "properties": {
                "query_type": {"type": "string", "enum": ["violations", "permits"]},
                "borough": {"type": "string", "enum": ["manhattan", "brooklyn", "queens", "bronx", "staten_island"]},
                "days": {"type": "integer", "default": 7},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["query_type", "borough"],
        },
    },
    "search_data_gov": {
        "function": search_data_gov,
        "description": "Search data.gov for open government datasets. Good for finding new data sources in any state.",
        "schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 10}},
            "required": ["query"],
        },
    },
    "search_socrata_portal": {
        "function": search_socrata_portal,
        "description": "Search a Socrata open data portal (data.cityofnewyork.us, data.lacity.org, data.ca.gov, etc).",
        "schema": {
            "type": "object",
            "properties": {"domain": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer", "default": 20}},
            "required": ["domain", "query"],
        },
    },
    "fetch_socrata_data": {
        "function": fetch_socrata_data,
        "description": "Fetch actual records from a Socrata dataset.",
        "schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"}, "dataset_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20}, "where_clause": {"type": "string", "default": ""},
            },
            "required": ["domain", "dataset_id"],
        },
    },
    "fetch_webpage": {
        "function": fetch_webpage,
        "description": "Fetch a public webpage and extract text content and data-related links.",
        "schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
    "save_lead_to_repository": {
        "function": save_lead_to_repository,
        "description": "Save a qualified lead to the SalesSignal repository.",
        "schema": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "default": "public_records"},
                "content": {"type": "string"}, "author": {"type": "string"},
                "state": {"type": "string"}, "region": {"type": "string"},
                "source_type": {"type": "string"}, "contact_name": {"type": "string", "default": ""},
                "contact_business": {"type": "string", "default": ""}, "contact_address": {"type": "string", "default": ""},
                "contact_phone": {"type": "string", "default": ""}, "contact_email": {"type": "string", "default": ""},
            },
            "required": ["content", "author", "state", "region", "source_type"],
        },
    },
    "check_lead_count": {
        "function": check_lead_count,
        "description": "Check how many leads exist in the repository for given filters.",
        "schema": {
            "type": "object",
            "properties": {"state": {"type": "string", "default": ""}, "region": {"type": "string", "default": ""}, "source_type": {"type": "string", "default": ""}},
        },
    },
    "send_sms_notification": {
        "function": send_sms_notification,
        "description": "Send an SMS text message via SignalWire.",
        "schema": {
            "type": "object",
            "properties": {"to_number": {"type": "string"}, "message": {"type": "string"}},
            "required": ["to_number", "message"],
        },
    },
}
