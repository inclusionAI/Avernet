"""Local SecretResolver.

The remote secret backend is unreachable in local/singlebox mode. Most secrets
are unavailable, but the aiworkbench repo URL is configured locally so
GitSyncService can reuse the same SecretResolver contract as corp.

Deliberately **not** here: the gateway principal signing key. It would only ever
be a singlebox-shaped stand-in for a secret store, shipped empty so it did
nothing, and every deployment that actually serves ``/openapi/v1`` resolves it
from a real store or the environment instead. Singlebox therefore has no key and
answers 401 on the public surface — the state it has always been in. Giving it
one is a deliberate change, not a config line.
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
        — they would set a value in the active config and see no effect.

        This names the overlay for log messages. The *values* come from
        :meth:`_read_singlebox_local_config`, which applies the same search
        order **and** the base-under-overlay deep merge the provider performs.

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
        logger.info("[LocalMock] SecretResolver.get_secret(%s) -> None", secret_name)
        return None

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

        Mirrors ``YamlConfigProvider._load_yaml_configs`` in full, which means
        **deep-merging ``application.yaml`` under the overlay** rather than
        reading the overlay alone. The overlay only carries what singlebox
        *changes*; anything the deployment leaves in the base is still part of
        the effective config. Reading one file would resolve to ``None`` for a
        value the app itself booted with — e.g. an ``openclaw.skills_repo_url``
        defined in the base and not repeated in the overlay, which would send
        GitSyncService to its on-disk fallback for no reason.

        An unreadable or malformed config is not fatal here: every caller treats
        a missing value as "this secret is absent", which is the Protocol's
        ``None`` outcome.
        """
        from agentclaw.community.core.config.yaml_provider import _deep_merge

        for config_dir in (
            Path.cwd() / "configs",
            self._SINGLEBOX_CONFIG_PATH.parent,
        ):
            base = config_dir / "application.yaml"
            overlay = config_dir / self._SINGLEBOX_CONFIG_PATH.name
            if base.exists() and overlay.exists():
                merged = _deep_merge(self._load_yaml(base), self._load_yaml(overlay))
                return merged.get("user_config") or {}

        # No directory holds the pair — read the overlay alone. Keeps a test
        # that points _SINGLEBOX_CONFIG_PATH at a lone fixture working.
        return self._load_yaml(self._SINGLEBOX_CONFIG_PATH).get("user_config") or {}

    def _load_yaml(self, path: Path) -> dict:
        """Parse one yaml file, or ``{}`` if it cannot be read."""
        try:
            import yaml

            with path.open(encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning(
                "[LocalMock] failed to read singlebox config %s: %s", path, exc
            )
            return {}
