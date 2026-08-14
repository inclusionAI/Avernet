"""``TeclawFilePromotion`` — snapshot a running teclaw bot's files into OSS at a
publish stage, as artifact refs.

A running teclaw container owns its live files (the backend keeps no mirror of
them; see the per-file write migration). So at a promotion boundary (draft→verify,
verify→publish) the backend must **read the source container's files from the
engine**, write each to a **stage-scoped OSS key**, and return ``{store, path}``
refs to embed in the composed :class:`BotConfigArtifact` for the new stage.

OSS key layout (stage-scoped so each snapshot is isolated — draft and verify do
not share objects)::

    teclaw/{env}/bolt_data/{entity_type}_{entity_id}/{bot_id}_{publish_id}_{stage}/teclaw/{ns}/{rel}
    └────────── store base "bot-data" ──────────┘└──────────────── ref path ────────────────────┘

where ``ns`` ∈ {``workspace``, ``identity``} and ``rel`` is the file's path
relative to that namespace. The live container's engine-relative paths
(``/workspace/...`` · ``/identity/...``) are unaffected — only the OSS storage
location / artifact ref carries the stage-scoped prefix.

Reads go through the source bot's :class:`DeviceFileSystem` using
namespace-relative logical paths (``"workspace"`` / ``"identity"`` for listing,
``"workspace/<rel>"`` for reading), which the teclaw mapper turns into the
engine-relative ``/workspace`` · ``/identity`` form.

Regenerable dependency/build directories (``node_modules``) are excluded from the
sweep — see ``_EXCLUDED_DIR_SEGMENTS``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agentclaw.community.log import get_logger
from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin

logger = get_logger()

# The two engine namespaces a teclaw bot owns; workspace → artifact resources,
# identity → artifact identity_files.
WORKSPACE_NS = "workspace"
IDENTITY_NS = "identity"
_NAMESPACES = (WORKSPACE_NS, IDENTITY_NS)

# The composer store id whose base is ``teclaw/{env}/bolt_data``.
_BOT_DATA_STORE = "bot-data"

# Regenerable dependency/build directories that must never be part of a content
# snapshot. They are large, symlink-heavy (symlinks cannot round-trip through
# per-file OSS staging), and reinstalled at container build — they are not user
# content to carry across publish stages. Sweeping into them also makes promotion
# fragile: ``node_modules`` routinely holds entries (e.g. ``*.js.map`` files and
# package symlinks) that ``list_dir`` reports but the engine cannot ``read_file``,
# which the source-of-truth contract below turns into a hard build failure.
# Skipping them by path segment (at any depth) avoids both problems.
_EXCLUDED_DIR_SEGMENTS = frozenset({"node_modules"})


def _has_excluded_segment(engine_path: str) -> bool:
    """True if any path segment of ``engine_path`` names an excluded directory."""
    return not _EXCLUDED_DIR_SEGMENTS.isdisjoint(engine_path.split("/"))


class TeclawFilePromotionError(Exception):
    """A source file could not be read, or its bytes could not be staged to OSS."""


@dataclass
class PromotedRefs:
    """Staged file refs for a publish stage, split by artifact section.

    Each ref is a serialized ``{name, store, path}`` dict (the shape the composed
    artifact's ``resources`` / ``identity_files`` entries already use), ready to
    merge into ``config_artifact``.
    """

    resources: list[dict[str, str]] = field(default_factory=list)
    identity_files: list[dict[str, str]] = field(default_factory=list)


class TeclawFilePromotion:
    """Gather a teclaw bot's live files from the engine and stage them to OSS."""

    NAMESPACES = _NAMESPACES

    def __init__(self, *, oss_storage: ObjectStoragePlugin) -> None:
        self._oss = oss_storage

    async def stage_files(
        self,
        *,
        device_fs: DeviceFileSystem,
        env: str,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        publish_id: int,
        stage: str,
    ) -> PromotedRefs:
        """Read the source bot's ``/workspace`` + ``/identity`` files via
        ``device_fs`` and write each to a stage-scoped OSS key; return the refs.

        Raises:
            TeclawFilePromotionError: a listed file could not be read (the source
                container is the source of truth, so a missing read is a hard
                failure, not a silent skip), or its OSS put failed.
        """
        store_base = f"teclaw/{env}/bolt_data"
        # Stage-scoped per-bot segment isolates each publish snapshot.
        rel_root = f"{entity_type}_{entity_id}/{bot_id}_{publish_id}_{stage}/teclaw"
        out = PromotedRefs()

        for ns in self.NAMESPACES:
            entries = await device_fs.list_dir(ns, recursive=True)
            # ``list_dir`` returns None on a failed listing (engine unreachable /
            # 5xx) and [] for a genuinely empty namespace. They must NOT be
            # conflated: a swallowed listing error would ship an artifact missing
            # files (there is no DB mirror to fall back on). Treat None as a hard
            # failure; [] is a legitimate empty namespace.
            if entries is None:
                raise TeclawFilePromotionError(
                    f"teclaw promotion: list_dir failed for namespace {ns!r} "
                    "(engine unreachable/error) — refusing to ship an incomplete snapshot"
                )
            for entry in entries:
                if entry.get("is_dir"):
                    continue
                engine_path = entry.get("path") or ""  # e.g. "/workspace/sub/x.csv"
                if not engine_path:
                    continue
                # Defensive: an engine listing for ``ns`` must only return paths
                # under ``/ns``. A stray cross-namespace entry would garble the
                # rel/key below (and read the wrong file), so skip it — it will be
                # captured under its own namespace's listing.
                if engine_path != f"/{ns}" and not engine_path.startswith(f"/{ns}/"):
                    logger.warning(
                        "[TeclawFilePromotion] listing for ns=%s returned "
                        "out-of-namespace path %r; skipping", ns, engine_path,
                    )
                    continue
                # Regenerable dependency/build dirs (``node_modules``) are not
                # content and their symlinked entries (``*.js.map`` etc.) list but
                # do not read — skip them so promotion neither bloats OSS nor
                # hard-fails on an unreadable non-content file. No per-file log: a
                # node_modules tree can hold tens of thousands of entries; the
                # end-of-sweep summary covers what was actually staged.
                if _has_excluded_segment(engine_path):
                    continue
                logical = engine_path.lstrip("/")  # "workspace/sub/x.csv"
                content = await device_fs.read_file(logical)
                if content is None:
                    raise TeclawFilePromotionError(
                        f"teclaw promotion: source file unreadable: {engine_path}"
                    )
                rel = logical[len(ns):].lstrip("/")  # "sub/x.csv"
                ref_path = f"{rel_root}/{ns}/{rel}"
                oss_key = f"{store_base}/{ref_path}"
                ok = await asyncio.to_thread(self._oss.put_object, oss_key, content)
                if not ok:
                    raise TeclawFilePromotionError(
                        f"teclaw promotion: OSS put_object failed for {oss_key!r}"
                    )
                ref = {
                    "name": rel.rsplit("/", 1)[-1],
                    "store": _BOT_DATA_STORE,
                    "path": ref_path,
                }
                target = out.resources if ns == WORKSPACE_NS else out.identity_files
                target.append(ref)

        logger.info(
            "[TeclawFilePromotion] staged bot=%s publish_id=%s stage=%s: "
            "%d resource(s), %d identity file(s)",
            bot_id, publish_id, stage, len(out.resources), len(out.identity_files),
        )
        return out


__all__ = ["TeclawFilePromotion", "TeclawFilePromotionError", "PromotedRefs"]
