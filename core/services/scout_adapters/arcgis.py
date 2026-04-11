import logging
import requests
from .base import BaseScoutAdapter

logger = logging.getLogger('agents')


class ArcGISAdapter(BaseScoutAdapter):
    """Adapter for ArcGIS REST Services portals."""

    name = "arcgis"

    def search_catalog(self, portal_url, query, limit=30):
        """List feature services and filter by query keyword."""
        results = []
        try:
            r = requests.get(f'https://{portal_url}/rest/services',
                             params={'f': 'json'}, timeout=20)
            if r.status_code != 200:
                logger.warning(f'[ArcGIS] Services list {portal_url} returned {r.status_code}')
                return []
            data = r.json()
            services = data.get('services', [])
            query_lower = query.lower()

            count = 0
            for svc in services:
                svc_name = svc.get('name', '')
                svc_type = svc.get('type', '')
                if svc_type not in ('FeatureServer', 'MapServer'):
                    continue
                if query_lower and query_lower not in svc_name.lower():
                    continue
                results.append({
                    'id': svc_name,
                    'name': svc_name.split('/')[-1].replace('_', ' '),
                    'description': '',
                    'type': svc_type,
                    'row_count': 0,
                })
                count += 1
                if count >= limit:
                    break
        except Exception as e:
            logger.error(f'[ArcGIS] Catalog error: {e}')
        return results

    def get_metadata(self, portal_url, dataset_id):
        """Get layer 0 metadata from a FeatureServer."""
        try:
            r = requests.get(f'https://{portal_url}/rest/services/{dataset_id}/FeatureServer/0',
                             params={'f': 'json'}, timeout=15)
            if r.status_code != 200:
                return {'columns': []}
            data = r.json()
            columns = []
            for field in data.get('fields', []):
                columns.append({
                    'fieldName': field.get('name', ''),
                    'name': field.get('alias', field.get('name', '')),
                    'dataTypeName': field.get('type', ''),
                })
            return {
                'columns': columns,
                'name': data.get('name', ''),
                'description': data.get('description', ''),
            }
        except Exception as e:
            logger.error(f'[ArcGIS] Metadata error for {dataset_id}: {e}')
            return {'columns': []}

    def get_sample_records(self, portal_url, dataset_id, limit=5):
        try:
            r = requests.get(
                f'https://{portal_url}/rest/services/{dataset_id}/FeatureServer/0/query',
                params={'where': '1=1', 'outFields': '*', 'resultRecordCount': limit, 'f': 'json'},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                return [f.get('attributes', {}) for f in data.get('features', [])]
        except Exception as e:
            logger.error(f'[ArcGIS] Sample fetch error for {dataset_id}: {e}')
        return []

    def build_api_url(self, portal_url, dataset_id):
        return f'https://{portal_url}/rest/services/{dataset_id}/FeatureServer/0/query'
