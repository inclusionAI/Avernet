"""Fakes for the apply engine's tests.

Each one **counts its calls**, because most of what these tests assert is the
*absence* of a write: convergence is "applying an unchanged document performs no
write", and all-or-nothing is "a category that could not be materialised wrote
nothing". Equal-looking output would prove neither.
"""

from __future__ import annotations


from typing import Any

from agentclaw.community.core.ports.resource_rows_port import WrittenFile
from agentclaw.community.core.ports.resource_file_port import WorkspaceEntry


class FakeResourceRows:
    """``ResourceRowsPort`` for one bot, recording every replacement; ``fail`` makes the write raise."""

    def __init__(self, fail: bool = False) -> None:
        self.replacements: list[dict[str, Any]] = []
        self.fail = fail

    async def replace_rows(self, *, files, owner_id, created_by):
        if self.fail:
            raise RuntimeError("rows unavailable")
        self.replacements.append(
            {"files": list(files), "owner_id": owner_id, "created_by": created_by}
        )

    @property
    def rows(self) -> list[WrittenFile]:
        """The rows as the last replacement left them."""
        return list(self.replacements[-1]["files"]) if self.replacements else []


def resource_rows_for(rows: FakeResourceRows | None = None):
    """A ``resource_rows`` factory answering one recorder for every bot; returns ``(factory, recorder)``."""
    recorder = rows or FakeResourceRows()
    return (lambda bot_id: recorder), recorder


class FakeResourceFileService:
    """Stands in for ``ResourceFileService``: a presence set, with every call recorded.

    ``ResourceFileService`` is v1's single write chain for manifest resources
    (its dispatcher covers the arca / baas / teclaw transports uniformly), so
    the fake needs only the apply-side entry points: ``list_entries`` for the
    plan stage's one recursive listing and the clear's root listing,
    ``delete_dir`` / ``delete`` and ``upload_file`` for the write stage, plus
    ``exists`` for the other materialisers and the older contract checks. Signatures mirror the port's apply-side
    surface (every parameter the materialiser passes; the router-only
    extras such as ``preserve_structure`` are deliberately absent), so a
    drift shows up as a TypeError in these tests before it shows up
    mid-apply in production.

    Presence is the workspace as the bot would see it: ``record_present``
    seeds it, uploads add to it, ``delete_dir`` and ``delete`` remove a
    subtree or a path. ``fail_clear`` makes every delete answer the
    transports' silent ``False`` with presence untouched — the real device
    filesystems catch their own errors and return ``False`` rather than
    raise — which is what the materialiser's reading of a ``False`` on an
    entry the listing just showed is tested with; a path in ``fail_deletes``
    does the same for one path. ``clear_calls`` records each listing of the
    root the clear starts with, ``listing_calls`` each recursive listing the
    plan makes, both by their addressing.
    """

    def __init__(
        self,
        exists_paths: set[str] | None = None,
        fail_deletes: set[str] | None = None,
    ) -> None:
        self.writes: dict[tuple[str, str], bytes] = {}
        self.deleted: list[str] = []
        # Full addressing of every call, so a test can pin *how* the write
        # stage addressed the workspace — same rationale as exists_probes.
        self.upload_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.exists_probes: list[dict[str, Any]] = []
        self._exists = set(exists_paths or ())
        self._fail_deletes = set(fail_deletes or ())
        self.list_calls: list[dict[str, Any]] = []
        self.listing_calls: list[dict[str, Any]] = []
        self.clear_calls: list[dict[str, Any]] = []
        self.delete_dir_calls: list[dict[str, Any]] = []
        #: When set, every delete answers ``False`` and removes nothing — the
        #: transports' one failure signal.
        self.fail_clear = False
        #: When set, ``list_entries`` raises — the port's answer for a listing it could not make.
        self.fail_list = False

    @property
    def present(self) -> frozenset[str]:
        """Every path the fake currently holds — the workspace as the bot would see it."""
        return frozenset(self._exists)

    def record_present(self, *paths: str) -> None:
        self._exists.update(paths)

    async def upload_file(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        target_dir: str,
        filename: str,
        data: bytes,
    ) -> dict[str, Any]:
        self.upload_calls.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "bot_id": bot_id,
                "engine_type": engine_type,
                "target_dir": target_dir,
                "filename": filename,
            }
        )
        self.writes[(target_dir, filename)] = data
        self._exists.add(f"{target_dir}/{filename}".replace("//", "/"))
        return {"path": f"{target_dir}/{filename}"}

    async def delete(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> bool:
        self.delete_calls.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "bot_id": bot_id,
                "engine_type": engine_type,
                "path": path,
            }
        )
        if self.fail_clear or path in self._fail_deletes:
            # Silent refusal: the transport answered, nothing was removed,
            # presence still reports the tree — an exists re-probe sees it.
            return False
        self.deleted.append(path)
        self._exists = {
            p for p in self._exists if p != path and not p.startswith(f"{path}/")
        }
        return True

    async def exists(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
        publish_id: str | None = None,
        device_uuid: str | None = None,
    ) -> bool:
        # Recorded so a test can pin *how* the plan stage addressed the
        # workspace — the entity half must be the owner, the router's own
        # address, not the manifest's storage key.
        self.exists_probes.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "bot_id": bot_id,
                "engine_type": engine_type,
                "path": path,
            }
        )
        return path in self._exists

    async def list_entries(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        prefix: str = "",
        recursive: bool = False,
        publish_id: str | None = None,
        device_uuid: str | None = None,
    ) -> tuple[WorkspaceEntry, ...]:
        coords = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "bot_id": bot_id,
            "engine_type": engine_type,
        }
        self.list_calls.append({**coords, "prefix": prefix, "recursive": recursive})
        if recursive:
            self.listing_calls.append(coords)
        elif not prefix:
            self.clear_calls.append(coords)
        if self.fail_list:
            raise RuntimeError("listing quoted a header with a token")
        base = f"{prefix}/" if prefix else ""
        entries: dict[str, bool] = {}
        for present in sorted(self._exists):
            if base and not present.startswith(base):
                continue
            segments = present[len(base) :].split("/")
            if recursive:
                for depth in range(1, len(segments)):
                    entries[base + "/".join(segments[:depth])] = True
                entries[present] = False
            else:
                entries[base + segments[0]] = len(segments) > 1
        return tuple(
            WorkspaceEntry(path, is_dir) for path, is_dir in sorted(entries.items())
        )

    async def delete_dir(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
        publish_id: str | None = None,
        device_uuid: str | None = None,
    ) -> bool:
        """The port's tree delete; ``fail_clear`` answers the transports' silent ``False``."""
        self.delete_dir_calls.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "bot_id": bot_id,
                "engine_type": engine_type,
                "path": path,
            }
        )
        if self.fail_clear or path in self._fail_deletes:
            return False
        self.deleted.append(path)
        self._exists = {
            p for p in self._exists if p != path and not p.startswith(f"{path}/")
        }
        return True


# ── the delivery seam (W8) ───────────────────────────────────────────────
#
# ``ApplyRunService`` takes one **required** argument for the
# whole seam: a ``DeliveryStrategyFactory`` carrying one built strategy per
# engine family. The composition root assembles it, so a rig has to assemble
# one too — and assembling it the same way is the point, which is what these
# helpers are for. A rig that only exercises ARCA binds a teclaw row that
# raises if it is ever reached, which is the thing worth catching.
