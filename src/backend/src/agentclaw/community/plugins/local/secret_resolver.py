"""Local SecretResolver.

The remote secret backend is unreachable in local/singlebox mode. Most secrets
are unavailable, but a couple are configured locally so their consumers can reuse
the same SecretResolver contract as corp:

- the aiworkbench repo URL, for GitSyncService;
- the gateway principal signing key, for the ``/openapi/v1`` verifier.

Both are read from ``application-singlebox.yaml``. Neither ships with a value:
this file resolves what the operator put there, and returns ``None`` when they
put nothing — a committed shared secret would be a committed credential.
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
_PRINCIPAL_SIGNING_KEY_SECRET_NAME = "gateway_principal_signing_key"


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
    # This is the *bundled* copy — see ``_active_singlebox_config_path``, which
    # prefers a deployed runtime overlay over it.
    _SINGLEBOX_CONFIG_PATH = (
        Path(__file__).resolve().parents[2] / "configs" / "application-singlebox.yaml"
    )

    def _active_singlebox_config_path(self) -> Path:
        """The overlay the app actually booted from.

        ``YamlConfigProvider._load_yaml_configs`` searches ``cwd/configs`` before
        the bundled subtree, and takes the first directory holding **both**
        ``application.yaml`` and the overlay. A deployed singlebox assembles its
        runtime ``configs/`` in the working directory, so reading only the
        bundled copy would resolve secrets from a file the operator never edits
        — they would set ``gateway_principal.signing_key`` in the active config
        and still get 401 everywhere. Mirror that search order exactly, pairing
        rule included, so both reads land on the same file.

        Falls back to the bundled path when no directory holds the pair, which
        keeps a test that points ``_SINGLEBOX_CONFIG_PATH`` at a lone fixture
        working.
        """
        for config_dir in (
            Path.cwd() / "configs",
            self._SINGLEBOX_CONFIG_PATH.parent,
        ):
            overlay = config_dir / self._SINGLEBOX_CONFIG_PATH.name
            if (config_dir / "application.yaml").exists() and overlay.exists():
                return overlay
        return self._SINGLEBOX_CONFIG_PATH

    def get_secret(self, secret_name: str) -> Any | None:
        if secret_name == _AIWORKBENCH_REPO_URL_SECRET_NAME:
            return self._get_aiworkbench_repo_url_secret()
        if secret_name == _PRINCIPAL_SIGNING_KEY_SECRET_NAME:
            return self._get_principal_signing_key_secret()

        logger.info("[LocalMock] SecretResolver.get_secret(%s) -> None", secret_name)
        return None

    def _get_principal_signing_key_secret(self) -> _LocalSecret | None:
        """Resolve the HMAC key singlebox shares with the gateway.

        Ships unset: ``application-singlebox.yaml`` carries the block but no
        value, so out of the box this returns ``None`` and every ``/openapi/v1``
        request answers 401 — the same state singlebox is in today. Set it there
        *and* in the gateway's ``AVERNET_PRINCIPAL_SIGNING_KEY`` to exercise the
        public surface locally; the two must match for a token to verify.
        """
        local_config = self._read_singlebox_local_config()
        principal_config = local_config.get("gateway_principal") or {}
        key = principal_config.get("signing_key")
        if not isinstance(key, str) or not key.strip():
            logger.info(
                "[LocalMock] no gateway_principal.signing_key in %s — the "
                "public API will answer 401",
                self._active_singlebox_config_path(),
            )
            return None

        # The name only — never the key itself.
        logger.info(
            "[LocalMock] SecretResolver.get_secret(%s) -> "
            "application-singlebox.yaml",
            _PRINCIPAL_SIGNING_KEY_SECRET_NAME,
        )
        return _LocalSecret(
            secret_user=_PRINCIPAL_SIGNING_KEY_SECRET_NAME,
            secret_value=key.strip(),
        )

    def _get_aiworkbench_repo_url_secret(self) -> _LocalSecret | None:
        repo_url = self._read_singlebox_skills_repo_url()
        if not repo_url:
            logger.warning(
                "[LocalMock] aiworkbench repo URL missing in %s",
                self._active_singlebox_config_path(),
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
        openclaw_config = self._read_singlebox_local_config().get("openclaw") or {}
        repo_url = openclaw_config.get("skills_repo_url")
        if isinstance(repo_url, str) and repo_url.strip():
            return repo_url.strip()
        return None

    def _read_singlebox_local_config(self) -> dict:
        """Return the ``user_config`` block, or ``{}`` if it cannot be read.

        An unreadable or malformed config is not fatal here: every caller treats
        a missing value as "this secret is absent", which is the Protocol's
        ``None`` outcome.
        """
        config_path = self._active_singlebox_config_path()
        try:
            import yaml

            with config_path.open(encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning(
                "[LocalMock] failed to read singlebox config %s: %s",
                config_path,
                exc,
            )
            return {}

        return config.get("user_config") or {}
