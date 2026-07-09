"""Local SecretResolver.

The remote secret backend is unreachable in local/singlebox mode. Most secrets are unavailable, but
the aiworkbench repo URL is configured locally so GitSyncService can reuse the
same SecretResolver contract as corp.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam

logger = get_logger()

_AIWORKBENCH_REPO_URL_SECRET_NAME = "other_manual_agentclaw_aiworkbench_repo_url"


@dataclass(frozen=True)
class _LocalSecret:
    secret_user: str
    secret_value: str


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="local config fallback",
)
class LocalSecretResolver(MockSeam, SecretResolver):
    """Local resolver backed by application-singlebox.yaml for known secrets."""

    # B11: application-singlebox.yaml lives in the community subtree
    # (agentclaw/community/configs). parents[2] is agentclaw/community from this file
    # (plugins/local/secret_resolver.py). Was parents[4] — off by two since the
    # community/ subtree was introduced, so this had been silently missing the file.
    _SINGLEBOX_CONFIG_PATH = (
        Path(__file__).resolve().parents[2] / "configs" / "application-singlebox.yaml"
    )

    def get_secret(self, secret_name: str) -> Any | None:
        if secret_name == _AIWORKBENCH_REPO_URL_SECRET_NAME:
            return self._get_aiworkbench_repo_url_secret()

        logger.info("[LocalMock] SecretResolver.get_secret(%s) -> None", secret_name)
        return None

    def _get_aiworkbench_repo_url_secret(self) -> _LocalSecret | None:
        repo_url = self._read_singlebox_skills_repo_url()
        if not repo_url:
            logger.warning(
                "[LocalMock] aiworkbench repo URL missing in %s",
                self._SINGLEBOX_CONFIG_PATH,
            )
            return None

        logger.info(
            "[LocalMock] SecretResolver.get_secret(%s) -> application-singlebox.yaml",
            _AIWORKBENCH_REPO_URL_SECRET_NAME,
        )
        return _LocalSecret(
            secret_user="aiworkbench_repo_url",
            secret_value=repo_url,
        )

    def _read_singlebox_skills_repo_url(self) -> str | None:
        try:
            import yaml

            with self._SINGLEBOX_CONFIG_PATH.open(encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning(
                "[LocalMock] failed to read singlebox config %s: %s",
                self._SINGLEBOX_CONFIG_PATH,
                exc,
            )
            return None

        local_config = config.get("user_config") or {}
        openclaw_config = local_config.get("openclaw") or {}
        repo_url = openclaw_config.get("skills_repo_url")
        if isinstance(repo_url, str) and repo_url.strip():
            return repo_url.strip()
        return None
