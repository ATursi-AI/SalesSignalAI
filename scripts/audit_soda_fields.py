#!/usr/bin/env python3
"""
Audit SODA datasets for contact fields we might be missing.
Checks metadata + sample data for phone, email, owner, name fields.

Usage: python scripts/audit_soda_fields.py
Output: scripts/soda_audit_report.json + stdout summary
"""
import json
import time
from datetime import datetime, timezone

import requests

DATASETS = [
    # NYC Open Data
    ("data.cityofnewyork.us", "43nn-pn8j"),  # ECB Violations
    ("data.cityofnewyork.us", "ipu4-2q9a"),  # DOB Permits
    ("data.cityofnewyork.us", "6bgk-3dad"),  # DOB Violations
    ("data.cityofnewyork.us", "bs8b-p36w"),  # DOB Complaints
    ("data.cityofnewyork.us", "rbx6-tga4"),  # Health Inspections
    ("data.cityofnewyork.us", "w9ak-ipjd"),  # Liquor Licenses
    # NY State
    ("data.ny.gov", "k4vb-judh"),
    ("data.ny.gov", "f8i8-k2gm"),
    ("data.ny.gov", "9s3h-dpkz"),
    # Santa Clara County
    ("data.sccgov.org", "2u2d-8jej"),
    ("data.sccgov.org", "vuw7-jmjk"),
    ("data.sccgov.org", "wkaa-4ccv"),
    # San Diego County
    ("data.sandiegocounty.gov", "nd4s-9r7d"),
]

CONTACT_KEYWORDS = [
    "phone", "tel", "mobile", "cell", "fax",
    "email", "e_mail", "mail",
    "contact", "owner", "name", "applicant", "respondent",
    "operator", "licensee", "registrant", "petitioner",
    "business_name", "dba", "firm", "company",
]

DELAY = 2.0


def fetch_metadata(portal, dataset_id):
    url = f"https://{portal}/api/views/{dataset_id}.json"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            return r.json()
        print(f"    WARN: metadata {r.status_code} for {portal}/{dataset_id}")
    except Exception as e:
        print(f"    ERROR: metadata fetch failed: {e}")
    return None


def fetch_samples(portal, dataset_id, limit=3):
    url = f"https://{portal}/resource/{dataset_id}.json"
    try:
        r = requests.get(url, params={"$limit": limit}, timeout=20)
        if r.status_code == 200:
            return r.json()
        print(f"    WARN: samples {r.status_code}")
    except Exception as e:
        print(f"    ERROR: samples fetch failed: {e}")
    return []


def is_contact_field(field_name):
    fn = field_name.lower()
    return any(kw in fn for kw in CONTACT_KEYWORDS)


def audit_dataset(portal, dataset_id):
    print(f"\n  [{portal}] {dataset_id}")

    meta = fetch_metadata(portal, dataset_id)
    time.sleep(DELAY)

    if not meta:
        return {"portal": portal, "dataset_id": dataset_id, "error": "metadata fetch failed"}

    name = meta.get("name", "Unknown")
    columns = meta.get("columns", [])
    print(f"    Name: {name} ({len(columns)} columns)")

    # Find contact-related columns
    contact_cols = []
    for col in columns:
        fn = col.get("fieldName", "")
        if fn.startswith(":"):  # skip system columns
            continue
        if is_contact_field(fn):
            contact_cols.append({
                "field": fn,
                "name": col.get("name", fn),
                "type": col.get("dataTypeName", "unknown"),
                "description": (col.get("description") or "")[:100],
            })

    if contact_cols:
        print(f"    Contact fields: {len(contact_cols)}")
        for c in contact_cols:
            print(f"      - {c['field']} ({c['type']}): {c['name']}")
    else:
        print(f"    Contact fields: NONE")

    # Fetch samples
    samples = fetch_samples(portal, dataset_id)
    time.sleep(DELAY)

    # Check sample values for contact fields
    for cf in contact_cols:
        fn = cf["field"]
        values = [rec.get(fn) for rec in samples]
        cf["sample_values"] = values
        populated = sum(1 for v in values if v)
        cf["populated_pct"] = round(populated / max(len(values), 1) * 100)

    return {
        "portal": portal,
        "dataset_id": dataset_id,
        "name": name,
        "total_columns": len([c for c in columns if not c.get("fieldName", "").startswith(":")]),
        "contact_fields": contact_cols,
        "all_fields": [
            c.get("fieldName", "") for c in columns
            if not c.get("fieldName", "").startswith(":")
        ],
        "sample_records": samples[:3],
    }


def main():
    print("=" * 60)
    print("  SODA DATASET CONTACT FIELD AUDIT")
    print(f"  {len(DATASETS)} datasets to scan")
    print("=" * 60)

    report = {
        "audit_date": datetime.now(timezone.utc).isoformat(),
        "datasets": [],
    }

    for portal, did in DATASETS:
        result = audit_dataset(portal, did)
        report["datasets"].append(result)

    # Save JSON report
    out_path = "scripts/soda_audit_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n\nReport saved to {out_path}")

    # Summary table
    print("\n" + "=" * 80)
    print(f"{'Dataset':15s} {'Name':35s} {'Contact Fields'}")
    print("-" * 80)

    total_fields = 0
    for ds in report["datasets"]:
        if "error" in ds:
            print(f"{ds['dataset_id']:15s} {'ERROR':35s} {ds.get('error', '')}")
            continue
        cfs = ds.get("contact_fields", [])
        total_fields += len(cfs)
        field_summary = ", ".join(
            f"{c['field']}({c.get('populated_pct', '?')}%)"
            for c in cfs
        ) if cfs else "—"
        print(f"{ds['dataset_id']:15s} {ds['name'][:35]:35s} {field_summary}")

    print("-" * 80)
    print(f"Total contact fields found across all datasets: {total_fields}")
    print()


if __name__ == "__main__":
    main()
