#!/usr/bin/env python3
"""Fail when newly added Git content contains likely plaintext credentials.

The scanner intentionally inspects only added lines in the requested Git range.
When a finding is reported, its added line is printed with the matched sensitive
value partially masked so the author can locate and fix the issue without
echoing a complete credential into the terminal or CI log.
"""

from __future__ import annotations

import argparse
import ast
import io
import math
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    content: str


PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
KNOWN_SECRET_RES = (
    ("Alibaba Cloud AccessKey ID", re.compile(r"\bLTAI[0-9A-Za-z]{12,30}\b")),
    ("Alibaba Cloud STS AccessKey ID", re.compile(r"\bSTS\.[0-9A-Za-z]{8,64}\b")),
    ("Alibaba Cloud security token", re.compile(r"\bCAIS[A-Za-z0-9_+=/-]{16,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b")),
    ("PyPI token", re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("Bearer token", re.compile(r"\bBearer[ \t]+[A-Za-z0-9._~+/=-]{24,}\b", re.IGNORECASE)),
)

ALIYUN_ASSIGNMENT_RE = re.compile(
    r"(?ix)"
    # Cover Alibaba Cloud/aliyun/OSS names in snake_case, kebab-case, and
    # common camelCase forms, including AK/SK aliases.
    r"(?:^|[^a-z0-9])"
    r"(?:alibaba(?:[._-]?cloud)?|aliyun|oss)[a-z0-9._-]*"
    r"(?:access[._-]?key(?:[._-]?(?:id|secret))?|ak|sk)\b[\"']?"
    r"\s*(?:[:=]|=>)\s*"
    r"(?:[\"'](?P<quoted>[^\"'\r\n]+)[\"']|(?P<bare>[^\s,;\"']+))"
)

ASSIGNMENT_RE = re.compile(
    r"(?ix)"
    # Permit common prefixes such as SERVICE_API_KEY while still requiring a
    # credential-like field name rather than matching arbitrary prose.
    r"(?:^|[^a-z0-9])(?:[a-z0-9]+[._-])*"
    r"(?:api[._-]?key|access[._-]?key|secret(?:[._-]?key)?|"
    r"private[._-]?key|client[._-]?secret|auth[._-]?token|"
    r"password|passwd|credential|token)\b[\"']?"
    r"\s*(?:[:=]|=>)\s*"
    r"(?:[\"'](?P<quoted>[^\"'\r\n]+)[\"']|(?P<bare>[^\s,;\"']+))"
)

PLACEHOLDER_RE = re.compile(
    r"(?ix)^(?:"
    r"\$\{[^}]+\}|\$[A-Z_][A-Z0-9_]*|"
    r"<[^>]+>|\.{3,}|"
    r"(?:changeme|change[-_ ]?me|dummy|example|fake|local(?:[-_ ]?only)?|"
    r"mock|not[-_ ]?a[-_ ]?real(?:[-_ ]?token)?|placeholder|sample|test(?:[-_ ]?token)?|"
    r"your[-_ ]?(?:token|secret|key|password)|"
    r"(?:dev|provider|bcs|baas|bot|gateway|openclaw|anthropic|e2e)[-_ ]?(?:test|token|secret|key)"
    r"))$")

# Common variable indirections and explicit non-secret values must not be
# treated as plaintext credentials by the key/value rule.
NON_SECRET_VALUE_RE = re.compile(
    r"(?ix)^(?:null|none|true|false|undefined|empty|unset|"
    r"os\.getenv\([^)]*\)|process\.env\.[A-Z0-9_]+)$"
)

# These values name entries in the existing secret resolver, not credentials.
SECRET_REFERENCE_RE = re.compile(r"^other_manual_agentclaw_[a-z0-9_]+$")
PYTHON_ATTRIBUTE_RE = re.compile(r"^\(*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\)*$")


def _entropy(value: str) -> float:
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _looks_like_plaintext_value(value: str) -> bool:
    value = value.strip()
    # Whitespace is not expected in the compact credential forms this rule
    # targets. Rejecting it also avoids treating string-concatenation source
    # such as `"KEY=" + value` as a literal credential.
    if any(char.isspace() for char in value):
        return False
    if (len(value) < 12 or PLACEHOLDER_RE.fullmatch(value)
            or NON_SECRET_VALUE_RE.fullmatch(value) or SECRET_REFERENCE_RE.fullmatch(value)):
        return False
    # A high-entropy value is a strong signal. Password/secret assignments are
    # also suspicious at a lower threshold because human-generated secrets can
    # have less entropy than generated API tokens.
    return _entropy(value) >= 3.2 or len(value) >= 24


# 以下为安全注释COSEC：掩码所有命中的敏感值后再显示源码行，避免诊断信息
# 在终端或 CI 日志中泄露完整凭据。
def _redact_value(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:2]}***{value[-3:]}"


def _redact_content(content: str) -> str:
    spans: list[tuple[int, int]] = []
    for pattern in (PRIVATE_KEY_RE, *(pattern for _, pattern in KNOWN_SECRET_RES)):
        spans.extend((match.start(), match.end()) for match in pattern.finditer(content))

    # A constructor may contain a literal in a nested mapping rather than a
    # credential-named assignment. Redact those literals before printing it.
    try:
        for token in tokenize.generate_tokens(io.StringIO(content.strip()).readline):
            if token.type != tokenize.STRING:
                continue
            try:
                value = ast.literal_eval(token.string)
            except (SyntaxError, ValueError):
                continue
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            if isinstance(value, str) and _looks_like_plaintext_value(value):
                indent = len(content) - len(content.lstrip())
                spans.append((indent + token.start[1], indent + token.end[1]))
    except (tokenize.TokenError, IndentationError):
        pass  # Partial diff lines still use the assignment/known-token rules.

    for assignment_pattern in (ALIYUN_ASSIGNMENT_RE, ASSIGNMENT_RE):
        for assignment in assignment_pattern.finditer(content):
            value = assignment.group("quoted") or assignment.group("bare") or ""
            if not _looks_like_plaintext_value(value):
                continue
            value_start = assignment.start("quoted")
            value_end = assignment.end("quoted")
            if value_start < 0 or value_end < 0:
                value_start = assignment.start("bare")
                value_end = assignment.end("bare")
            if value_start >= 0 and value_end >= 0:
                spans.append((value_start, value_end))

    if not spans:
        return content

    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    redacted = content
    for start, end in reversed(merged):
        redacted = redacted[:start] + _redact_value(content[start:end]) + redacted[end:]
    return redacted


def _added_lines(diff: str) -> list[tuple[str, int, str]]:
    path = ""
    new_line = 0
    added: list[tuple[str, int, str]] = []
    for raw_line in diff.splitlines():
        if raw_line.startswith("+++ b/"):
            path = raw_line[6:]
            continue
        if raw_line.startswith("@@ "):
            match = re.search(r"\+(\d+)(?:,(\d+))?", raw_line)
            if match is None:
                path = ""
                continue
            new_line = int(match.group(1))
            continue
        if not path or raw_line.startswith("--- ") or raw_line.startswith("\\ No newline"):
            continue
        if raw_line.startswith("+"):
            added.append((path, new_line, raw_line[1:]))
            new_line += 1
        elif raw_line.startswith(" "):
            new_line += 1
    return added


def _python_reference_is_safe(content: str, assignment: re.Match[str]) -> bool:
    """Recognize expressions, not secrets, without evaluating Python source.

    Calls are exempt only when their argument values contain no suspicious
    literals. Dictionary keys describe fields; values carry credentials.
    Unparseable diff fragments retain the conservative regex behavior.
    """
    if assignment.group("quoted") is not None:
        return False
    expression = content[assignment.start("bare"):].strip().rstrip(",")
    try:
        node = ast.parse(expression, mode="eval").body
    except (SyntaxError, ValueError):
        return False

    def safe(value: ast.AST) -> bool:
        if isinstance(value, ast.Name):
            return True
        if isinstance(value, ast.Attribute):
            return safe(value.value)
        if isinstance(value, ast.Call):
            return (safe(value.func) and all(safe(arg) for arg in value.args)
                    and all(safe(arg.value) for arg in value.keywords))
        if isinstance(value, ast.Dict):
            return all(safe(item) for item in value.values)
        if isinstance(value, (ast.List, ast.Tuple)):
            return all(safe(item) for item in value.elts)
        if isinstance(value, ast.Constant):
            return not isinstance(value.value, (str, bytes)) or not (
                _looks_like_plaintext_value(
                    value.value.decode("utf-8", errors="replace")
                    if isinstance(value.value, bytes) else value.value
                )
            )
        return False

    return isinstance(node, (ast.Name, ast.Attribute, ast.Call)) and safe(node)


def _findings(diff: str) -> list[Finding]:
    findings: list[Finding] = []
    for path, line, content in _added_lines(diff):
        for rule, pattern in (("private key", PRIVATE_KEY_RE), *KNOWN_SECRET_RES):
            if pattern.search(content):
                findings.append(Finding(path, line, rule, _redact_content(content)))
                break
        else:
            assignment_rules = (
                ("Alibaba Cloud credential-like assignment", ALIYUN_ASSIGNMENT_RE),
                ("credential-like assignment", ASSIGNMENT_RE),
            )
            for rule, assignment_pattern in assignment_rules:
                detected = False
                for assignment in assignment_pattern.finditer(content):
                    value = assignment.group("quoted") or assignment.group("bare") or ""
                    if path.endswith(".py") and (
                        _python_reference_is_safe(content, assignment)
                        or (assignment.group("quoted") is None
                            and PYTHON_ATTRIBUTE_RE.fullmatch(value))
                    ):
                        continue
                    if _looks_like_plaintext_value(value):
                        findings.append(Finding(path, line, rule, _redact_content(content)))
                        detected = True
                        break
                if detected:
                    break
    return findings


def _git_diff(base: str, head: str) -> str:
    # Use an argv list and shell=False so revision names cannot become shell
    # syntax. Git validates the revisions and reports errors to the caller.
    result = subprocess.run(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--no-color",
            "--unified=0",
            "--diff-filter=ACMRTUXB",
            base,
            head,
            "--",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "git diff failed"
        raise RuntimeError(message)
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="exclusive base revision")
    parser.add_argument("--head", required=True, help="inclusive head revision")
    args = parser.parse_args(argv)

    try:
        findings = _findings(_git_diff(args.base, args.head))
    except RuntimeError as error:
        print(f"error: plaintext secret scan could not inspect Git range: {error}", file=sys.stderr)
        return 2

    if not findings:
        print("plaintext secret scan passed")
        return 0

    print("ERROR: plaintext secret-like content detected; push blocked.", file=sys.stderr)
    print("Remove the credential, move it to a secure runtime secret source, and rotate it if it was real.", file=sys.stderr)
    for finding in findings:
        print(f"  - {finding.path}:{finding.line} ({finding.rule})", file=sys.stderr)
        # Encode control characters so source content cannot alter terminal
        # output while still showing the author the relevant added line.
        safe_content = finding.content.encode("unicode_escape").decode("ascii")
        print(f"    added line: {safe_content}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
