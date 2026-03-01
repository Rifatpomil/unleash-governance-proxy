"""Authorization - OpenFGA or local policy fallback with hot-reload."""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

from app.config import get_settings

logger = logging.getLogger(__name__)


class AuthorizationService:
    """Check if user can edit a feature. Uses OpenFGA or local allowlist."""

    def __init__(self):
        self._openfga_client = None
        self._local_policy: Optional[dict] = None
        self._policy_mtime: Optional[float] = None
        self._init_openfga()
        self._load_local_policy()

    def _policy_path(self) -> Path:
        return Path(get_settings().policy_file_path)

    def _init_openfga(self) -> None:
        """Initialize OpenFGA client if configured (uses HTTP API for sync)."""
        settings = get_settings()
        if settings.openfga_api_url and settings.openfga_store_id:
            logger.info("OpenFGA configured (URL: %s)", settings.openfga_api_url)
        else:
            logger.info("OpenFGA not configured, using local policy")

    def _load_local_policy(self) -> None:
        """Load local allowlist policy from YAML file."""
        path = self._policy_path()
        if path.exists():
            try:
                with open(path) as f:
                    self._local_policy = yaml.safe_load(f) or {}
                self._policy_mtime = os.path.getmtime(path)
            except Exception as e:
                logger.warning("Failed to load policy file: %s", e)
                self._local_policy = {}
        else:
            self._local_policy = {"allowlist": [], "allow_all": False}

    def _reload_if_changed(self) -> None:
        """Hot-reload policy if file mtime changed."""
        path = self._policy_path()
        if path.exists():
            try:
                mtime = os.path.getmtime(path)
                if self._policy_mtime is None or mtime > self._policy_mtime:
                    self._load_local_policy()
                    logger.info("Policy reloaded from %s", path)
            except OSError:
                pass

    def can_edit_feature(
        self,
        user_id: str,
        tenant: Optional[str],
        feature_key: str,
    ) -> bool:
        """
        Check if user can edit the feature.
        Uses OpenFGA when available, else local allowlist.
        Hot-reloads local policy if file changed.
        """
        self._reload_if_changed()
        settings = get_settings()
        if settings.openfga_api_url and settings.openfga_store_id:
            return self._check_openfga(user_id, tenant, feature_key)
        return self._check_local_policy(user_id, tenant, feature_key)

    def _check_openfga(
        self,
        user_id: str,
        tenant: Optional[str],
        feature_key: str,
    ) -> bool:
        """Check via OpenFGA: can_edit_feature(user, tenant, featureKey)."""
        settings = get_settings()
        if not settings.openfga_api_url or not settings.openfga_store_id:
            return self._check_local_policy(user_id, tenant, feature_key)
        try:
            import httpx

            obj = f"feature:{tenant or 'default'}:{feature_key}"
            user = f"user:{user_id}"
            relation = "can_edit"

            url = f"{settings.openfga_api_url.rstrip('/')}/stores/{settings.openfga_store_id}/check"
            body = {
                "tuple_key": {
                    "user": user,
                    "relation": relation,
                    "object": obj,
                },
            }
            if settings.openfga_model_id:
                body["authorization_model_id"] = settings.openfga_model_id

            with httpx.Client(timeout=5.0) as client:
                r = client.post(url, json=body)
                r.raise_for_status()
                data = r.json()
                return data.get("allowed", False)
        except Exception as e:
            logger.warning("OpenFGA check failed: %s", e)
            return self._check_local_policy(user_id, tenant, feature_key)

    def _check_local_policy(
        self,
        user_id: str,
        tenant: Optional[str],
        feature_key: str,
    ) -> bool:
        """
        Local allowlist: allowlist is list of {user, tenant?, feature?}.
        If allow_all: true, everyone can edit.
        """
        policy = self._local_policy or {}
        if policy.get("allow_all"):
            return True

        allowlist = policy.get("allowlist", [])
        for entry in allowlist:
            user_match = entry.get("user") == user_id or entry.get("user") == "*"
            tenant_match = (
                entry.get("tenant") is None
                or entry.get("tenant") == tenant
                or entry.get("tenant") == "*"
            )
            feature_match = (
                entry.get("feature") is None
                or entry.get("feature") == feature_key
                or entry.get("feature") == "*"
            )
            if user_match and tenant_match and feature_match:
                return True
        return False


_authz_service: Optional[AuthorizationService] = None


def get_authorization_service() -> AuthorizationService:
    """Singleton authorization service."""
    global _authz_service
    if _authz_service is None:
        _authz_service = AuthorizationService()
    return _authz_service
