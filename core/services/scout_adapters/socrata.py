import logging
import requests
from .base import BaseScoutAdapter

logger = logging.getLogger('agents')


class SocrataAdapter(BaseScoutAdapter):
    """Adapter for Socrata-powered open data portals."""

    name = "socrata"

    def search_catalog(self, portal_url, query, limit=30):
        try:
            r = requests.get(f'https://{portal_url}/api/catalog/v1',
                             params={'q': query, 'limit': limit}, timeout=20)
            if r.status_code != 200:
                logger.warning(f'[Socrata] Catalog {portal_url} returned {r.status_code}')
                return []
            results = []
            for item in r.json().get('results', []):
                res = item.get('resource', {})
                rtype = res.get('type', '')
                if rtype not in ('dataset', ''):
                    continue
                results.append({
                    'id': res.get('id', ''),
                    'name': res.get('name', ''),
                    'description': (res.get('description', '') or '')[:500],
                    'type': rtype,
                    'row_count': res.get('page_views', {}).get('page_views_total', 0),
                })
            return results
        except Exception as e:
            logger.error(f'[Socrata] Catalog search error: {e}')
            return []

    def get_metadata(self, portal_url, dataset_id):
        try:
            r = requests.get(f'https://{portal_url}/api/views/{dataset_id}.json', timeout=15)
            if r.status_code != 200:
                return {'columns': []}
            data = r.json()
            return {
                'columns': data.get('columns', []),
                'name': data.get('name', ''),
                'description': data.get('description', ''),
            }
        except Exception as e:
            logger.error(f'[Socrata] Metadata error for {dataset_id}: {e}')
            return {'columns': []}

    def get_sample_records(self, portal_url, dataset_id, limit=5):
        try:
            r = requests.get(f'https://{portal_url}/resource/{dataset_id}.json',
                             params={'$limit': limit}, timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logger.error(f'[Socrata] Sample fetch error for {dataset_id}: {e}')
        return []

    def build_api_url(self, portal_url, dataset_id):
        return f'https://{portal_url}/resource/{dataset_id}.json'
