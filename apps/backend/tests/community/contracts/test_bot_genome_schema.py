"""Contract: the Bot Genome contract files agree with each other and with the docs.

The contract lives in ``apps/backend/api-contracts/genome/v1/`` (work item
RSI-02). Nothing in Backend reads it yet: the Genome Registry (RSI-03) will
validate every revision and patch against it, and ``apps/evolution`` reads the
same files. Until then this suite is what keeps the files honest:

- every example validates, and every invalid example fails for the stated reason;
- every digest and revision id in the examples is real (the bytes are in
  ``examples/blobs/``);
- the canonical JSON vectors reproduce;
- the rewrite-measure vectors reproduce (README §6.3);
- every patch op has a risk tier, and the tier tables in the design docs equal
  ``risk-tiers.json``;
- every Manifest v1 example in ``docs/bot-config-manifest/examples.zh-CN.md``
  has a round-trip case, and each case's document gets the outcome it states
  from the real Manifest validator.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

import jsonschema
import pytest
import yaml
from referencing import Registry, Resource

from agentclaw.community.core.content_store import (
    canonical_tree_bytes,
    decode_tree_bytes,
)
from agentclaw.community.core.bot_genome.compile import compile_spec
from agentclaw.community.core.bot_genome.canonical import (
    NotCanonicalizable,
    canonicalize,
    is_canonical_gene_path,
    loads_strict,
    revision_id,
    sha256_digest,
)
from agentclaw.community.core.bot_genome.rewrite import lcs_length, rewrite_flagged, strip_common, to_lines
from agentclaw.community.core.bot_genome.spec_models import GenomeSpec

_BACKEND = Path(__file__).resolve().parents[3]
_CONTRACT = _BACKEND / "api-contracts" / "genome" / "v1"
_EXAMPLES = _CONTRACT / "examples"
_BLOBS = _EXAMPLES / "blobs"
_CASES = _EXAMPLES / "manifest-v1"
_EVOLUTION_DOCS = _BACKEND / "docs" / "bot-evolution"
_MANIFEST_EXAMPLES_DOC = _BACKEND / "docs" / "bot-config-manifest" / "examples.zh-CN.md"
_DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")


def _load(path: Path) -> Any:
    return loads_strict(path.read_text(encoding="utf-8"))


_REVISION_SCHEMA = _load(_CONTRACT / "genome-revision.schema.json")
_PATCH_SCHEMA = _load(_CONTRACT / "genome-patch.schema.json")
_RISK_TIERS = _load(_CONTRACT / "risk-tiers.json")
_REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in (_REVISION_SCHEMA, _PATCH_SCHEMA)
)
_VALIDATORS = {
    "revision": jsonschema.Draft7Validator(_REVISION_SCHEMA, registry=_REGISTRY),
    "patch": jsonschema.Draft7Validator(_PATCH_SCHEMA, registry=_REGISTRY),
}
_MEMORY_ITEMS = jsonschema.Draft7Validator(
    {"$ref": _REVISION_SCHEMA["$id"] + "#/definitions/memoryItems"}, registry=_REGISTRY
)


def _files(pattern: str) -> list[Path]:
    return sorted(_EXAMPLES.glob(pattern))


def _ids(paths: list[Path]) -> list[str]:
    return [str(path.relative_to(_EXAMPLES)) for path in paths]


_REVISIONS = _files("revisions/*.json") + _files("manifest-v1/*/revision.json")
_PATCHES = _files("patches/*.json")
_INVALID = _files("invalid/*.json")


def _blob(digest: str) -> bytes:
    path = _BLOBS / digest.split(":", 1)[1]
    assert path.is_file(), f"no blob for {digest}"
    data = path.read_bytes()
    assert sha256_digest(data) == digest, f"blob {path.name} does not hash to its name"
    return data


# --------------------------------------------------------------------------------------------
# Rules JSON Schema cannot express (README §2, §3.1): list order and uniqueness, canonical paths.


_SORTED_BY = {
    "persona": "type",
    "skills": "name",
    "resources": "path",
}


def order_violations(record: dict[str, Any]) -> list[str]:
    """Every breach of README §3.1's sort and uniqueness rules in one revision."""
    spec, policy = record["spec"], record["policy"]
    problems: list[str] = []

    def check(name: str, keys: list[str]) -> None:
        if keys != sorted(keys):
            problems.append(f"{name} is not sorted")
        if len(keys) != len(set(keys)):
            problems.append(f"{name} has duplicate keys")

    for category, key in _SORTED_BY.items():
        check(f"spec.{category}", [entry[key] for entry in spec[category]])
    check("spec.tools.mcp", [entry["server_code"] for entry in spec["tools"]["mcp"]])
    check("policy.mutable_genes", list(policy["mutable_genes"]))
    check("policy.pins", list(policy["pins"]))
    return problems


def gene_paths(record: dict[str, Any]) -> Iterator[str]:
    policy = record["policy"]
    yield from policy["mutable_genes"]
    yield from policy["pins"]
    yield from policy["risk_overrides"]


def _patch_gene_paths(patch: dict[str, Any]) -> Iterator[str]:
    for op in patch["ops"]:
        if "target" in op:
            yield op["target"]


# --------------------------------------------------------------------------------------------
# Schemas and examples


def test_schemas_are_valid_draft_07() -> None:
    for schema in (_REVISION_SCHEMA, _PATCH_SCHEMA):
        jsonschema.Draft7Validator.check_schema(schema)


def _schema_examples(node: Any, pointer: str = "") -> Iterator[tuple[str, Any]]:
    """Every ``examples`` entry in a schema, with the JSON pointer of the schema it illustrates."""
    if isinstance(node, dict):
        for example in node.get("examples", []):
            yield pointer, example
        for key, child in node.items():
            if key != "examples":
                yield from _schema_examples(child, f"{pointer}/{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _schema_examples(child, f"{pointer}/{index}")


def test_schema_examples_are_valid() -> None:
    """The ``examples`` in the schemas illustrate them, so each one must validate where it stands."""
    found = 0
    for schema in (_REVISION_SCHEMA, _PATCH_SCHEMA):
        for pointer, example in _schema_examples(schema):
            validator = jsonschema.Draft7Validator({"$ref": schema["$id"] + "#" + pointer}, registry=_REGISTRY)
            errors = [error.message for error in validator.iter_errors(example)]
            assert not errors, (pointer, errors)
            found += 1
    assert found, "the schemas carry examples"


@pytest.mark.parametrize("path", _REVISIONS, ids=_ids(_REVISIONS))
def test_every_example_revision_is_valid(path: Path) -> None:
    record = _load(path)
    errors = [error.message for error in _VALIDATORS["revision"].iter_errors(record)]
    assert not errors, errors
    assert not order_violations(record)
    assert all(is_canonical_gene_path(gene) for gene in gene_paths(record))


@pytest.mark.parametrize("path", _PATCHES, ids=_ids(_PATCHES))
def test_every_example_patch_is_valid(path: Path) -> None:
    patch = _load(path)
    errors = [error.message for error in _VALIDATORS["patch"].iter_errors(patch)]
    assert not errors, errors
    assert all(is_canonical_gene_path(gene) for gene in _patch_gene_paths(patch))
    canonicalize(patch)  # a patch is hashed into its candidate id, so it must be canonicalizable
    for file_path in _patch_file_paths(patch):
        _assert_segments(file_path)


@pytest.mark.parametrize("path", _INVALID, ids=_ids(_INVALID))
def test_every_invalid_example_fails_its_stated_check(path: Path) -> None:
    case = _load(path)
    record = case["record"]
    check = case["check"]
    if check == "schema":
        assert list(_VALIDATORS[case["schema"]].iter_errors(record)), case["reason"]
        return
    # The other checks run on schema-valid records, so each case fails only its own rule.
    assert not list(_VALIDATORS[case["schema"]].iter_errors(record)), "expected a schema-valid record"
    if check != "identity":
        assert record["revision"]["id"] == revision_id(record["spec"], record["policy"]), "keep the id consistent"
    if check == "order":
        assert order_violations(record), case["reason"]
    elif check == "canonical-path":
        assert not all(is_canonical_gene_path(gene) for gene in gene_paths(record)), case["reason"]
    elif check == "tree":
        with pytest.raises(AssertionError):
            for entry in record["spec"]["resources"]:
                if entry["path"].endswith("/"):
                    _tree_members(_blob(entry["digest"]))
    elif check == "skill-package":
        with pytest.raises(AssertionError):
            for entry in record["spec"]["skills"]:
                if entry["origin"] == "local":
                    _assert_skill_package(entry["name"], _tree_members(_blob(entry["digest"])))
    elif check == "utf8":
        with pytest.raises(UnicodeDecodeError):
            for entry in record["spec"]["persona"]:
                _blob(entry["digest"]).decode("utf-8")
    elif check == "admission":
        with pytest.raises(AssertionError):
            for entry in record["spec"]["resources"]:
                _assert_resource_admitted(entry)
    elif check == "resource-paths":
        with pytest.raises(AssertionError):
            _assert_resource_paths(record["spec"]["resources"])
    elif check == "identity":
        assert record["revision"]["id"] != revision_id(record["spec"], record["policy"]), case["reason"]
    else:
        pytest.fail(f"unknown check {check!r}")


# --------------------------------------------------------------------------------------------
# Identity and digests


@pytest.mark.parametrize("path", _REVISIONS, ids=_ids(_REVISIONS))
def test_example_revision_ids_and_digests_are_real(path: Path) -> None:
    record = _load(path)
    spec = record["spec"]
    expected = revision_id(spec, record["policy"])
    assert record["revision"]["id"] == expected, f"revision.id should be {expected}"

    for entry in spec["persona"]:
        _blob(entry["digest"]).decode("utf-8")  # apply refuses a persona file that is not UTF-8
    _assert_resource_paths(spec["resources"])
    for entry in spec["resources"]:
        _assert_segments(entry["path"])
    for entry in spec["resources"]:
        _assert_resource_admitted(entry)
    for entry in spec["skills"]:
        if entry["origin"] == "local":
            _assert_skill_package(entry["name"], _tree_members(_blob(entry["digest"])))
    if spec["memory"] is not None:
        raw = _blob(spec["memory"]["items_digest"])
        items = loads_strict(raw.decode("utf-8"))
        assert not list(_MEMORY_ITEMS.iter_errors(items))
        assert canonicalize(items) == raw, "memory items must be stored as their canonical JSON"
        keys = [item["key"] for item in items]
        assert keys == sorted(set(keys))
        for item in items:
            _assert_memory_item_order(item)
    if spec["script"] is not None:
        _blob(spec["script"]["digest"]).decode("utf-8")


def test_candidate_records_the_patch_that_made_it() -> None:
    base = _load(_EXAMPLES / "revisions" / "01-support-bot.json")
    candidate = _load(_EXAMPLES / "revisions" / "02-support-bot-candidate.json")
    patch = _load(_EXAMPLES / "patches" / "00-mixed-r1-to-r2.json")
    assert patch["base"] == base["revision"]["id"] == candidate["revision"]["parent"]
    assert candidate["revision"]["patch_digest"] == sha256_digest(canonicalize(patch))
    assert candidate["policy"] == base["policy"], "a patch never changes the policy"


@pytest.mark.parametrize("path", _PATCHES, ids=_ids(_PATCHES))
def test_patch_digest_references_are_real(path: Path) -> None:
    for op in _load(path)["ops"]:
        content = op.get("content")
        if isinstance(content, dict):
            _blob(content["digest"])
            if op["op"] == "resource.add":
                _assert_resource_admitted({"path": op["path"], "digest": content["digest"]})
        for value in (op.get("files") or {}).values():
            if isinstance(value, dict):
                _blob(value["digest"])
        if "item" in op:
            _assert_memory_item_order(op["item"])


# Tree member paths follow the patch schema's file-path rule (README §3.4): no control character
# (NUL included), backslash, drive letter, leading "/", or empty, "." or ".." segment.
_SAFE_MEMBER_PATH = re.compile(_PATCH_SCHEMA["definitions"]["skillFilePath"]["pattern"])


def _assert_segments(path: str) -> None:
    """No path segment is over 255 bytes of UTF-8, a file system's name limit (README §3.4)."""
    assert all(len(s.encode()) <= 255 for s in path.split("/")), f"a segment of {path!r} is over 255 bytes"


def _patch_file_paths(patch: dict[str, Any]) -> Iterator[str]:
    """Every workspace or skill file path a patch names."""
    for op in patch["ops"]:
        if "path" in op:
            yield op["path"]
        yield from (op.get("files") or {})
        yield from (d["path"] for d in op.get("file_diffs", []))


def _tree_members(data: bytes) -> list[tuple[str, bytes]]:
    """Decode an acm-tree-v1 blob and require its canonical form (README §3.4)."""
    members = decode_tree_bytes(data)
    assert members is not None, "not an acm-tree-v1 blob"
    assert canonical_tree_bytes(members) == data, "tree members are not in canonical order"
    names = [name for name, _ in members]
    assert len(names) == len(set(names)), "tree member paths must be unique"
    colliding = sorted({n for n in names for m in names if m.startswith(n + "/")})
    assert not colliding, f"tree members {colliding} are files and also directories of other members"
    for name in names:
        assert _SAFE_MEMBER_PATH.match(name), f"tree member path {name!r} is not a safe relative path"
        _assert_segments(name)
    return members


def _assert_skill_package(name: str, members: list[tuple[str, bytes]]) -> None:
    """A local skill's tree is a package apply accepts (README §4.2, SkillPackageValidator)."""
    files = dict(members)
    assert "SKILL.md" in files and sum(1 for p in files if p.split("/")[-1] == "SKILL.md") == 1
    assert len(files) <= 500 and sum(map(len, files.values())) <= 50 * 1024 * 1024
    assert all(len(data) <= 10 * 1024 * 1024 and len(path) <= 256 for path, data in files.items())
    text = files["SKILL.md"].decode("utf-8")
    assert text.startswith("---\n"), "SKILL.md needs YAML front matter"
    metadata = yaml.safe_load(text.split("---\n", 2)[1])
    assert metadata.get("name") == name, f"SKILL.md names its skill {metadata.get('name')!r}, not {name!r}"
    assert str(metadata.get("description") or "").strip(), "SKILL.md needs a description"


def _assert_resource_paths(entries: list[dict[str, Any]]) -> None:
    """No resource path is a whole-segment prefix of another, and the files they write can coexist (README §4.2)."""
    names = [e["path"].rstrip("/") for e in entries]
    assert len(set(names)) == len(names), f"resource paths alias one workspace path: {names}"
    nested = sorted((n, m) for n in names for m in names if m.startswith(n + "/"))
    assert not nested, f"resource paths lie beneath other resource paths: {nested}"
    written = [
        f"{e['path']}{member}" for e in entries if e["path"].endswith("/") for member, _ in _tree_members(_blob(e["digest"]))
    ] + [e["path"] for e in entries if not e["path"].endswith("/")]
    colliding = sorted({n for n in written for m in written if m.startswith(n + "/")})
    assert not colliding, f"workspace files {colliding} are also directories of other files"
    too_long = [path for path in written if len(path.encode()) > 1024]
    assert not too_long, f"workspace paths over 1024 bytes: {too_long}"


def _assert_resource_admitted(entry: dict[str, Any]) -> None:
    """Every file a resource writes passes apply's workspace admission rule (README §4.2, §6.4)."""
    from agentclaw.community.core.resources.services.file_service import admission_refusal

    data = _blob(entry["digest"])
    files = _tree_members(data) if entry["path"].endswith("/") else [(entry["path"], data)]
    for path, content in files:
        refusal = admission_refusal(path.rsplit("/", 1)[-1], content)
        assert refusal is None, f"{entry['path']}: {path}: {refusal}"


def _assert_memory_item_order(item: dict[str, Any]) -> None:
    for field in ("tags", "source"):
        assert item[field] == sorted(set(item[field])), f"memory item {item['key']}: {field} must be sorted"


# --------------------------------------------------------------------------------------------
# Canonical JSON vectors

_VECTORS = _load(_CONTRACT / "canonical-json-vectors.json")


@pytest.mark.parametrize("vector", _VECTORS["canonical"], ids=lambda v: v["name"])
def test_canonical_json_vectors(vector: dict[str, Any]) -> None:
    canonical = canonicalize(loads_strict(vector["input"]))
    assert canonical.decode("utf-8") == vector["expected"]["canonical"]
    assert sha256_digest(canonical) == vector["expected"]["sha256"]


@pytest.mark.parametrize("vector", _VECTORS["rejected"], ids=lambda v: v["name"])
def test_canonical_json_rejections(vector: dict[str, Any]) -> None:
    assert vector["expected"]["rejected"] is True
    with pytest.raises(NotCanonicalizable):
        canonicalize(loads_strict(vector["input"]))


@pytest.mark.parametrize("vector", _VECTORS["revision_ids"], ids=lambda v: v["name"])
def test_revision_id_vectors(vector: dict[str, Any]) -> None:
    spec, policy = vector["input"]["spec"], vector["input"]["policy"]
    assert canonicalize({"spec": spec, "policy": policy}).decode() == vector["expected"]["canonical"]
    assert revision_id(spec, policy) == vector["expected"]["revision_id"]


# --------------------------------------------------------------------------------------------
# Rewrite measure vectors

_REWRITE = _load(_CONTRACT / "rewrite-vectors.json")


def _expand(runs: list[list[Any]]) -> list[str]:
    return [line for line, count in runs for _ in range(count)]


@pytest.mark.parametrize("vector", _REWRITE["vectors"], ids=lambda v: v["name"])
def test_rewrite_vectors(vector: dict[str, Any]) -> None:
    threshold = _RISK_TIERS["rewrite_threshold"]
    given, expected = vector["input"], vector["expected"]
    if "a_text" in given:
        a, b = to_lines(given["a_text"]), to_lines(given["b_text"])
    else:
        a, b = _expand(given["a"]), _expand(given["b"])
    _, rest_a, rest_b = strip_common(a, b)
    assert (len(rest_a) * len(rest_b) > threshold["lcs_cell_limit"]) == expected["over_cell_limit"]
    if "lcs" in expected:
        assert len(a) - len(rest_a) + lcs_length(rest_a, rest_b) == expected["lcs"]
    flagged = rewrite_flagged(
        a, b, numerator=threshold["numerator"], denominator=threshold["denominator"],
        cell_limit=threshold["lcs_cell_limit"],
    )
    assert flagged == expected["flagged"], vector["note"]


# --------------------------------------------------------------------------------------------
# Risk tiers

_OPS = _PATCH_SCHEMA["definitions"]["op"]["properties"]["op"]["enum"]


def test_every_op_in_the_schema_has_a_risk_tier() -> None:
    rule_ops = [rule["op"] for rule in _RISK_TIERS["rules"]]
    assert set(rule_ops) == set(_OPS)
    # First match wins, so for each (op, target) the unconditional rule must come last.
    for op, target in {(rule["op"], rule["target"]) for rule in _RISK_TIERS["rules"]}:
        rules = [rule for rule in _RISK_TIERS["rules"] if rule["op"] == op and rule["target"] == target]
        assert "when" not in rules[-1], f"{op} on {target}: the last rule must have no condition"
        assert all("when" in rule for rule in rules[:-1]), f"{op} on {target}: an unconditional rule shadows later ones"


def _tier_rows() -> list[tuple[str, str, str, str]]:
    rows = []
    for rule in _RISK_TIERS["rules"]:
        when = rule.get("when")
        condition = "—" if when is None else ", ".join(f"{key}={value}".replace("=True", "") for key, value in when.items())
        rows.append((rule["op"], rule["target"], condition, rule["tier"]))
    return rows


def _doc_tier_rows(text: str) -> list[tuple[str, str, str, str]]:
    block = re.search(r"<!-- risk-tiers:begin -->(.*?)<!-- risk-tiers:end -->", text, re.S)
    assert block, "no <!-- risk-tiers:begin --> … <!-- risk-tiers:end --> table"
    rows = []
    for line in block.group(1).splitlines():
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or set(cells[0]) <= {"-", " "} or not re.match(r"[a-z_]+\.[a-z_]+\Z", cells[0]):
            continue
        rows.append(tuple(cells))
    return rows


@pytest.mark.parametrize(
    "doc", ["01-genome.md", "01-genome.zh-CN.md", "08-promotion.md", "08-promotion.zh-CN.md"]
)
def test_risk_tables_in_design_docs_match_risk_tiers_json(doc: str) -> None:
    text = (_EVOLUTION_DOCS / doc).read_text(encoding="utf-8")
    assert _doc_tier_rows(text) == _tier_rows()


# --------------------------------------------------------------------------------------------
# Manifest v1 round-trip cases

# The YAML blocks of examples.zh-CN.md that are Manifest documents or fragments, by section,
# and the case that works each one. A new example in that doc fails here until it has a case.
_DOC_SECTIONS = {
    "1.2": ["01-complete", "02-complete-without-engine-config"],
    "2": ["03-sources-only"],
    "3": ["04-engine-config"],
    "4": ["05-identity"],
    "5": ["06-resources-file-and-directory", "07-resources-git-and-archive"],
    "6": ["08-skills"],
    "7": ["09-cli-tools"],
    "8": ["10-mcp"],
    "9": ["11-script"],
}
_YAML_BLOCKS_PER_SECTION = {"1.2": 1, "2": 1, "3": 1, "4": 1, "5": 2, "6": 1, "7": 1, "8": 1, "9": 1}


def _yaml_blocks_by_section(text: str) -> dict[str, int]:
    """Count ```yaml blocks per section; §1's subsections count separately (only §1.2 is a document)."""
    counts: dict[str, int] = {}
    section = ""
    in_block = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if not in_block and re.match(r"```ya?ml\b", line.strip()) and section:
                counts[section] = counts.get(section, 0) + 1
            in_block = not in_block
            continue
        heading = None if in_block else re.match(r"(#{2,3}) (\d+(?:\.\d+)?)[ .]", line)
        if heading:
            level, number = heading.groups()
            if level == "##" or number.startswith("1."):
                section = number
    return counts


def test_manifest_v1_cases_cover_every_examples_doc_fragment() -> None:
    counts = _yaml_blocks_by_section(_MANIFEST_EXAMPLES_DOC.read_text(encoding="utf-8"))
    assert set(_DOC_SECTIONS) == set(_YAML_BLOCKS_PER_SECTION)
    assert counts == _YAML_BLOCKS_PER_SECTION, "examples.zh-CN.md changed: add or update a manifest-v1 case"
    cases = sorted(path.name for path in _CASES.iterdir() if path.is_dir() and path.name != "sources")
    assert cases == sorted(name for names in _DOC_SECTIONS.values() for name in names)


_CASE_DIRS = [path for path in sorted(_CASES.iterdir()) if path.is_dir() and path.name != "sources"]


@pytest.mark.parametrize("case", _CASE_DIRS, ids=[path.name for path in _CASE_DIRS])
def test_manifest_v1_case_gets_its_stated_outcome(case: Path) -> None:
    from agentclaw.community.core.bot_config_manifest.capabilities import resolve_capabilities
    from agentclaw.community.core.bot_config_manifest.schema import (
        ManifestValidationError,
        validate_document,
    )

    capabilities = resolve_capabilities(active_engine="openclaw", bot_type="personal", is_teclaw=lambda _: False)
    document = (case / "manifest.yaml").read_text(encoding="utf-8")
    expected = case / "expected.json"
    if expected.exists():
        with pytest.raises(ManifestValidationError) as refused:
            validate_document(document, capabilities)
        got = sorted((v.location, v.code) for v in refused.value.violations)
        assert got == sorted((v["location"], v["code"]) for v in _load(expected)["violations"])
        assert not (case / "revision.json").exists()
        return

    validate_document(document, capabilities)
    revision = _load(case / "revision.json")
    compiled = yaml.safe_load((case / "compiled.yaml").read_text(encoding="utf-8"))
    assert compiled == _compile(revision["spec"]), "compiled.yaml must be derived from revision.json"


class _BlobReader:
    """The example blobs as the registry's ``ContentReader``."""

    def read(self, digest: str) -> bytes:
        return _blob(digest)


def _compile(spec: dict[str, Any]) -> dict[str, Any]:
    """M′ from a revision: the registry's own compile (RSI-03), by manifest-v1-compatibility.md."""
    for entry in spec["persona"] + spec["resources"] + [s for s in spec["skills"] if "digest" in s]:
        _blob(entry["digest"])  # every digest the document points at is a real example blob
    return compile_spec(GenomeSpec.from_json(spec), _BlobReader(), revision_id="example").document


def test_example_digests_are_well_formed() -> None:
    for path in _BLOBS.iterdir():
        assert _DIGEST.match("sha256:" + path.name), path.name


# --------------------------------------------------------------------------------------------
# Recording, re-derived: each case's revision.json from manifest.yaml and the fixture sources


_SOURCES = _CASES / "sources"
_OSS_FIXTURES = {  # (bucket, key) -> the fixture file that object serves
    ("artifacts", "tools/skills/order-lookup-1.4.0.zip"): "oss-artifacts-order-lookup-1.4.0.zip",
    ("artifacts", "tools/shopctl/2.3.0/shopctl-linux-amd64"): "oss-artifacts-shopctl-linux-amd64",
    ("cms-assets", "kb/knowledge-base.zip"): "oss-cms-assets-knowledge-base.zip",
}
_DEFAULT_POLICY = {"mutable_genes": ["memory", "persona", "resources", "skills"], "pins": ["persona/SAFETY.md"], "risk_overrides": {}}


def _source(document: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    declared = document.get("sources", {})[entry["from"]] if "from" in entry else entry["source"]
    return {**declared, "subpath": (declared.get("subpath") or "") + (entry.get("subpath") or "")}


def _fetch_files(document: dict[str, Any], entry: dict[str, Any]) -> dict[str, bytes]:
    """The files an entry's source serves, after unpack and strip_components (README §7)."""
    source = _source(document, entry)
    if source["protocol"] == "git":
        root = _SOURCES / "git-content" / source["subpath"]
        if root.is_file():
            return {"": root.read_bytes()}
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    raw = (_SOURCES / _OSS_FIXTURES[(source["bucket"], source["key"])]).read_bytes()
    if "digest" in entry:
        assert sha256_digest(raw) == entry["digest"], "the Manifest's digest pin must match the fixture"
    if entry.get("unpack") != "zip":
        return {"": raw}
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    strip = entry.get("strip_components", 0)
    return {"/".join(name.split("/")[strip:]): data for name, data in files.items()}


def _single(files: dict[str, bytes]) -> bytes:
    assert list(files) == [""], "expected one file"
    return files[""]


def _skill_files(files: dict[str, bytes]) -> list[tuple[str, bytes]]:
    """The files the skills materialiser keeps: wrapper stripped, ignored paths dropped (README §4.2)."""
    from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
    from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator

    return list(SkillPackageValidator(SkillParser()).validate_directory(list(files.items())).files)


def _record(document: dict[str, Any]) -> dict[str, Any]:
    """A first revision on an empty bot, recorded from ``document`` against the fixtures."""
    manifest = document.get("manifest") or {}
    persona = []
    for entry in manifest.get("identity", []):
        data = entry["content"].encode() if "content" in entry else _single(_fetch_files(document, entry))
        persona.append({"type": entry["type"], "digest": sha256_digest(data)})
    skills = [
        {"name": e["name"], "origin": "local", "digest": sha256_digest(canonical_tree_bytes(_skill_files(_fetch_files(document, e))))}
        for e in manifest.get("skills", [])
    ]
    resources = []
    for entry in manifest.get("resources", []):
        files = _fetch_files(document, entry)
        data = canonical_tree_bytes(list(files.items())) if entry["path"].endswith("/") else _single(files)
        resources.append({"path": entry["path"], "digest": sha256_digest(data)})
    for entry in manifest.get("cli_tools", []):
        _fetch_files(document, entry)  # ignored by genome v1, but its digest pin is still checked
    mcp = []
    for entry in manifest.get("mcp", []):
        config = dict(entry.get("config") or {})
        if "transport_protocol" in config:
            config["transport_protocol"] = config["transport_protocol"].upper()
        mcp.append({"server_code": entry["server_code"], **({"config": config} if "config" in entry else {})})
    script = None
    if document.get("script"):
        body = document["script"]["body"]
        assert "${BOT_" not in body, "these fixtures do not exercise placeholder substitution"
        script = {"digest": sha256_digest(body.encode())}
    return {
        "persona": sorted(persona, key=lambda e: e["type"]),
        "skills": sorted(skills, key=lambda e: e["name"]),
        "memory": None,
        "resources": sorted(resources, key=lambda e: e["path"]),
        "tools": {"mcp": sorted(mcp, key=lambda e: e["server_code"])},
        "engine_config": {},
        "script": script,
    }


_RECORDED_CASES = [path for path in _CASE_DIRS if (path / "revision.json").exists()]


@pytest.mark.parametrize("case", _RECORDED_CASES, ids=[path.name for path in _RECORDED_CASES])
def test_manifest_v1_case_revision_is_what_recording_produces(case: Path) -> None:
    document = yaml.safe_load((case / "manifest.yaml").read_text(encoding="utf-8"))
    revision = _load(case / "revision.json")
    assert revision["spec"] == _record(document)
    assert revision["policy"] == _DEFAULT_POLICY
