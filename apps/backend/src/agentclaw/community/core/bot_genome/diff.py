"""The difference between two specs, per gene (01-genome.md §12 ``GenomeDiff``).

``diff_specs(a, b)`` reports every gene that ``b`` (the revision) has
added, removed or modified against ``a``, with a unified diff for a modified
UTF-8 text file (persona files, file resources, members of a directory or a
local skill, memory items, an MCP override, the script). Its tier is the
tier a patch making the same changes would get (README §6.3): each change
is judged as the op that makes it, the rewrite measure runs on each modified
text file, and the policy's overrides lift by overlap. Changes no patch can
make (``tools``, ``script``, ``engine_config``) are ``T3``: they are
human-only by construction.
"""
from __future__ import annotations

import difflib
import json
from typing import Any

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import decode_tree_bytes
from agentclaw.community.core.bot_genome import contract, gene_paths
from agentclaw.community.core.bot_genome.canonical import loads_strict
from agentclaw.community.core.bot_genome.limits import MAX_DIFF_BYTES
from agentclaw.community.core.bot_genome.models import ContentReader, GeneChange, GenomeDiff
from agentclaw.community.core.bot_genome.patch.ops import FileChange, OpTouch
from agentclaw.community.core.bot_genome.patch.risk import (
    apply_overrides,
    apply_raises,
    highest,
    op_tier,
    rewritten_files,
)
from agentclaw.community.core.bot_genome.text_edits import description_only

__all__ = ["diff_specs", "unified_diff"]

_NO_NEWLINE = "\\ No newline at end of file"
_SENTINEL = "\n"  # a line from _split never contains one, so the suffix is unambiguous


def unified_diff(before: str, after: str) -> str:
    """The hunks of ``before`` → ``after`` as the contract's ``diff`` kind spells them (README §6.2).

    No ``---``/``+++`` header lines; ``\\ No newline at end of file`` where
    the last line of either side lacks one, so the text applies back
    through ``text_edits.apply_unified_diff``.
    """
    old, old_nl = _split(before)
    new, new_nl = _split(after)
    # A missing final newline is part of the last line's identity (README §6.3 "lines"): marking it
    # makes difflib treat a flip of that state as a changed line instead of an unchanged one, and the
    # marker comes out as the contract's "\ No newline at end of file" line.
    old_marked = old[:-1] + [old[-1] + _SENTINEL] if old and not old_nl else list(old)
    new_marked = new[:-1] + [new[-1] + _SENTINEL] if new and not new_nl else list(new)
    out: list[str] = []
    for line in list(difflib.unified_diff(old_marked, new_marked, lineterm="", n=3))[2:]:
        if line.endswith(_SENTINEL) and not line.startswith("@@"):
            out.append(line[: -len(_SENTINEL)])
            out.append(_NO_NEWLINE)
        else:
            out.append(line)
    return "\n".join(out) + "\n" if out else ""


def _split(text: str) -> tuple[list[str], bool]:
    if text == "":
        return [], True
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
        return lines, True
    return lines, False


# ---------------------------------------------------------------------------------------------


class _Differ:
    def __init__(self, a: dict[str, Any], b: dict[str, Any], content: ContentReader) -> None:
        self.a, self.b, self.content = a, b, content
        self.changes: list[GeneChange] = []
        self.touched: list[OpTouch] = []
        self.files: dict[str, FileChange] = {}

    # -- helpers ------------------------------------------------------------------------------

    def _text_diff(self, before: bytes, after: bytes) -> str | None:
        try:
            text = unified_diff(before.decode("utf-8"), after.decode("utf-8"))
        except UnicodeDecodeError:
            return None
        return text if len(text.encode("utf-8")) <= MAX_DIFF_BYTES else None

    def _json_diff(self, before: Any, after: Any) -> str:
        dump = lambda value: json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"  # noqa: E731
        return unified_diff(dump(before), dump(after))

    def _file(self, gene: str, before: bytes, after: bytes) -> None:
        """A modified file: its change, its text diff, and its entry for the rewrite measure."""
        self.changes.append(GeneChange(gene, "modified", self._text_diff(before, after)))
        self.files[gene] = FileChange(before, after)

    def _members(self, gene: str, before: bytes, after: bytes) -> tuple[list[str], list[str]]:
        """Member-level changes of two trees: ``(modified members, every member touched)`` as gene paths."""
        old = dict(decode_tree_bytes(before) or [])
        new = dict(decode_tree_bytes(after) or [])
        modified: list[str] = []
        touched: list[str] = []
        for path in sorted(set(old) | set(new)):
            member = gene + "/" + gene_paths.join("skills", path).split("/", 1)[1]
            if path not in old:
                self.changes.append(GeneChange(member, "added"))
            elif path not in new:
                self.changes.append(GeneChange(member, "removed"))
            elif old[path] != new[path]:
                self._file(member, old[path], new[path])
                modified.append(member)
            else:
                continue
            touched.append(member)
        return modified, touched

    # -- categories ---------------------------------------------------------------------------

    def persona(self) -> None:
        old = {e["type"]: e["digest"] for e in self.a["persona"]}
        new = {e["type"]: e["digest"] for e in self.b["persona"]}
        for kind in sorted(set(old) | set(new)):
            gene = gene_paths.persona_path(kind)
            if kind not in old:
                self.changes.append(GeneChange(gene, "added"))
                self.touched.append(OpTouch("persona.add", gene))
            elif kind not in new:
                self.changes.append(GeneChange(gene, "removed"))
                self.touched.append(OpTouch("persona.remove", gene))
            elif old[kind] != new[kind]:
                self._file(gene, self.content.read(old[kind]), self.content.read(new[kind]))
                self.touched.append(OpTouch("file.edit", gene, (gene,)))

    def skills(self) -> None:
        old = {e["name"]: e for e in self.a["skills"]}
        new = {e["name"]: e for e in self.b["skills"]}
        for name in sorted(set(old) | set(new)):
            gene = gene_paths.skill_path(name)
            if name not in old:
                self.changes.append(GeneChange(gene, "added"))
                self.touched.append(OpTouch("skill.add", gene))
            elif name not in new:
                self.changes.append(GeneChange(gene, "removed"))
                self.touched.append(OpTouch("skill.remove", gene))
            elif old[name] != new[name]:
                self.changes.append(GeneChange(gene, "modified"))
                before, after = old[name], new[name]
                if before["origin"] == "local" and after["origin"] == "local":
                    old_blob, new_blob = self.content.read(before["digest"]), self.content.read(after["digest"])
                    changed, touched = self._members(gene, old_blob, new_blob)
                    skill_md = gene_paths.skill_path(name, "SKILL.md")
                    only_description = touched == [skill_md] and description_only(
                        self.files[skill_md].before or b"", self.files[skill_md].after or b""
                    )
                    self.touched.append(OpTouch("skill.update", gene, tuple(changed), description_only=only_description))
                else:
                    self.files[gene] = FileChange(None, None, text=False)  # a change of kind: a replacement
                    self.touched.append(OpTouch("skill.update", gene, (gene,)))

    def resources(self) -> None:
        old = {gene_paths.resource_path(e["path"]): e for e in self.a["resources"]}
        new = {gene_paths.resource_path(e["path"]): e for e in self.b["resources"]}
        for gene in sorted(set(old) | set(new)):
            if gene not in old:
                self.changes.append(GeneChange(gene, "added"))
                self.touched.append(OpTouch("resource.add", gene))
            elif gene not in new:
                self.changes.append(GeneChange(gene, "removed"))
                self.touched.append(OpTouch("resource.remove", gene))
            elif old[gene] != new[gene]:
                before, after = old[gene], new[gene]
                old_dir, new_dir = before["path"].endswith("/"), after["path"].endswith("/")
                old_blob, new_blob = self.content.read(before["digest"]), self.content.read(after["digest"])
                if old_dir and new_dir:
                    self.changes.append(GeneChange(gene, "modified"))
                    changed, _ = self._members(gene, old_blob, new_blob)
                    self.touched.append(OpTouch("file.edit", gene, tuple(changed)))
                elif old_dir or new_dir:
                    self.changes.append(GeneChange(gene, "modified"))
                    self.files[gene] = FileChange(None, None, text=False)
                    self.touched.append(OpTouch("resource.add", gene, (gene,)))
                else:
                    self._file(gene, old_blob, new_blob)
                    self.touched.append(OpTouch("file.edit", gene, (gene,)))

    def memory(self) -> None:
        old, new = self.a["memory"], self.b["memory"]
        if old is None and new is None:
            return
        gene = gene_paths.memory_path()
        if old is None or new is None:
            self.changes.append(GeneChange(gene, "added" if old is None else "removed"))
            self.touched.append(OpTouch("memory.set_mode", gene, mode=(new or old)["mode"]))
        elif old["mode"] != new["mode"]:
            self.changes.append(GeneChange(gene, "modified", self._json_diff({"mode": old["mode"]}, {"mode": new["mode"]})))
            self.touched.append(OpTouch("memory.set_mode", gene, mode=new["mode"]))
        old_items = self._items(old)
        new_items = self._items(new)
        for key in sorted(set(old_items) | set(new_items)):
            item_gene = gene_paths.memory_path(key)
            if key not in old_items:
                self.changes.append(GeneChange(item_gene, "added"))
                self.touched.append(OpTouch("memory.add", item_gene))
            elif key not in new_items:
                self.changes.append(GeneChange(item_gene, "removed"))
                self.touched.append(OpTouch("memory.retire", item_gene))
            elif old_items[key] != new_items[key]:
                self.changes.append(GeneChange(item_gene, "modified", self._json_diff(old_items[key], new_items[key])))
                self.touched.append(OpTouch("memory.update", item_gene))

    def _items(self, memory: dict[str, Any] | None) -> dict[str, Any]:
        if memory is None:
            return {}
        items = loads_strict(self.content.read(memory["items_digest"]).decode("utf-8"))
        return {item["key"]: item for item in items}

    def tools(self) -> None:
        old = {e["server_code"]: e for e in self.a["tools"]["mcp"]}
        new = {e["server_code"]: e for e in self.b["tools"]["mcp"]}
        for code in sorted(set(old) | set(new)):
            gene = gene_paths.mcp_path(code)
            if code not in old:
                self.changes.append(GeneChange(gene, "added"))
            elif code not in new:
                self.changes.append(GeneChange(gene, "removed"))
            elif old[code] != new[code]:
                self.changes.append(GeneChange(gene, "modified", self._json_diff(old[code], new[code])))
            else:
                continue
            self.touched.append(OpTouch("human.only", gene))

    def script(self) -> None:
        old, new = self.a["script"], self.b["script"]
        if old == new:
            return
        if old is None or new is None:
            self.changes.append(GeneChange("script", "added" if old is None else "removed"))
        else:
            self.changes.append(GeneChange("script", "modified", self._text_diff(self.content.read(old["digest"]), self.content.read(new["digest"]))))
        self.touched.append(OpTouch("human.only", "script"))

    def engine_config(self) -> None:
        if self.a["engine_config"] != self.b["engine_config"]:
            self.changes.append(GeneChange("engine_config", "modified", self._json_diff(self.a["engine_config"], self.b["engine_config"])))
            self.touched.append(OpTouch("human.only", "engine_config"))


def diff_specs(
    a: dict[str, Any],
    b: dict[str, Any],
    content: ContentReader,
    *,
    revision: str,
    against: str,
    policy: dict[str, Any] | None = None,
) -> GenomeDiff:
    """``b`` (the revision) against ``a``; ``policy`` is the revision's, for its risk overrides."""
    differ = _Differ(a, b, content)
    for category in (differ.persona, differ.skills, differ.resources, differ.memory, differ.tools, differ.script, differ.engine_config):
        category()
    tiers = contract.risk_tiers()
    rewritten = rewritten_files(differ.files, tiers["rewrite_threshold"])
    rewritten_set = set(rewritten)
    op_tiers = []
    for touch in differ.touched:
        tier = "T3" if touch.op == "human.only" else op_tier(touch, tiers["rules"])
        tier = apply_raises(tier, ["rewrite"] if rewritten_set.intersection(touch.files) else [], tiers["raises"])
        op_tiers.append(apply_overrides(tier, touch.gene, (policy or {}).get("risk_overrides", {})))
    return GenomeDiff(
        revision=revision,
        against=against,
        changes=tuple(differ.changes),
        risk_tier=highest(op_tiers),
        rewrite_flags=rewritten,
    )
