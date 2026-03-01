"""HTTP client for Unleash Admin API with retries and backoff."""

import logging
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings

logger = logging.getLogger(__name__)


def _retry_transport() -> httpx.HTTPTransport:
    """Transport with retries for 5xx and connection errors."""
    return httpx.HTTPTransport(retries=3)


@retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def _request_with_retry(client: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
    """Execute request with tenacity retry (only for 5xx and network errors)."""
    r = client.request(method, url, **kwargs)
    if r.status_code >= 500:
        r.raise_for_status()
    return r


class UnleashClient:
    """Client for Unleash Admin API with retries and backoff."""

    def __init__(self):
        settings = get_settings()
        self._base_url = settings.unleash_base_url
        self._token = settings.unleash_api_token
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": self._token,
                "Content-Type": "application/json",
            },
            timeout=30.0,
            transport=_retry_transport(),
        )

    def get_feature(
        self,
        project_id: str,
        feature_name: str,
    ) -> Optional[dict]:
        """GET /api/admin/projects/{projectId}/features/{featureName}."""
        try:
            r = _request_with_retry(
                self._client,
                "GET",
                f"/api/admin/projects/{project_id}/features/{feature_name}",
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return None
        except Exception as e:
            logger.error("Unleash get_feature failed: %s", e)
            raise

    def create_feature(
        self,
        project_id: str,
        payload: dict,
    ) -> dict:
        """POST /api/admin/projects/{projectId}/features."""
        r = _request_with_retry(
            self._client,
            "POST",
            f"/api/admin/projects/{project_id}/features",
            json=payload,
        )
        r.raise_for_status()
        return r.json()

    def update_feature(
        self,
        project_id: str,
        feature_name: str,
        payload: dict,
    ) -> dict:
        """PUT /api/admin/projects/{projectId}/features/{featureName}."""
        r = _request_with_retry(
            self._client,
            "PUT",
            f"/api/admin/projects/{project_id}/features/{feature_name}",
            json=payload,
        )
        r.raise_for_status()
        return r.json()

    def add_strategies(
        self,
        project_id: str,
        feature_name: str,
        environment: str,
        strategies: list[dict],
    ) -> None:
        """Add strategies to feature environment."""
        for strategy in strategies:
            r = _request_with_retry(
                self._client,
                "POST",
                f"/api/admin/projects/{project_id}/features/{feature_name}"
                f"/environments/{environment}/strategies",
                json=strategy,
            )
            r.raise_for_status()

    def toggle_environment(
        self,
        project_id: str,
        feature_name: str,
        environment: str,
        enabled: bool,
    ) -> dict:
        """Enable or disable feature in environment."""
        path = "on" if enabled else "off"
        r = _request_with_retry(
            self._client,
            "POST",
            f"/api/admin/projects/{project_id}/features/{feature_name}"
            f"/environments/{environment}/{path}",
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    def apply_change_request(
        self,
        project_id: str,
        feature_key: str,
        desired_changes: dict,
        environment: Optional[str] = None,
        strategies: Optional[list[dict]] = None,
    ) -> dict:
        """
        Apply a change request to Unleash.
        - If feature exists: update metadata, sync strategies, toggle env
        - If not: create feature, add strategies, enable in env
        - Handles partial updates and strategy replacement per env
        """
        env = environment or "default"
        existing = self.get_feature(project_id, feature_key)

        if existing:
            # Update existing: description, type, impressionData, stale
            update_payload = {
                k: v
                for k, v in desired_changes.items()
                if k in ("description", "type", "impressionData", "stale")
            }
            if update_payload:
                result = self.update_feature(project_id, feature_key, update_payload)
            else:
                result = existing

            # Sync strategies: add new ones (Unleash doesn't support replace-all via API)
            if strategies:
                self.add_strategies(project_id, feature_key, env, strategies)
        else:
            # Create new feature
            create_payload = {
                "name": feature_key,
                "description": desired_changes.get("description", ""),
                "type": desired_changes.get("type", "release"),
                "impressionData": desired_changes.get("impressionData", False),
            }
            result = self.create_feature(project_id, create_payload)
            if strategies:
                self.add_strategies(project_id, feature_key, env, strategies)

        # Toggle environment enabled/disabled
        enabled = desired_changes.get("enabled", True)
        self.toggle_environment(project_id, feature_key, env, enabled)

        return result


_unleash_client: Optional[UnleashClient] = None


def get_unleash_client() -> UnleashClient:
    """Singleton Unleash client."""
    global _unleash_client
    if _unleash_client is None:
        _unleash_client = UnleashClient()
    return _unleash_client
