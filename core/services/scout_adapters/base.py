from abc import ABC, abstractmethod


class BaseScoutAdapter(ABC):
    """Base adapter for dataset discovery on open data portals."""

    name = "base"

    @abstractmethod
    def search_catalog(self, portal_url: str, query: str, limit: int = 30) -> list:
        """Search catalog. Return list of dicts with keys: id, name, description, type, row_count."""
        pass

    @abstractmethod
    def get_metadata(self, portal_url: str, dataset_id: str) -> dict:
        """Get metadata. Return dict with key 'columns': list of {fieldName, name, dataTypeName}."""
        pass

    @abstractmethod
    def get_sample_records(self, portal_url: str, dataset_id: str, limit: int = 5) -> list:
        """Fetch sample records. Return list of dicts."""
        pass

    def build_api_url(self, portal_url: str, dataset_id: str) -> str:
        """Build the scraping API URL."""
        return f"https://{portal_url}/resource/{dataset_id}"
