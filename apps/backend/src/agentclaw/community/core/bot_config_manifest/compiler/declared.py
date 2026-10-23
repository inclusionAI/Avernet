"""What a parsed document declares for one construct, or that it does not."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agentclaw.community.core.apply_engine.constructs import ApplyConstruct, ManifestSection

__all__ = ["declared_entries"]


def declared_entries(parsed: Mapping[str, Any], construct: ApplyConstruct) -> Sequence[dict[str, Any]] | None:
    """What the document declares for one construct, or ``None`` if it does not.

    ``parsed`` is the whole validated document. Worked through for one
    document::

        parsed = {
            "schema_version": 1,
            "script": {"body": "echo hi"},
            "manifest": {"mcp": [{"server_code": "gh"}], "skills": []},
        }

        declared_entries(parsed, ManifestSection.SCRIPT)
        # -> [{"body": "echo hi"}]      a section arrives as a one-entry list
        declared_entries(parsed, ManifestCategory.MCP)
        # -> [{"server_code": "gh"}]
        declared_entries(parsed, ManifestCategory.SKILLS)
        # -> []                         declared empty: remove everything
        declared_entries(parsed, ManifestCategory.RESOURCES)
        # -> None                       not declared: do not touch the area

    The distinction this returns is the load-bearing one, and it is why the
    return type is ``None``-or-list rather than just a list:

    * ``None`` — **not declared**. No opinion; the area is untouched and nothing
      about it is reported. This is what makes deleting a manifest delete
      nothing: a document with no categories declares nothing, so nothing is
      overwritten.
    * ``[]`` — **declared empty**. A declaration that the set is empty, so the
      area is emptied. For a section, ``script: null`` is this case (#2862):
      the key is present and declares that there is no script.

    A construct is read from the document by its own name: a section sits at
    the top level, a category under ``manifest``. Which of the two a construct
    is, is its type — this function does not carry a list of names.
    """
    if isinstance(construct, ManifestSection):
        if construct.value not in parsed:
            return None
        section = parsed[construct.value]
        # A section is a single object rather than a list; the compiler works
        # in entries throughout, so it arrives as a one-entry list. An
        # explicitly-null section is a declaration that there is none.
        return [section] if isinstance(section, dict) else []
    manifest = parsed.get("manifest")
    if not isinstance(manifest, dict) or construct.value not in manifest:
        return None
    entries = manifest.get(construct.value)
    if entries is None:
        return []
    return entries if isinstance(entries, list) else []
