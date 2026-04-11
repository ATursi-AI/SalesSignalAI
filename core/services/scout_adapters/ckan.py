import logging
import requests
from .base import BaseScoutAdapter

logger = logging.getLogger('agents')


class CKANAdapter(BaseScoutAdapter):
    """Adapter for CKAN-powered data portals (e.g. data.gov)."""

    name = "ckan"

    def search_catalog(self, portal_url, query, limit=30):
        try:
            r = requests.get(f'https://{portal_url}/api/3/action/package_search',
                             params={'q': query, 'rows': limit}, timeout=20)
            if r.status_code != 200:
                logger.warning(f'[CKAN] Search {portal_url} returned {r.status_code}')
                return []
            data = r.json()
            results = []
            for pkg in data.get('result', {}).get('results', []):
                # Find the first datastore resource
                resource_id = ''
                for res in pkg.get('resources', []):
                    if res.get('datastore_active') or res.get('format', '').upper() in ('CSV', 'JSON'):
                        resource_id = res.get('id', '')
                        break
                if not resource_id and pkg.get('resources'):
                    resource_id = pkg['resources'][0].get('id', '')

                results.append({
                    'id': resource_id or pkg.get('id', ''),
                    'name': pkg.get('title', pkg.get('name', '')),
                    'description': (pkg.get('notes', '') or '')[:500],
                    'type': 'dataset',
                    'row_count': 0,
                    '_package_id': pkg.get('id', ''),
                    '_organization': pkg.get('organization', {}).get('title', ''),
                })
            return results
        except Exception as e:
            logger.error(f'[CKAN] Catalog error: {e}')
            return []

    def get_metadata(self, portal_url, dataset_id):
        """Try datastore_search to get field info."""
        try:
            r = requests.get(f'https://{portal_url}/api/3/action/datastore_search',
                             params={'resource_id': dataset_id, 'limit': 0}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                result = data.get('result', {})
                columns = []
                for field in result.get('fields', []):
                    if field.get('id', '').startswith('_'):
                        continue
                    columns.append({
                        'fieldName': field.get('id', ''),
                        'name': field.get('id', ''),
                        'dataTypeName': field.get('type', ''),
                    })
                return {
                    'columns': columns,
                    'name': '',
                    'description': '',
                    'total': result.get('total', 0),
                }
        except Exception as e:
            logger.error(f'[CKAN] Metadata error for {dataset_id}: {e}')
        return {'columns': []}

    def get_sample_records(self, portal_url, dataset_id, limit=5):
        try:
            r = requests.get(f'https://{portal_url}/api/3/action/datastore_search',
                             params={'resource_id': dataset_id, 'limit': limit}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                return data.get('result', {}).get('records', [])
        except Exception as e:
            logger.error(f'[CKAN] Sample fetch error for {dataset_id}: {e}')
        return []

    def build_api_url(self, portal_url, dataset_id):
        return f'https://{portal_url}/api/3/action/datastore_search?resource_id={dataset_id}'
