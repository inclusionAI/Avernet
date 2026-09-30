"""No new check may hide inside these five routers' handler bodies.

The collaborator seam gets this property from assembly: an operation missing
from its table cannot be constructed, so the application does not start. There
is no assembly step to hook here, so a test does the same job — and does it by
inventory rather than by pattern-matching, because "is this a check or a
mapper?" is a judgement a reviewer makes, not one a regex makes.

Every module-private callable in the five routers is classified below. Adding a
sixth fails this test until someone writes down which side of Rule 7's line it
falls on: **domain policy belongs in core; error mapping and auth interpretation
may stay in the adapter.** The reason strings are the point — a mute allowlist
would let the next person add a check and silence the test in the same commit
without ever stating why.
"""

from __future__ import annotations

import importlib
import inspect

_PREFIX = "agentclaw.community.adapters.http.openapi_v1"

#: Module-private callables in the five in-scope routers, and why each is
#: allowed to be there. ``MOVED`` marks a name that is now a binding to a core
#: object — the check itself lives in ``core`` and the table names it; the
#: binding is kept so call sites read unchanged.
CLASSIFIED: dict[str, dict[str, str]] = {
    f"{_PREFIX}.resources.router": {
        "_safe_path": "MOVED — binding to core.safe_workspace_path",
        "_require_path": "MOVED — binding to core.require_workspace_path",
        "_file_coords": "MOVED — binding to core.resource_coords_from_record",
        "_reject_read_only": (
            "adapter: maps core's is_write_forbidden verdict onto this "
            "surface's 403 body. The decision is in core; only the phrasing is "
            "here, which Rule 7 permits"
        ),
        "_to_file_entry": "adapter: serialization, service dict → response schema",
        "_list_dir_or_empty": (
            "adapter: reads an absent directory as an empty listing so baas and "
            "the other providers answer alike. Transport-shaped, not policy"
        ),
        "_read_file_or_404": "adapter: maps device errors onto this surface's 404/413",
        "_preview_resource": "adapter: serializes shared preview bytes for two HTTP handlers",
    },
    f"{_PREFIX}.skills.router": {
        "_require_addressed_bot": "MOVED — binding to core.require_addressed_bot",
        "_require_skills_grant": (
            "adapter: takes ActingCaller, an adapter type, and an application "
            "grant is a fact about an HTTP caller. Apply arrives as its own "
            "operation with its own grant already checked at its own door"
        ),
        "_directory_relative_paths": (
            "adapter: parses the legacy multipart folder wire. A manifest has "
            "no multipart form to parse"
        ),
        "_tags": "adapter: serialization, tolerates the record's JSON-or-list tags",
        "_to_skill": "adapter: serialization, record → response schema",
        "_uploaded_skill_response": "adapter: chooses 200 vs 201 and builds the envelope",
    },
    f"{_PREFIX}.identity.router": {},
    f"{_PREFIX}.mcp.router": {
        "_to_server": "adapter: serialization",
        "_optional_text": "adapter: serialization",
        "_display_label": "adapter: serialization",
        "_snake_key": "adapter: serialization",
        "_legacy_detail_to_openapi": "adapter: serialization",
        "_to_server_detail": "adapter: serialization",
        "_category_label": "adapter: serialization",
        "_to_tenant": "adapter: serialization",
        "_to_config": "adapter: serialization",
    },
    f"{_PREFIX}.bots.engine_config": {
        "_engine_config_coords": (
            "MOVED — binding to core.engine_config_coords_from_record, which "
            "carries the ownership guard as well as the address"
        ),
    },
}


def _private_callables(module) -> set[str]:
    """Module-private callables *defined or bound* at this module's top level.

    Imported names are excluded — a private name imported from elsewhere is
    that module's business, and this test governs what these five own.
    """
    found = set()
    for name, obj in vars(module).items():
        if not name.startswith("_") or name.startswith("__"):
            continue
        if not callable(obj) or inspect.isclass(obj):
            continue
        found.add(name)
    return found


def test_every_private_callable_is_classified():
    unclassified: list[str] = []
    for module_path, classified in CLASSIFIED.items():
        module = importlib.import_module(module_path)
        for name in sorted(_private_callables(module) - set(classified)):
            unclassified.append(f"{module_path}.{name}")

    assert not unclassified, (
        "New module-private callables in the bot-config routers:\n  "
        + "\n  ".join(unclassified)
        + "\n\nClassify each in CLASSIFIED with a reason. Domain policy belongs "
        "in core, where manifest apply can reach it (see "
        "core/bot_config_surface/table.py); error mapping, serialization and "
        "auth interpretation may stay in the adapter (Rule 7)."
    )


def test_classification_has_no_stale_entries():
    """A name that left the router must leave the list, or the list is fiction."""
    stale: list[str] = []
    for module_path, classified in CLASSIFIED.items():
        module = importlib.import_module(module_path)
        present = _private_callables(module)
        for name in sorted(set(classified) - present):
            stale.append(f"{module_path}.{name}")
    assert not stale, f"Classified but no longer present: {stale}"


def test_every_reason_is_written_down():
    for module_path, classified in CLASSIFIED.items():
        for name, reason in classified.items():
            assert reason.strip(), f"{module_path}.{name} has an empty reason"


def test_moved_checks_really_live_in_core():
    """A ``MOVED`` claim must be true: the object's home is ``core``, not here."""
    for module_path, classified in CLASSIFIED.items():
        module = importlib.import_module(module_path)
        for name, reason in classified.items():
            if not reason.startswith("MOVED"):
                continue
            obj = getattr(module, name)
            home = getattr(obj, "__module__", "")
            assert home.startswith("agentclaw.community.core."), (
                f"{module_path}.{name} is classified MOVED but its home is "
                f"{home!r} — either it did not move, or the reason is wrong"
            )
            assert not home.startswith(_PREFIX), (
                f"{module_path}.{name} claims MOVED but still lives in the adapter"
            )
