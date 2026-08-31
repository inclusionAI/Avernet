"""Manifest document schema v1 — models, parsing and document-level validation.

The wire shape is the contract in ``docs/bot-config-manifest/manifest-schema.zh-CN.md``;
this module turns it into one pydantic model per category plus a validator
that walks the parsed document and reports every rule broken with a stable
rule code and the offending entry's location (``skills[3]``, ``sources.content``).

Two layers, on purpose:

- **single-entry shape** — pydantic. Types, required fields, ``extra="forbid"``.
  ``extra="forbid"`` is what makes ``apply_once`` and ``engine_ext`` un-writable
  (the reserved word is rejected as an unknown field wherever it appears) —
  cheaper and more precise than enumerating forbidden names.
- **cross-entry rules** — :func:`validate_document`. ``from`` referring to an
  undeclared source, a resource path nested under another entry's directory,
  placeholder names, category limits: rules that compare one entry against
  another cannot live in a per-entry model.

Limits are the ``schema §5`` write-time-checkable ones (document size/entry
count/inline size); fetch-time limits (remote content sizes) belong to the
fetcher (W2) and are not duplicated here.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# The script size cap is #935's, reused verbatim so the two surfaces cannot
# drift: a manifest script and a plain startup-script PUT answer to the same
# limit. Core -> core import, allowed by the layer chain.
from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (  # noqa: F401
    MAX_SCRIPT_BYTES,
)

#: ``schema §5`` — write-time-checkable limits.
MAX_DOCUMENT_BYTES = 64 * 1024
MAX_CATEGORY_ENTRIES = 50
MAX_INLINE_CONTENT_BYTES = 64 * 1024

#: ``schema §2`` — ``on_fetch_failure``. ``skip`` was withdrawn by the D2
#: revision (category-coverage semantics): under "apply makes the category
#: equal the declaration", *skip* would mean "delete this entry" — the opposite
#: of what the word says. It is rejected at write time, here.
ON_FETCH_FAILURE_VALUES = ("keep_last", "fail")

#: ``schema §2.2`` — archive kinds the pipeline will ever understand.
UNPACK_KINDS = ("zip", "tar.gz")

#: ``schema §4`` — platform-injected placeholders. Whitelisted so a manifest
#: cannot probe platform internals through naming a variable that happens to
#: resolve. ``OCB_BOT_ARCH`` is constant ``amd64`` for the whole ARCA fleet
#: (work-items X3) — pinned here from day one because it is environment state,
#: not a W9 feature.
PLACEHOLDER_WHITELIST = frozenset(
    {"OCB_BOT_ID", "OCB_ENGINE_TYPE", "OCB_ENV", "OCB_TENANT", "OCB_BOT_ARCH"}
)

_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Categories a manifest document can declare, in declaration order.
CATEGORIES: tuple[str, ...] = (
    "mcp",
    "resources",
    "skills",
    "engine_config",
    "identity",
    "cli_tools",
)


@dataclass(frozen=True)
class Violation:
    """One broken rule, naming the entry it was broken on.

    ``entry`` is a document position (``skills[2]``, ``sources.content``) so
    the API can hand back a per-entry reason list — the #1469 acceptance:
    every rejection message names the offending entry.
    """

    entry: str
    rule: str
    message: str


class ManifestInvalidError(Exception):
    """The document violates schema v1. Carries every violation, not just one.

    Raised by :func:`parse_document` (shape errors) and by the service after
    :func:`validate_document` (cross-entry rules). The public surface maps it
    to 422 and returns the per-entry list.
    """

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = list(violations)
        summary = "; ".join(f"{v.entry}: {v.rule}" for v in self.violations[:5])
        if len(self.violations) > 5:
            summary = f"{summary}; +{len(self.violations) - 5} more"
        super().__init__(f"config manifest is invalid: {summary}")


# --- entry models -----------------------------------------------------------


class _EntryModel(BaseModel):
    """Shared config for every manifest entry model.

    ``extra='forbid'`` carries real acceptance weight: it is what rejects
    ``apply_once`` (v1 reserved word) and any unknown field, wherever they
    appear — #1469's "any position" without enumerating positions.
    """

    model_config = ConfigDict(extra="forbid")


class GitSource(_EntryModel):
    """Inline git source: repository URL, ref, optional in-repository subpath."""

    git: str = Field(..., description="仓库 https URL（见 manifest-schema §2.2）")
    ref: str = Field(..., description="tag / branch / commit SHA")
    subpath: Optional[str] = Field(
        default=None, description="源内路径（仓库内子目录或文件；缺省 = 仓库根）"
    )


class McpEntry(_EntryModel):
    """MCP servers by registry reference; credentials never ride the manifest."""

    server_code: str = Field(..., description="平台 MCP 注册表引用（必填）")
    config: Optional[dict[str, Any]] = Field(
        default=None, description="per-bot 配置，形状同现有 MCP config API"
    )


class ResourceEntry(_EntryModel):
    """Workspace resources: file or directory entries.

    A path ending in a slash declares a directory entry.
    """

    path: str = Field(..., description="workspace 相对逻辑路径（目录条目以 / 结尾）")
    from_: Optional[str] = Field(
        default=None, alias="from", description="命名源引用"
    )
    source: Optional[Any] = Field(
        default=None, description="内联来源：https URL 字符串或 git 引用对象"
    )
    content: Optional[str] = Field(default=None, description="内联 UTF-8 文本")
    subpath: Optional[str] = Field(default=None, description="源内路径")
    digest: Optional[str] = Field(default=None, description="sha256:… 钉扎")
    auth: Optional[str] = Field(default=None, description="租户级命名凭证引用")
    on_fetch_failure: Optional[str] = Field(default=None, description="keep_last | fail")
    unpack: Optional[str] = Field(default=None, description="zip | tar.gz（显式覆写）")
    strip_components: Optional[int] = Field(
        default=None, ge=0, description="剥掉归档内前 N 层目录段"
    )

    @field_validator("source")
    @classmethod
    def _source_shape(cls, v: Any) -> Any:
        if v is None or isinstance(v, str):
            return v
        if isinstance(v, dict):
            return GitSource.model_validate(v)
        raise ValueError("source must be an https URL string or a git object")


class SkillEntry(_EntryModel):
    """Local skills; a source that is not git must be pinned by digest."""

    name: str = Field(..., description="skill 标识符（安装位置由引擎决定）")
    from_: Optional[str] = Field(default=None, alias="from", description="命名源引用")
    source: Optional[Any] = Field(
        default=None, description="skill 目录（git）或 zip 包（URL）的来源"
    )
    content: Optional[str] = Field(default=None, description="内联 skill 文本")
    subpath: Optional[str] = Field(default=None, description="源内路径")
    digest: Optional[str] = Field(default=None, description="sha256:…（非 git 源必填）")
    auth: Optional[str] = Field(default=None, description="租户级命名凭证引用")
    on_fetch_failure: Optional[str] = Field(default=None, description="keep_last | fail")


class IdentityEntry(_EntryModel):
    """Identity/persona files; the engine's accepted type set applies."""

    type: str = Field(..., description="identity 文件类型（如 SOUL.md / CLAUDE.md）")
    from_: Optional[str] = Field(default=None, alias="from", description="命名源引用")
    source: Optional[Any] = Field(default=None, description="https URL 或 git 引用")
    content: Optional[str] = Field(default=None, description="小文件可内联")
    subpath: Optional[str] = Field(default=None, description="源内路径")
    digest: Optional[str] = Field(default=None, description="sha256:…")
    auth: Optional[str] = Field(default=None, description="租户级命名凭证引用")
    on_fetch_failure: Optional[str] = Field(default=None, description="keep_last | fail")


class CliToolEntry(_EntryModel):
    """CLI tools callable by the model: a static binary or an archive."""

    name: str = Field(..., description="工具名 / 命令名")
    source: str = Field(..., description="https URL（单二进制或归档）")
    digest: str = Field(..., description="sha256:…（本类目强制）")
    version: Optional[str] = Field(default=None, description="元数据，进 apply report")
    subpath: Optional[str] = Field(default=None, description="源内路径")
    unpack: Optional[str] = Field(default=None, description="zip | tar.gz")
    strip_components: Optional[int] = Field(
        default=None, ge=0, description="剥掉归档内前 N 层目录段"
    )
    entrypoints: Optional[list[str]] = Field(
        default=None, description="包内暴露为命令的文件（归档形态必填）"
    )


class EngineConfigPart(_EntryModel):
    """Engine configuration: one object; declared top-level keys win."""

    config: dict[str, Any] = Field(..., description="键值对象，形状同现有 engine-config API")


class ScriptPart(_EntryModel):
    """The imperative part: the per-bot startup script, engine-gated."""

    body: str = Field(..., description="脚本正文（≤ MAX_SCRIPT_BYTES，原文往返）")


class NamedSource(_EntryModel):
    """A named source: declared once, referenced by entries, ref changes upgrade atomically."""

    git: Optional[str] = Field(default=None, description="git 仓库 https URL")
    url: Optional[str] = Field(default=None, description="URL 源前缀")
    ref: Optional[str] = Field(default=None, description="git ref（git 源必填）")
    subpath: Optional[str] = Field(default=None, description="源内路径（可选）")
    auth: Optional[str] = Field(
        default=None, description="凭证引用（声明在源上，不在条目上）"
    )


class ManifestSection(_EntryModel):
    """The six declarative categories.

    An absent category does not participate in apply; a present-but-empty
    list means "no managed entities of this category" instead.
    """

    mcp: list[McpEntry] = Field(
        default_factory=list, description="MCP servers declared by registry reference"
    )
    resources: list[ResourceEntry] = Field(
        default_factory=list, description="Workspace resources: files and directories"
    )
    skills: list[SkillEntry] = Field(
        default_factory=list, description="Local skills to install and activate"
    )
    engine_config: Optional[EngineConfigPart] = Field(
        default=None, description="Engine configuration, one object"
    )
    identity: list[IdentityEntry] = Field(
        default_factory=list, description="Identity/persona files to write"
    )
    cli_tools: list[CliToolEntry] = Field(
        default_factory=list, description="CLI tools for the model to call"
    )


class ManifestDocument(_EntryModel):
    """配置清单文档 v1（顶层结构见 manifest-schema §1）。"""

    schema_version: Literal[1] = Field(default=1, description="manifest schema 版本")
    sources: dict[str, NamedSource] = Field(
        default_factory=dict, description="命名源（声明处，可选）"
    )
    manifest: ManifestSection = Field(default_factory=ManifestSection)
    script: Optional[ScriptPart] = Field(default=None, description="命令式部分")


EMPTY_DOCUMENT = ManifestDocument()



# --- parsing ----------------------------------------------------------------


def parse_document(raw: str | dict[str, Any] | ManifestDocument) -> ManifestDocument:
    """Parse the serialized document into :class:`ManifestDocument`.

    Pydantic's own errors are converted into :class:`Violation` s so every
    rejection — shape or cross-entry — reports the same way: a rule code and
    the document position it was broken on.
    """
    if isinstance(raw, ManifestDocument):
        return raw
    try:
        data: Any = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ManifestInvalidError(
            [Violation("<document>", "document-json", f"invalid JSON: {exc}")]
        ) from exc
    try:
        return ManifestDocument.model_validate(data)
    except ValidationError as exc:
        violations = [
            Violation(
                entry=".".join(str(part) for part in err["loc"]) or "<document>",
                rule="schema",
                message=str(err["msg"]),
            )
            for err in exc.errors()
        ]
        raise ManifestInvalidError(violations) from exc


# --- document-level validation ----------------------------------------------


def _iter_content_entries(
    doc: ManifestDocument,
) -> Iterable[tuple[str, McpEntry | ResourceEntry | SkillEntry | IdentityEntry]]:
    section = doc.manifest
    for entry in section.resources:
        yield "resources", entry
    for entry in section.skills:
        yield "skills", entry
    for entry in section.identity:
        yield "identity", entry


def _entry_label(category: str, index: int) -> str:
    return f"{category}[{index}]"


def _placeholder_violations(text: str | None, label: str) -> list[Violation]:
    """Every ``${...}`` must name a whitelisted platform variable.

    Positional (no braces) ``$VAR`` is left alone on purpose: it is plain shell
    in script bodies, and outside them the schema means braces only (§4).
    """
    if not text:
        return []
    return [
        Violation(
            entry=label,
            rule="placeholder-unknown",
            message=f"unknown placeholder ${{{name}}} (whitelist: "
            f"{', '.join(sorted(PLACEHOLDER_WHITELIST))})",
        )
        for name in _PLACEHOLDER_RE.findall(text)
        if name not in PLACEHOLDER_WHITELIST
    ]


def _path_ok(path: str) -> bool:
    """Logical paths: relative, no ``..`` segments, no emptiness.

    Applies to ``resources.path`` (workspace-relative by contract) and to
    ``subpath`` (source-internal). Windows separators are not paths here — a
    backslash is just a character the platform would faithfully reproduce into
    a filename, which the same rules above already constrain enough for the
    apply layer to re-check.
    """
    if not path or path.startswith("/") or path.startswith("\\"):
        return False
    return ".." not in path.split("/")


def _digest_ok(digest: str) -> bool:
    return bool(_DIGEST_RE.match(digest))


def _check_sources(doc: ManifestDocument) -> list[Violation]:
    out: list[Violation] = []
    for name, src in doc.sources.items():
        label = f"sources.{name}"
        kinds = [k for k in ("git", "url") if getattr(src, k)]
        if len(kinds) != 1:
            rule = "sources-multiple-kind" if kinds else "sources-no-kind"
            out.append(
                Violation(
                    entry=label, rule=rule,
                    message="a named source declares exactly one of git or url",
                )
            )
            continue
        if src.git and not src.ref:
            out.append(
                Violation(
                    entry=label, rule="sources-ref-required",
                    message="a git source declares a ref (tag / branch / SHA)",
                )
            )
        out.extend(_placeholder_violations(src.git, label))
        out.extend(_placeholder_violations(src.url, label))
        if src.subpath and not _path_ok(src.subpath):
            out.append(
                Violation(
                    entry=label, rule="subpath-traversal",
                    message=f"subpath must be a relative path without '..' "
                    f"segments: {src.subpath!r}",
                )
            )
    return out


def _entry_is_git_sourced(
    entry: ResourceEntry | SkillEntry | IdentityEntry,
    source_names: dict[str, NamedSource],
) -> bool:
    """Whether the entry's declared content ultimately comes from a git ref.

    Inline ``source`` object → git; ``from`` → git iff the named source
    declares ``git``; content/URL → not git.
    """
    if isinstance(entry.source, GitSource):
        return True
    if entry.from_ is not None:
        return bool((source_names.get(entry.from_) or NamedSource()).git)
    return False


def _check_entry(
    category: str,
    index: int,
    entry: ResourceEntry | SkillEntry | IdentityEntry,
    source_names: dict[str, NamedSource],
) -> list[Violation]:
    label = _entry_label(category, index)
    out: list[Violation] = []

    # 来源四选一：from / source / content 必须恰好一个（schema §2）。
    present = [
        kind
        for kind, value in (
            ("from", entry.from_),
            ("source", entry.source),
            ("content", entry.content),
        )
        if value is not None
    ]
    if len(present) == 0:
        out.append(
            Violation(
                entry=label, rule="entry-no-source",
                message="an entry declares exactly one of from / source / content",
            )
        )
        return out
    if len(present) > 1:
        out.append(
            Violation(
                entry=label, rule="entry-multiple-source",
                message=f"from / source / content are mutually exclusive; got "
                f"{', '.join(present)}",
            )
        )
        return out

    if "from" in present and entry.from_ not in source_names:
        out.append(
            Violation(
                entry=label, rule="from-undeclared",
                message=f"from references source name {entry.from_!r} the "
                f"document never declares",
            )
        )

    # auth 的合法位置：内联 source 条目，或命名源的声明处。from/content 条目
    # 上出现即拒（#1469）——原因正相反的两种错放。
    if "from" in present and entry.auth is not None:
        out.append(
            Violation(
                entry=label, rule="auth-not-inline-source",
                message="auth lives on the named source declaration, not on the "
                "entry referencing it",
            )
        )
    if "content" in present and (
        entry.auth is not None or entry.digest is not None or entry.on_fetch_failure is not None
    ):
        out.append(
            Violation(
                entry=label, rule="content-no-fetch-fields",
                message="inline content needs no fetch: auth / digest / "
                "on_fetch_failure are rejected on it",
            )
        )

    if entry.digest is not None:
        if _entry_is_git_sourced(entry, source_names):
            # git 源以 commit SHA 为天然 digest —— 条目 digest 写了即错（#1469）。
            out.append(
                Violation(
                    entry=label, rule="git-with-digest",
                    message="a git source is pinned by its ref/SHA; entry digest "
                    "is for URL sources only",
                )
            )
        elif not _digest_ok(entry.digest):
            out.append(
                Violation(
                    entry=label, rule="digest-format",
                    message=f"digest must be 'sha256:<64 hex chars>', got "
                    f"{entry.digest!r}",
                )
            )

    if entry.on_fetch_failure is not None and entry.on_fetch_failure not in ON_FETCH_FAILURE_VALUES:
        out.append(
            Violation(
                entry=label, rule="on-failure-value",
                message=f"on_fetch_failure must be one of "
                f"{', '.join(ON_FETCH_FAILURE_VALUES)} — 'skip' was withdrawn "
                f"by the category-coverage semantics",
            )
        )

    # 占位符扫描：URL 字符串、git URL/ref、subpath。$VAR 无大括号不扫——
    # 那在 script 正文里是普通 shell 语法,而 schema 的占位符一律用 ${…}（§4）。
    if isinstance(entry.source, str):
        out.extend(_placeholder_violations(entry.source, label))
    elif isinstance(entry.source, GitSource):
        out.extend(_placeholder_violations(entry.source.git, label))
        out.extend(_placeholder_violations(entry.source.ref, label))
    out.extend(_placeholder_violations(entry.subpath, label))

    if entry.subpath is not None and not _path_ok(entry.subpath):
        out.append(
            Violation(
                entry=label, rule="subpath-traversal",
                message=f"subpath must be relative without '..' segments: "
                f"{entry.subpath!r}",
            )
        )

    return out


def _normalized_dir(path: str) -> str:
    """``data/kb/`` → ``data/kb``：目录条目标识形态统一后再做嵌套比较."""
    return path.rstrip("/")


def _check_resources(
    entries: list[ResourceEntry], source_names: dict[str, NamedSource]
) -> list[Violation]:
    """resource 专属规则（schema §3.2）：path 形态、条目形态互锁、嵌套禁止。"""
    out: list[Violation] = []
    directory_paths: list[str] = []

    for index, entry in enumerate(entries):
        label = _entry_label("resources", index)
        # path 规则：相对、无 ``..``、非空。目录与否由结尾斜杠判定。
        if not _path_ok(entry.path.rstrip("\\/")) or entry.path == "/":
            if entry.path.startswith(("/", "\\")):
                rule = "resource-path-absolute"
            elif ".." in entry.path.split("/"):
                rule = "resource-dotdot"
            else:
                rule = "resource-path-empty"
            out.append(
                Violation(
                    entry=label, rule=rule,
                    message=f"path must be workspace-relative without '..' "
                    f"segments: {entry.path!r}",
                )
            )
        is_dir = entry.path.endswith("/")
        is_git = _entry_is_git_sourced(entry, source_names)
        if is_dir:
            directory_paths.append(_normalized_dir(entry.path))
            # 目录条目三形态只有两个合法：归档（URL 源,unpack 可显式覆写）
            # 或 git（免打包）。内联 content 表达不了树。
            if is_git and (entry.unpack is not None or entry.strip_components is not None):
                out.append(
                    Violation(
                        entry=label, rule="git-dir-no-unpack",
                        message="a directory entry sourced from git needs no "
                        "unpack or strip_components — the repository is the tree",
                    )
                )
            if entry.content is not None:
                out.append(
                    Violation(
                        entry=label, rule="resource-dir-content",
                        message="a directory entry cannot be inline content — "
                        "declare an archive or git source",
                    )
                )
            if entry.unpack is not None and entry.unpack not in UNPACK_KINDS:
                out.append(
                    Violation(
                        entry=label, rule="unpack-kind",
                        message=f"unpack must be one of {', '.join(UNPACK_KINDS)}",
                    )
                )
        else:
            if entry.unpack is not None or entry.strip_components is not None:
                out.append(
                    Violation(
                        entry=label, rule="unpack-on-file-entry",
                        message="unpack / strip_components describe archive "
                        "extraction and belong to directory entries only",
                    )
                )

    # 嵌套禁止（#1469）：任何条目的 path 落在另一个目录条目之下即拒——目录
    # 归 manifest、其内部文件又单独声明的所有权无法定义。两个目录条目互相
    # 嵌套同样被这条覆盖。
    if directory_paths:
        for index, entry in enumerate(entries):
            candidate = _normalized_dir(entry.path)
            for dir_path in directory_paths:
                if candidate != dir_path and candidate.startswith(dir_path + "/"):
                    out.append(
                        Violation(
                            entry=_entry_label("resources", index),
                            rule="resource-nested",
                            message=f"path {entry.path!r} nests under declared "
                            f"directory {dir_path!r}",
                        )
                    )
                    break
    return out


def _utf8_len_or_none(text: str) -> Optional[int]:
    """Byte length, or None when the text is not encodable UTF-8.

    A JSON-legal escaped lone surrogate survives ``json.loads`` and pydantic,
    then detonates in ``str.encode``; the #935 surface answers that with a 400
    — here it must be a violation like every other write-time rule, never an
    unhandled UnicodeEncodeError on the 500 path.
    """
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _check_limits(doc: ManifestDocument) -> list[Violation]:
    """``schema §5`` 写入期可查的限额：条目数与内联大小。

    文档总大小以序列化形态计,由服务层在落库前检查（这里拿不到序列化文本
    的权威长度）；远端内容大小属 fetch 期（W2）。
    """
    out: list[Violation] = []
    section = doc.manifest
    lists = (
        ("mcp", section.mcp),
        ("resources", section.resources),
        ("skills", section.skills),
        ("identity", section.identity),
        ("cli_tools", section.cli_tools),
    )
    for category, entries in lists:
        if len(entries) > MAX_CATEGORY_ENTRIES:
            out.append(
                Violation(
                    entry=f"manifest.{category}",
                    rule="limit-category-entries",
                    message=f"{len(entries)} entries exceed the per-category "
                    f"limit of {MAX_CATEGORY_ENTRIES}",
                )
            )
        for index, entry in enumerate(entries):
            content = getattr(entry, "content", None)
            if content is None:
                continue
            size = _utf8_len_or_none(content)
            if size is None:
                out.append(
                    Violation(
                        entry=_entry_label(category, index),
                        rule="not-encodable",
                        message="inline content is not valid UTF-8 (lone surrogate)",
                    )
                )
            elif size > MAX_INLINE_CONTENT_BYTES:
                out.append(
                    Violation(
                        entry=_entry_label(category, index),
                        rule="limit-inline-content",
                        message=f"inline content is {size} bytes, over the "
                        f"{MAX_INLINE_CONTENT_BYTES}-byte limit",
                    )
                )
    return out


def _check_script(doc: ManifestDocument) -> list[Violation]:
    """script 正文与 #935 共用同一限额（MAX_SCRIPT_BYTES 常量同一来源）。"""
    if doc.script is None:
        return []
    size = _utf8_len_or_none(doc.script.body)
    if size is None:
        return [
            Violation(
                entry="script.body",
                rule="not-encodable",
                message="script body is not valid UTF-8 (lone surrogate)",
            )
        ]
    if size > MAX_SCRIPT_BYTES:
        return [
            Violation(
                entry="script.body",
                rule="script-too-large",
                message=f"script body is {size} bytes, over the "
                f"{MAX_SCRIPT_BYTES}-byte limit",
            )
        ]
    return []


def validate_document(doc: ManifestDocument) -> list[Violation]:
    """跑全部文档级规则。空列表 = 合法；非空 = 调用方拒绝写入、逐条返回。

    一个函数跑完所有规则（而不是校验到第一条错就返回）：PUT 需要一份完整
    的逐条原因列表,不是第一道坎。
    """
    out: list[Violation] = []
    out.extend(_check_sources(doc))
    source_names: dict[str, NamedSource] = dict(doc.sources)
    for category, entries in (
        ("resources", doc.manifest.resources),
        ("skills", doc.manifest.skills),
        ("identity", doc.manifest.identity),
    ):
        for index, entry in enumerate(entries):
            out.extend(_check_entry(category, index, entry, source_names))
    out.extend(_check_resources(doc.manifest.resources, source_names))
    out.extend(_check_skills(doc.manifest.skills, source_names))
    out.extend(_check_limits(doc))
    out.extend(_check_script(doc))
    return out


def _check_skills(
    entries: list[SkillEntry], source_names: dict[str, NamedSource]
) -> list[Violation]:
    """skill 携带代码：非 git 源必须有 digest 钉扎（W5 的 PUT 面）."""
    out: list[Violation] = []
    for index, entry in enumerate(entries):
        label = _entry_label("skills", index)
        if entry.content is not None:
            continue
        is_git = isinstance(entry.source, GitSource)
        if not is_git and entry.from_ is not None:
            src = source_names.get(entry.from_) or NamedSource()
            is_git = bool(src.git)
        if not is_git and not entry.digest:
            out.append(
                Violation(
                    entry=label, rule="skills-digest-required",
                    message="skills carry code: a non-git source must be pinned "
                    "with digest (git is pinned by its SHA)",
                )
            )
    return out
