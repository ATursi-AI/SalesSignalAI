#!/usr/bin/env python3
"""Seed DatasetRegistry with existing SODA monitors."""
import os
import sys
import django

# Support both VPS and local paths
for p in ['/root/SalesSignalAI', os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]:
    if os.path.isdir(p):
        sys.path.insert(0, p)
        break

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'salessignal.settings')
django.setup()

from core.models.data_sources import DatasetRegistry

DATASETS = [
    {
        'state': 'NY',
        'portal_domain': 'data.cityofnewyork.us',
        'dataset_id': '43nn-pn8j',
        'name': 'NYC Restaurant Inspections',
        'data_type': 'health_inspections',
        'date_field': 'inspection_date',
        'phone_field': 'phone',
        'name_field': 'dba',
        'update_frequency': 'daily',
        'notes': 'Phone 100% populated. DBA 100% populated.',
    },
    {
        'state': 'NY',
        'portal_domain': 'data.cityofnewyork.us',
        'dataset_id': 'ipu4-2q9a',
        'name': 'DOB Permit Issuance',
        'data_type': 'permits',
        'date_field': 'filing_date',
        'phone_field': 'owner_s_phone__',
        'name_field': 'owner_s_first_name',
        'update_frequency': 'daily',
        'notes': 'Owner phone 100% populated. Also has permittee_s_phone__, owner_s_business_name.',
    },
    {
        'state': 'NY',
        'portal_domain': 'data.cityofnewyork.us',
        'dataset_id': '6bgk-3dad',
        'name': 'DOB ECB Violations',
        'data_type': 'violations',
        'date_field': 'issue_date',
        'name_field': 'respondent_name',
        'address_field': 'respondent_street',
        'update_frequency': 'daily',
        'notes': 'Respondent name/address 100% populated. No phone field.',
    },
    {
        'state': 'NY',
        'portal_domain': 'data.cityofnewyork.us',
        'dataset_id': 'bs8b-p36w',
        'name': 'DOB Certificate of Occupancy',
        'data_type': 'permits',
        'date_field': 'c_o_issue_date',
        'name_field': '',
        'update_frequency': 'daily',
        'notes': 'No contact fields beyond street_name.',
    },
    {
        'state': 'NY',
        'portal_domain': 'data.cityofnewyork.us',
        'dataset_id': 'rbx6-tga4',
        'name': 'DOB NOW Approved Permits',
        'data_type': 'permits',
        'date_field': 'issued_date',
        'name_field': 'owner_name',
        'address_field': 'owner_street_address',
        'update_frequency': 'daily',
        'notes': 'Owner name/business 100% populated. Applicant fields also available. No phone.',
    },
    {
        'state': 'NY',
        'portal_domain': 'data.cityofnewyork.us',
        'dataset_id': 'w9ak-ipjd',
        'name': 'DOB NOW Job Filings',
        'data_type': 'permits',
        'date_field': 'filing_date',
        'name_field': 'applicant_first_name',
        'update_frequency': 'daily',
        'notes': 'Applicant + owner + filing rep fields. No phone.',
    },
    {
        'state': 'NY',
        'portal_domain': 'data.ny.gov',
        'dataset_id': 'k4vb-judh',
        'name': 'NY Business Filings',
        'data_type': 'business_filings',
        'date_field': 'initial_dos_filing_date',
        'name_field': 'corp_name',
        'update_frequency': 'daily',
        'notes': 'Corp name + filer name 100% populated. No phone/email.',
    },
    {
        'state': 'NY',
        'portal_domain': 'data.ny.gov',
        'dataset_id': 'f8i8-k2gm',
        'name': 'SLA Pending Licenses',
        'data_type': 'liquor_licenses',
        'date_field': 'received_date',
        'name_field': 'legalname',
        'update_frequency': 'weekly',
        'notes': 'Legal name 100%, DBA 33%. No phone.',
    },
    {
        'state': 'NY',
        'portal_domain': 'data.ny.gov',
        'dataset_id': '9s3h-dpkz',
        'name': 'SLA Active Licenses',
        'data_type': 'liquor_licenses',
        'date_field': 'lastissuedate',
        'name_field': 'legalname',
        'update_frequency': 'weekly',
        'notes': 'Legal name 100%, DBA 33%. No phone.',
    },
    {
        'state': 'CA',
        'portal_domain': 'data.sccgov.org',
        'dataset_id': '2u2d-8jej',
        'name': 'Santa Clara Inspections',
        'data_type': 'health_inspections',
        'date_field': 'inspection_date',
        'update_frequency': 'weekly',
        'notes': 'No contact fields. Join with vuw7-jmjk for business info.',
    },
    {
        'state': 'CA',
        'portal_domain': 'data.sccgov.org',
        'dataset_id': 'vuw7-jmjk',
        'name': 'Santa Clara Food Business',
        'data_type': 'health_inspections',
        'phone_field': 'phone_number',
        'name_field': 'name',
        'update_frequency': 'weekly',
        'notes': 'Phone 100% populated. Name 100% populated.',
    },
    {
        'state': 'CA',
        'portal_domain': 'data.sccgov.org',
        'dataset_id': 'wkaa-4ccv',
        'name': 'Santa Clara Violations',
        'data_type': 'violations',
        'date_field': 'violation_date',
        'update_frequency': 'weekly',
        'notes': 'No contact fields. Join with vuw7-jmjk for business info.',
    },
    {
        'state': 'CA',
        'portal_domain': 'data.lacity.org',
        'dataset_id': 'u82d-eh7z',
        'name': 'LA Building Code Enforcement',
        'data_type': 'code_enforcement',
        'date_field': 'adddttm',
        'status_field': 'stat',
        'update_frequency': 'weekly',
        'notes': 'Open cases only (stat=O). 2-3 week data lag. No contact fields.',
    },
    {
        'state': 'CA',
        'portal_domain': 'data.sfgov.org',
        'dataset_id': 'nbtm-fbw5',
        'name': 'SF Building Violations',
        'data_type': 'violations',
        'date_field': 'date_filed',
        'update_frequency': 'daily',
        'notes': 'Updated daily. 510K records. No phone/email. Has neighborhood.',
    },
    {
        'state': 'CA',
        'portal_domain': 'data.sfgov.org',
        'dataset_id': 'i98e-djp9',
        'name': 'SF Building Permits',
        'data_type': 'permits',
        'date_field': 'filed_date',
        'update_frequency': 'nightly',
        'notes': '1M+ records. Has estimated_cost for filtering. No contact fields.',
    },
]


def main():
    created = 0
    updated = 0
    for ds in DATASETS:
        defaults = {
            'state': ds['state'],
            'name': ds['name'],
            'data_type': ds['data_type'],
            'date_field': ds.get('date_field', ''),
            'phone_field': ds.get('phone_field', ''),
            'name_field': ds.get('name_field', ''),
            'address_field': ds.get('address_field', ''),
            'status_field': ds.get('status_field', ''),
            'update_frequency': ds.get('update_frequency', ''),
            'notes': ds.get('notes', ''),
            'api_url': f"https://{ds['portal_domain']}/resource/{ds['dataset_id']}.json",
            'is_active': True,
        }
        obj, was_created = DatasetRegistry.objects.update_or_create(
            portal_domain=ds['portal_domain'],
            dataset_id=ds['dataset_id'],
            defaults=defaults,
        )
        if was_created:
            created += 1
            print(f"  Created: {ds['name']}")
        else:
            updated += 1
            print(f"  Updated: {ds['name']}")

    print(f"\nDone. Created: {created}, Updated: {updated}")
    print(f"Total in registry: {DatasetRegistry.objects.count()}")


if __name__ == '__main__':
    main()
