"""A source declaration, parsed once into one model.

**The axis is explicit.** A source says which protocol its content travels by —
``protocol: git`` or ``protocol: oss`` — and every rule downstream keys on that
value. It used to be spelled by *which key the mapping happened to carry*
(``git:`` for a repository, ``url:`` for everything else), which made the
protocol an emergent property of a shape rather than a declared fact: nothing
could enumerate it, the capabilities endpoint could not publish it, and the
support rules had to re-derive it per category. Every defect this change closes
begins there.

**One parser, both times it is asked.** The ``PUT`` validator parses to refuse a
malformed declaration, and the apply-side fetcher parses to act on a stored one.
:func:`parse_source` is pure — no ``Context``, no I/O — so both get the same
answer and neither owns a private copy of the rules, the same arrangement
``relative_path_refusal`` already uses for paths.

**The old spelling is refused, not translated.** ``schema_version`` stays ``1``:
the feature is pre-release, so there is no installed base whose documents a
compatibility road would protect, and a dual spelling is two vocabularies to
keep honest forever. A source written the old way is refused at ``PUT`` with a
message naming the replacement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from agentclaw.community.core.bot_config_manifest.support_matrix import SourceKind

#: ``mode`` selects what happens when a ref resolves somewhere new
#: (work-items §3.2). Declared on the **source**, never on an entry: the
#: property is "may this ref move under me", which belongs to the thing holding
#: the ref.
VALID_SOURCE_MODES: frozenset[str] = frozenset({"strict", "non_strict"})

#: The default. Someone who writes ``ref: main`` rather than a SHA is asking for
#: the ref to move; ``strict`` is how they say otherwise.
DEFAULT_SOURCE_MODE = "non_strict"

#: Keys every source may carry, whatever its protocol.
_COMMON_KEYS: frozenset[str] = frozenset({"protocol", "auth"})

#: Every other key belongs to exactly one protocol, with the sentence a caller
#: reads when they write it on the other one.
#:
#: The git side describes what only a repository has: ``url`` addresses it,
#: ``ref`` names a revision, ``mode`` rules on that revision's movement, and
#: ``subpath`` selects inside a **tree**. The oss side describes what only an
#: object store has, and it is two fields rather than a URL on purpose — the
#: bucket and the key are structured inputs to the store's own client, and a
#: URL would only be parsed back apart at the first call.
_KEY_OWNER: dict[str, tuple[SourceKind, str]] = {
    "url": (SourceKind.GIT, "addresses a repository"),
    "ref": (SourceKind.GIT, "names a revision"),
    "mode": (SourceKind.GIT, "rules on whether a revision may move"),
    "subpath": (SourceKind.GIT, "selects inside the tree a repository delivers"),
    "bucket": (SourceKind.OSS, "names a bucket in an object store"),
    "key": (SourceKind.OSS, "names an object inside that bucket"),
}

#: How each protocol says where its content is — quoted back to a caller who
#: reached for the other protocol's vocabulary.
_ADDRESSING: dict[SourceKind, str] = {
    SourceKind.GIT: "a git source addresses its content with 'url' and 'ref'",
    SourceKind.OSS: (
        "an oss source addresses its object with 'bucket' and 'key'"
    ),
}

_KEYS_BY_PROTOCOL: dict[SourceKind, frozenset[str]] = {
    protocol: _COMMON_KEYS
    | frozenset(k for k, (owner, _) in _KEY_OWNER.items() if owner is protocol)
    for protocol in (SourceKind.GIT, SourceKind.OSS)
}

#: What each protocol cannot do without. Everything else is optional or
#: defaulted; these are the fields whose absence makes a source unfetchable.
_REQUIRED_BY_PROTOCOL: dict[SourceKind, tuple[str, ...]] = {
    SourceKind.GIT: ("url",),
    # ``auth`` is required here and optional on git, and the asymmetry is not
    # an oversight. A bucket read needs an endpoint, and the endpoint is a
    # property of the credential — with no ``auth`` there is nowhere to send
    # the request. Refusing it at PUT rather than at apply is the rule this
    # whole feature is built on: the surface must not accept what it cannot
    # apply. The fetcher re-asks it as a belt, for documents that reached
    # storage before this rule existed.
    SourceKind.OSS: ("bucket", "auth"),
}

#: The field each protocol says *where* with. Writing the other protocol's is
#: one mistake, and the refusal for it already names the replacement — so the
#: "you are missing X" complaint is suppressed rather than added to it.
_ADDRESSING_FIELDS: frozenset[str] = frozenset({"url", "bucket"})

#: The protocols a source may declare. ``content`` is a
#: :class:`~agentclaw.community.core.bot_config_manifest.support_matrix.SourceKind`
#: but never a *source*: inline text is written on the entry and there is
#: nothing to declare.
DECLARABLE_PROTOCOLS: tuple[SourceKind, ...] = (SourceKind.GIT, SourceKind.OSS)

_PROTOCOL_LIST = " or ".join(f"'protocol: {p.value}'" for p in DECLARABLE_PROTOCOLS)


@dataclass(frozen=True)
class SourceDecl:
    """One source declaration, however and wherever it was written.

    The same model whether it came from the top-level ``sources`` map or from
    an entry's inline ``source:`` object — the two spellings are one thing, and
    a consumer that could tell them apart would eventually treat them
    differently.
    """

    protocol: SourceKind
    #: git only — the repository's address. ``None`` on an oss source, which
    #: addresses its content with ``bucket``/``key`` instead.
    url: str | None = None
    #: oss only — the bucket to read from. Lives on the source rather than on
    #: the credential because one credential commonly reads several buckets in
    #: an account, and the bucket is the part a manifest author chooses.
    bucket: str | None = None
    #: oss only — a key **prefix**, composed with an entry's own ``key`` the
    #: way ``subpath`` composes on the git road (source's first, then the
    #: entry's). ``None`` means the entry's key is the whole object name.
    key: str | None = None
    #: git only — a tag, a branch, or a full commit SHA. ``None`` means the
    #: repository's default head.
    ref: str | None = None
    #: git only — the part of the repository's tree this declaration
    #: addresses. Composes with an entry's own ``subpath`` (source's first,
    #: then the entry's), which is what lets one source serve many entries::
    #:
    #:     sources:
    #:       content:
    #:         protocol: git
    #:         url: https://code.example.com/team/content.git
    #:         ref: v1.2.0
    #:         subpath: kb          # ← this field
    #:     manifest:
    #:       resources:
    #:         - path: data/faq.csv
    #:           from: content
    #:           subpath: faq.csv   # ← composes to "kb/faq.csv"
    #:         - path: data/prices/
    #:           from: content
    #:           subpath: prices    # ← composes to "kb/prices", a whole subtree
    #:
    #: Both entries read one checkout of one commit: the composition changes
    #: which path is read out of the tree, never which tree is fetched.
    subpath: str | None = None
    #: The **name** of a stored credential. Never a value — the manifest has no
    #: vocabulary for one, at any depth.
    auth: str | None = None
    mode: str = DEFAULT_SOURCE_MODE

    @property
    def is_git(self) -> bool:
        return self.protocol is SourceKind.GIT


@dataclass(frozen=True)
class SourceViolation:
    """One problem with a declaration, addressed relative to the source.

    ``suffix`` is appended to whatever location the caller is reporting under
    (``sources.content``, ``manifest.skills[0].source``), so this module never
    needs to know where in a document it was called from.
    """

    suffix: str
    code: str
    message: str


def parse_source(
    raw: object,
) -> tuple[SourceDecl | None, tuple[SourceViolation, ...]]:
    """Read one source declaration. Returns the model, or ``None`` and reasons.

    ``raw`` is ``object`` rather than a mapping type, and rather than ``Any``:
    it is whatever ``yaml.safe_load`` produced at that position in a
    caller-authored document — a string, a list, ``None``, anything. ``Any``
    would silence the checker; ``object`` makes the narrowing below mandatory,
    which is exactly the contract this function has with its callers.

    Every problem that can be found is found — the whole document's violations
    are answered at once, so a caller fixes their source in one pass rather
    than discovering the next fault on each resubmission. A declaration is
    returned only when it is wholly usable; a partial model would be a value
    later stages could act on while the request is already being refused.
    """
    violations: list[SourceViolation] = []

    def add(suffix: str, code: str, message: str) -> None:
        violations.append(SourceViolation(suffix, code, message))


    if not isinstance(raw, Mapping):
        add("", "invalid_source", "a source must be a mapping")
        return None, tuple(violations)

    protocol = _parse_protocol(raw, add)
    if protocol is None:
        # Without a protocol nothing else can be judged: which keys are legal,
        # whether ``ref`` means anything, and what the support matrix is asked
        # all depend on it. Reporting shape errors under a guessed protocol
        # would name fields the caller may not even end up writing.
        return None, tuple(violations)

    allowed = _KEYS_BY_PROTOCOL[protocol]
    #: Keys refused because this protocol has no use for them. Their *shape* is
    #: then not checked: a caller who wrote ``mode: strictt`` on an object store
    #: has one mistake to fix (the field does not belong there), and answering
    #: with a second violation about the spelling of a value they must delete
    #: reads as two problems.
    misplaced: set[str] = set()
    for key in raw:
        if key in allowed or key == "apply_once":
            # ``apply_once`` is the document-wide reserved word, reported by
            # the sweep that names every occurrence at any depth. Naming it
            # here too would report one mistake as two problems.
            continue
        if key in _KEY_OWNER:
            misplaced.add(key)
            _, reason = _KEY_OWNER[key]
            add(
                f".{key}",
                "field_not_valid_for_protocol",
                f"'{key}' {reason} and is not valid on "
                f"'{protocol.value}' sources; {_ADDRESSING[protocol]}",
            )
            continue
        add(
            f".{key}",
            "unknown_field",
            f"unknown field '{key}' on a {protocol.value} source",
        )

    # A caller who addressed the source the other protocol's way has one
    # mistake, and the refusal above already names what to write instead;
    # adding "and you are missing 'bucket'" states that same mistake twice in
    # different words. Same discipline as ``misplaced`` itself.
    misaddressed = bool(misplaced & _ADDRESSING_FIELDS)
    for required in _REQUIRED_BY_PROTOCOL[protocol]:
        if required in misplaced or (
            misaddressed and required in _ADDRESSING_FIELDS
        ):
            continue
        value = raw.get(required)
        if not isinstance(value, str) or not value:
            add(
                f".{required}",
                "missing_source",
                f"a source on '{protocol.value}' must declare '{required}'",
            )

    url = None if "url" in misplaced else raw.get("url")
    if url is not None and (not isinstance(url, str) or not url):
        add(".url", "invalid_source", "'url' must be a non-empty string")
        url = None

    bucket = None if "bucket" in misplaced else raw.get("bucket")
    if bucket is not None and (not isinstance(bucket, str) or not bucket):
        add(".bucket", "invalid_bucket", "'bucket' must be a non-empty string")
        bucket = None

    key_prefix = None if "key" in misplaced else raw.get("key")
    if key_prefix is not None and (
        not isinstance(key_prefix, str) or not key_prefix
    ):
        add(".key", "invalid_path", "'key' must be a non-empty string")
        key_prefix = None

    ref = None if "ref" in misplaced else raw.get("ref")
    if ref is not None and (not isinstance(ref, str) or not ref):
        add(
            ".ref",
            "invalid_ref",
            "'ref' must be a non-empty string — a tag, a branch, or a full "
            "commit SHA",
        )
        ref = None

    subpath = None if "subpath" in misplaced else raw.get("subpath")
    if subpath is not None and not isinstance(subpath, str):
        add(".subpath", "invalid_path", "subpath must be a non-empty string")
        subpath = None

    auth = raw.get("auth")
    if auth is not None and (not isinstance(auth, str) or not auth):
        add(".auth", "invalid_auth", "'auth' must name a stored credential")
        auth = None

    mode = (
        DEFAULT_SOURCE_MODE
        if "mode" in misplaced
        else raw.get("mode", DEFAULT_SOURCE_MODE)
    )
    if mode not in VALID_SOURCE_MODES:
        # Refused rather than defaulted, for the reason ``on_fetch_failure``
        # refuses a misspelling: a typo would land on the default silently and
        # the caller would believe they had pinned something.
        add(".mode", "invalid_mode", "mode must be 'strict' or 'non_strict'")
        mode = DEFAULT_SOURCE_MODE

    if violations:
        return None, tuple(violations)
    return (
        SourceDecl(
            protocol=protocol,
            url=url,
            bucket=bucket,
            key=key_prefix,
            ref=ref,
            subpath=subpath,
            auth=auth,
            mode=str(mode),
        ),
        (),
    )


def _parse_protocol(
    raw: Mapping[str, Any], add: Callable[[str, str, str], None]
) -> SourceKind | None:
    """``protocol:``, or the message that names what to write instead."""
    declared = raw.get("protocol")
    if declared is None:
        add(".protocol", "missing_protocol", _missing_protocol_message(raw))
        return None
    if not isinstance(declared, str):
        add(".protocol", "invalid_protocol", _unknown_protocol_message(declared))
        return None
    try:
        protocol = SourceKind(declared)
    except ValueError:
        add(".protocol", "invalid_protocol", _unknown_protocol_message(declared))
        return None
    if protocol not in DECLARABLE_PROTOCOLS:
        # ``content`` is a real ``SourceKind`` and reaching it here means the
        # caller tried to declare inline text as a fetchable source. Named for
        # what it is rather than swept into "unknown protocol".
        add(
            ".protocol",
            "invalid_protocol",
            "inline text is written on the entry as 'content', not declared "
            f"as a source; a source declares {_PROTOCOL_LIST}",
        )
        return None
    return protocol


def _missing_protocol_message(raw: Mapping[str, Any]) -> str:
    """Name the replacement for whichever old spelling was written.

    A caller who wrote ``git: <url>`` gets the exact two lines that replace it,
    not the generic "protocol is required" — the old spelling is the reason
    almost every one of these refusals will be raised, and the shortest path
    from the refusal to a working document is worth the two branches.
    """
    if "git" in raw:
        return (
            "a source declares its protocol: replace 'git: <url>' with "
            "'protocol: git' and 'url: <url>'"
        )
    if "url" in raw:
        return (
            "a source declares its protocol: add 'protocol: oss' beside 'url' "
            "(or 'protocol: git' for a repository)"
        )
    return f"a source must declare {_PROTOCOL_LIST}"


def _unknown_protocol_message(declared: Any) -> str:
    return (
        f"unknown source protocol {declared!r}; this build speaks "
        + " and ".join(f"'{p.value}'" for p in DECLARABLE_PROTOCOLS)
    )


__all__ = [
    "DECLARABLE_PROTOCOLS",
    "DEFAULT_SOURCE_MODE",
    "VALID_SOURCE_MODES",
    "SourceDecl",
    "SourceViolation",
    "parse_source",
]
