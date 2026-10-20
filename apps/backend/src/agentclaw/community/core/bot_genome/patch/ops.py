"""Apply a Genome Patch to a spec, op by op (README §6.1, §6.2).

``apply_patch`` takes a schema-valid patch and the spec of its base, and
returns the new spec with every byte the patch produced. It stops at the
first op that does not apply *exactly*: the entry it names is missing (or
present, for an add), a heading or anchor is not unique, a diff's context
does not match. The reason codes are the contract's: ``does_not_apply``,
``memory_disabled``, ``not_utf8``, ``limit_exceeded``.

What it does not do: policy (``policy.py``), risk (``risk.py``), the
apply-time predicates (``spec_checks.py``) and limits on counts. Those run
on the outcome, so one ``PatchRejected`` can list every reason at once where
the contract allows it.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (
    canonical_tree_bytes,
    decode_tree_bytes,
)
from agentclaw.community.core.bot_genome import gene_paths
from agentclaw.community.core.bot_genome.canonical import (
    NotCanonicalizable,
    canonicalize,
    loads_strict,
    sha256_digest,
)
from agentclaw.community.core.bot_genome.errors import PatchRejected, Reason
from agentclaw.community.core.bot_genome.limits import MAX_DIFF_BYTES, MAX_INLINE_CONTENT_BYTES
from agentclaw.community.core.bot_genome.models import ContentReader
from agentclaw.community.core.bot_genome.text_edits import (
    EditDoesNotApply,
    apply_edits,
    apply_unified_diff,
    description_only,
)

__all__ = ["FileChange", "OpTouch", "PatchOutcome", "apply_patch"]


@dataclass(frozen=True)
class FileChange:
    """One file the patch changed, base → result, for the rewrite measure (README §6.3).

    ``text`` is False when either side is a directory or a skill tree (a
    replacement of one entry kind by another), which the contract flags
    without measuring.
    """

    before: bytes | None
    after: bytes | None
    text: bool = True


@dataclass(frozen=True)
class OpTouch:
    """What one op touched: its gene path, the files it changed, and the facts the risk rules ask for."""

    op: str
    gene: str
    files: tuple[str, ...] = ()
    description_only: bool = False
    mode: str | None = None


@dataclass(frozen=True)
class PatchOutcome:
    spec: dict[str, Any]
    writes: tuple[tuple[str, bytes], ...]
    touched: tuple[OpTouch, ...]
    changed_files: dict[str, FileChange] = field(default_factory=dict)
    replaced: frozenset[str] = frozenset()


def _is_text(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


class _Reject(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Applier:
    """Mutable state of one application; ``apply_patch`` is its only entry."""

    def __init__(self, base_spec: dict[str, Any], content: ContentReader) -> None:
        self.spec = copy.deepcopy(base_spec)
        self.content = content
        self.writes: dict[str, bytes] = {}
        self.touched: list[OpTouch] = []
        self.changed: dict[str, FileChange] = {}
        self.replaced: set[str] = set()
        # gene paths this patch added (entries and skill members): not in the base, so never measured
        self.added: set[str] = set()
        # gene path → (digest or None, is a tree) for entries removed earlier in this patch
        self.removed: dict[str, tuple[str | None, bool]] = {}

    # -- helpers ------------------------------------------------------------------------------

    def _store(self, data: bytes) -> str:
        digest = sha256_digest(data)
        self.writes[digest] = data
        return digest

    def _read(self, digest: str) -> bytes:
        """Bytes an earlier op of this patch produced, or stored bytes."""
        return self.writes[digest] if digest in self.writes else self.content.read(digest)

    def _bytes_of(self, value: Any, location: str) -> bytes:
        """The bytes a ``content`` value names: inline text (bounded) or uploaded bytes by digest."""
        if isinstance(value, str):
            data = value.encode("utf-8")
            if len(data) > MAX_INLINE_CONTENT_BYTES:
                raise _Reject("limit_exceeded", f"{location}: inline content is {len(data)} bytes, over {MAX_INLINE_CONTENT_BYTES}")
            self.writes[sha256_digest(data)] = data
            return data
        return self._read(value["digest"])

    def _text_of(self, data: bytes, what: str) -> str:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _Reject("not_utf8", f"{what} is not UTF-8 text") from exc

    def _record_change(self, gene: str, before: bytes, after: bytes) -> None:
        """Measured once, base → result: the first record's base side (and kind) is kept."""
        if any(gene_paths.covers(added, gene) for added in self.added):
            return  # not in the base: README §6.3 measures only files that exist there
        earlier = self.changed.get(gene)
        if earlier is None:
            self.changed[gene] = FileChange(before, after)
        elif earlier.text:
            self.changed[gene] = FileChange(earlier.before, after)
        # a replacement across kinds stays flagged whatever happens to the file afterwards

    def _mark_add(self, gene: str, *, new_digest: str | None, new_is_tree: bool) -> None:
        """README §6.3 replacement: an add after a remove of the same key is judged as a change."""
        old = self.removed.pop(gene, None)
        if old is None:
            self.added.add(gene)
            return
        old_digest, old_is_tree = old
        self.replaced.add(gene)
        earlier = self.changed.get(gene)  # the entry was edited before it was removed: its base side wins
        if old_digest is None or new_digest is None or old_is_tree or new_is_tree or (earlier is not None and not earlier.text):
            self.changed[gene] = FileChange(None, None, text=False)
            return
        before = earlier.before if earlier is not None else self._read(old_digest)
        self.changed[gene] = FileChange(before, self._read(new_digest))

    def _mark_remove(self, gene: str, digest: str | None, is_tree: bool) -> None:
        """Remember a removed base entry for the replacement rule; an entry this patch added just goes."""
        if gene in self.added:
            self.added.discard(gene)
            return
        self.removed[gene] = (digest, is_tree)

    def _entries(self, category: str) -> list[dict[str, Any]]:
        return self.spec[category]

    def _find(self, category: str, key: str, value: str) -> dict[str, Any] | None:
        return next((entry for entry in self._entries(category) if entry[key] == value), None)

    @staticmethod
    def _diff_text(text: str, diff: str, location: str) -> str:
        if len(diff.encode("utf-8")) > MAX_DIFF_BYTES:
            raise _Reject("limit_exceeded", f"{location}: the diff is over {MAX_DIFF_BYTES} bytes")
        try:
            return apply_unified_diff(text, diff)
        except EditDoesNotApply as exc:
            raise _Reject("does_not_apply", f"{location}: {exc}") from exc

    # -- file.edit ----------------------------------------------------------------------------

    def file_edit(self, op: dict[str, Any], location: str) -> OpTouch:
        target = op["target"]
        try:
            category, segments = gene_paths.split(target)
        except ValueError as exc:
            raise _Reject("does_not_apply", f"{location}: {exc}") from exc
        for edit in op["edits"]:
            for key in ("content", "diff"):
                if key in edit and len(edit[key].encode("utf-8")) > MAX_INLINE_CONTENT_BYTES:
                    raise _Reject("limit_exceeded", f"{location}: an edit's {key} is over {MAX_INLINE_CONTENT_BYTES} bytes")

        if category == "persona" and len(segments) == 1:
            entry = self._find("persona", "type", segments[0])
            if entry is None:
                raise _Reject("does_not_apply", f"{location}: the bot has no persona file {segments[0]!r}")
            before = self._read(entry["digest"])
            after = self._edit_text(before, op["edits"], location)
            entry["digest"] = self._store(after)
            self._record_change(target, before, after)
            return OpTouch("file.edit", target, (target,))

        if category == "skills" and len(segments) >= 2:
            name, member = segments[0], "/".join(segments[1:])
            entry = self._find("skills", "name", name)
            if entry is None or entry["origin"] != "local":
                raise _Reject("does_not_apply", f"{location}: the bot has no local skill {name!r}")
            members = self._tree(entry["digest"], f"{location}: skill {name!r}")
            if member not in members:
                raise _Reject("does_not_apply", f"{location}: skill {name!r} has no file {member!r}")
            before = members[member]
            after = self._edit_text(before, op["edits"], location)
            members[member] = after
            entry["digest"] = self._store(canonical_tree_bytes(list(members.items())))
            self._record_change(target, before, after)
            is_skill_md = member == "SKILL.md"
            return OpTouch("file.edit", target, (target,), description_only=is_skill_md and description_only(before, after))

        if category == "resources" and segments:
            entry, member = self._resource_file(segments, location)
            if member is None:
                before = self._read(entry["digest"])
                after = self._edit_text(before, op["edits"], location)
                entry["digest"] = self._store(after)
            else:
                members = self._tree(entry["digest"], f"{location}: resource {entry['path']!r}")
                before = members[member]
                after = self._edit_text(before, op["edits"], location)
                members[member] = after
                entry["digest"] = self._store(canonical_tree_bytes(list(members.items())))
            self._record_change(target, before, after)
            return OpTouch("file.edit", target, (target,))

        raise _Reject("does_not_apply", f"{location}: {target!r} does not name a persona file, a skill file or a resource file")

    def _edit_text(self, before: bytes, edits: list[dict[str, Any]], location: str) -> bytes:
        text = self._text_of(before, f"{location}: the target")
        for index, edit in enumerate(edits):
            try:
                text = apply_edits(text, [edit])
            except EditDoesNotApply as exc:
                raise _Reject("does_not_apply", f"{location}.edits[{index}]: {exc}") from exc
        return text.encode("utf-8")

    def _tree(self, digest: str, what: str) -> dict[str, bytes]:
        members = decode_tree_bytes(self._read(digest))
        if members is None:
            raise _Reject("does_not_apply", f"{what} is not stored as an acm-tree-v1 tree")
        return dict(members)

    def _resource_file(self, segments: tuple[str, ...], location: str) -> tuple[dict[str, Any], str | None]:
        """The file resource at this path, or the directory resource it lies in plus the member path."""
        path = "/".join(segments)
        for entry in self._entries("resources"):
            if entry["path"] == path:
                return entry, None
        for cut in range(len(segments) - 1, 0, -1):
            directory = "/".join(segments[:cut]) + "/"
            for entry in self._entries("resources"):
                if entry["path"] == directory:
                    member = "/".join(segments[cut:])
                    members = self._tree(entry["digest"], f"{location}: resource {directory!r}")
                    if member not in members:
                        raise _Reject("does_not_apply", f"{location}: resource {directory!r} has no file {member!r}")
                    return entry, member
        raise _Reject("does_not_apply", f"{location}: the bot has no resource file {path!r}")

    # -- persona ------------------------------------------------------------------------------

    def persona_add(self, op: dict[str, Any], location: str) -> OpTouch:
        gene = gene_paths.persona_path(op["type"])
        if self._find("persona", "type", op["type"]) is not None:
            raise _Reject("does_not_apply", f"{location}: the bot already has persona file {op['type']!r}")
        data = self._bytes_of(op["content"], f"{location}.content")
        digest = sha256_digest(data)
        self._entries("persona").append({"type": op["type"], "digest": digest})
        self._mark_add(gene, new_digest=digest, new_is_tree=False)
        return OpTouch("persona.add", gene, (gene,) if gene in self.replaced else ())

    def persona_remove(self, op: dict[str, Any], location: str) -> OpTouch:
        gene = gene_paths.persona_path(op["type"])
        entry = self._find("persona", "type", op["type"])
        if entry is None:
            raise _Reject("does_not_apply", f"{location}: the bot has no persona file {op['type']!r}")
        self._entries("persona").remove(entry)
        self._mark_remove(gene, entry["digest"], False)
        return OpTouch("persona.remove", gene)

    # -- skills -------------------------------------------------------------------------------

    def skill_add(self, op: dict[str, Any], location: str) -> OpTouch:
        name = op["name"]
        gene = gene_paths.skill_path(name)
        if self._find("skills", "name", name) is not None:
            raise _Reject("does_not_apply", f"{location}: the bot already has skill {name!r}")
        members = {
            path: self._bytes_of(value, f"{location}.files[{path!r}]") for path, value in op["files"].items()
        }
        digest = self._store(canonical_tree_bytes(list(members.items())))
        self._entries("skills").append({"name": name, "origin": "local", "digest": digest})
        self._mark_add(gene, new_digest=digest, new_is_tree=True)
        return OpTouch("skill.add", gene, (gene,) if gene in self.replaced else ())

    def skill_update(self, op: dict[str, Any], location: str) -> OpTouch:
        name = op["name"]
        gene = gene_paths.skill_path(name)
        entry = self._find("skills", "name", name)
        if entry is None or entry["origin"] != "local":
            raise _Reject("does_not_apply", f"{location}: the bot has no local skill {name!r}")
        members = self._tree(entry["digest"], f"{location}: skill {name!r}")
        diffs = op.get("file_diffs", [])
        files = op.get("files", {})
        diff_paths = [item["path"] for item in diffs]
        twice = sorted({p for p in diff_paths if diff_paths.count(p) > 1} | (set(diff_paths) & set(files)))
        if twice:
            raise _Reject("does_not_apply", f"{location}: {twice[0]!r} appears more than once across file_diffs and files")
        changed: dict[str, tuple[bytes, bytes]] = {}
        for index, item in enumerate(diffs):
            path, where = item["path"], f"{location}.file_diffs[{index}]"
            if path not in members:
                raise _Reject("does_not_apply", f"{where}: skill {name!r} has no file {path!r}")
            before = members[path]
            after = self._diff_text(self._text_of(before, f"{where}: {path!r}"), item["diff"], where).encode("utf-8")
            members[path] = after
            changed[path] = (before, after)
        for path, value in files.items():
            where = f"{location}.files[{path!r}]"
            if value is None:
                if path not in members:
                    raise _Reject("does_not_apply", f"{where}: skill {name!r} has no file {path!r} to delete")
                if path == "SKILL.md":
                    raise _Reject("does_not_apply", f"{where}: SKILL.md cannot be deleted")
                del members[path]
                continue
            before = members.get(path)
            after = self._bytes_of(value, where)
            members[path] = after
            if before is not None:
                changed[path] = (before, after)
            else:
                self.added.add(gene_paths.skill_path(name, path))
        measured: list[str] = []
        for path, (before, after) in changed.items():
            if before != after and _is_text(before) and _is_text(after):
                file_gene = gene_paths.skill_path(name, path)
                self._record_change(file_gene, before, after)
                measured.append(file_gene)
        touched = set(diff_paths) | set(files)
        only_description = touched == {"SKILL.md"} and "SKILL.md" in changed and description_only(*changed["SKILL.md"])
        entry["digest"] = self._store(canonical_tree_bytes(list(members.items())))
        return OpTouch("skill.update", gene, tuple(measured), description_only=only_description)

    def skill_remove(self, op: dict[str, Any], location: str) -> OpTouch:
        name = op["name"]
        gene = gene_paths.skill_path(name)
        entry = self._find("skills", "name", name)
        if entry is None:
            raise _Reject("does_not_apply", f"{location}: the bot has no skill {name!r}")
        self._entries("skills").remove(entry)
        self._mark_remove(gene, entry.get("digest"), True)
        return OpTouch("skill.remove", gene)

    # -- resources ----------------------------------------------------------------------------

    def resource_add(self, op: dict[str, Any], location: str) -> OpTouch:
        path = op["path"]
        gene = gene_paths.resource_path(path)
        stripped = path.rstrip("/")
        for entry in self._entries("resources"):
            if entry["path"].rstrip("/") == stripped:
                raise _Reject("does_not_apply", f"{location}: the bot already has resource {entry['path']!r}")
        is_dir = path.endswith("/")
        if is_dir:
            if not isinstance(op["content"], dict):
                raise _Reject("does_not_apply", f"{location}: a directory resource is added from an uploaded tree, by digest")
            data = self._read(op["content"]["digest"])
            if decode_tree_bytes(data) is None:
                raise _Reject("does_not_apply", f"{location}: {op['content']['digest']} is not an acm-tree-v1 blob")
        else:
            data = self._bytes_of(op["content"], f"{location}.content")
        digest = sha256_digest(data)
        self._entries("resources").append({"path": path, "digest": digest})
        self._mark_add(gene, new_digest=digest, new_is_tree=is_dir)
        return OpTouch("resource.add", gene, (gene,) if gene in self.replaced else ())

    def resource_remove(self, op: dict[str, Any], location: str) -> OpTouch:
        path = op["path"]
        gene = gene_paths.resource_path(path)
        entry = self._find("resources", "path", path)
        if entry is None:
            raise _Reject("does_not_apply", f"{location}: the bot has no resource {path!r}")
        self._entries("resources").remove(entry)
        self._mark_remove(gene, entry["digest"], path.endswith("/"))
        return OpTouch("resource.remove", gene)

    # -- memory -------------------------------------------------------------------------------

    def _memory_items(self, location: str) -> list[dict[str, Any]]:
        memory = self.spec["memory"]
        if memory is None:
            raise _Reject("memory_disabled", f"{location}: the base has no curated memory (spec.memory is null)")
        try:
            items = loads_strict(self._read(memory["items_digest"]).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, NotCanonicalizable) as exc:
            raise _Reject("does_not_apply", f"{location}: the stored memory items are not canonical JSON") from exc
        if not isinstance(items, list):
            raise _Reject("does_not_apply", f"{location}: the stored memory items are not a list")
        return items

    def _store_memory_items(self, items: list[dict[str, Any]]) -> None:
        items.sort(key=lambda item: item["key"])
        self.spec["memory"]["items_digest"] = self._store(canonicalize(items))

    def memory_add(self, op: dict[str, Any], location: str) -> OpTouch:
        item, key = op["item"], op["item"]["key"]
        items = self._memory_items(location)
        if any(existing["key"] == key for existing in items):
            raise _Reject("does_not_apply", f"{location}: memory already has an item {key!r}")
        items.append(copy.deepcopy(item))
        self._store_memory_items(items)
        return OpTouch("memory.add", gene_paths.memory_path(key))

    def memory_update(self, op: dict[str, Any], location: str) -> OpTouch:
        item, key = op["item"], op["item"]["key"]
        items = self._memory_items(location)
        index = next((i for i, existing in enumerate(items) if existing["key"] == key), None)
        if index is None:
            raise _Reject("does_not_apply", f"{location}: memory has no item {key!r}")
        items[index] = copy.deepcopy(item)
        self._store_memory_items(items)
        return OpTouch("memory.update", gene_paths.memory_path(key))

    def memory_retire(self, op: dict[str, Any], location: str) -> OpTouch:
        key = op["key"]
        items = self._memory_items(location)
        kept = [existing for existing in items if existing["key"] != key]
        if len(kept) == len(items):
            raise _Reject("does_not_apply", f"{location}: memory has no item {key!r}")
        self._store_memory_items(kept)
        return OpTouch("memory.retire", gene_paths.memory_path(key))

    def memory_set_mode(self, op: dict[str, Any], location: str) -> OpTouch:
        if self.spec["memory"] is None:
            raise _Reject("memory_disabled", f"{location}: the base has no curated memory (spec.memory is null)")
        self.spec["memory"]["mode"] = op["mode"]
        return OpTouch("memory.set_mode", gene_paths.memory_path(), mode=op["mode"])

    # -- the loop -----------------------------------------------------------------------------

    _HANDLERS = {
        "file.edit": file_edit,
        "persona.add": persona_add,
        "persona.remove": persona_remove,
        "skill.add": skill_add,
        "skill.update": skill_update,
        "skill.remove": skill_remove,
        "resource.add": resource_add,
        "resource.remove": resource_remove,
        "memory.add": memory_add,
        "memory.update": memory_update,
        "memory.retire": memory_retire,
        "memory.set_mode": memory_set_mode,
    }

    def run(self, ops: list[dict[str, Any]]) -> PatchOutcome:
        for index, op in enumerate(ops):
            location = f"ops[{index}]"
            handler = self._HANDLERS.get(op.get("op"))
            if handler is None:
                raise PatchRejected([Reason("schema", location, f"{op.get('op')!r} is not a patch op")])
            try:
                self.touched.append(handler(self, op, location))
            except _Reject as exc:
                raise PatchRejected([Reason(exc.code, location, str(exc))]) from exc
        self.spec["persona"].sort(key=lambda entry: entry["type"])
        self.spec["skills"].sort(key=lambda entry: entry["name"])
        self.spec["resources"].sort(key=lambda entry: entry["path"])
        return PatchOutcome(
            spec=self.spec,
            writes=tuple(self.writes.items()),
            touched=tuple(self.touched),
            changed_files=dict(self.changed),
            replaced=frozenset(self.replaced),
        )


def apply_patch(base_spec: dict[str, Any], patch: dict[str, Any], content: ContentReader) -> PatchOutcome:
    """Ops in order, each on the result of the previous; ``PatchRejected`` at the first op that does not apply.

    ``patch`` is assumed valid against ``genome-patch.schema.json`` (the
    registry validates first); ``base_spec`` is not modified.
    """
    return _Applier(base_spec, content).run(patch["ops"])
