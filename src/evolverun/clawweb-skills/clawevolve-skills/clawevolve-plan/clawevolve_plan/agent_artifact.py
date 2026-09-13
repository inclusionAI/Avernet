from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from .io import atomic_write_json, atomic_write_text

ValidatedT = TypeVar("ValidatedT")


@dataclass(frozen=True)
class StructuredArtifactResult(Generic[ValidatedT]):
    validated: ValidatedT
    payload: Any
    source: str
    final_path: Path
    warnings: tuple[str, ...] = ()


class StructuredArtifactError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        attempt: int,
        archive_paths: tuple[Path, ...] = (),
        failures: tuple[str, ...] = (),
        repair_source_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.attempt = attempt
        self.archive_paths = archive_paths
        self.failures = failures
        self.repair_source_path = repair_source_path


def materialize_structured_artifact(
    *,
    label: str,
    candidate_path: Path,
    final_path: Path,
    response_text: str,
    validator: Callable[[Any], ValidatedT],
    canonicalizer: Callable[[ValidatedT], Any],
    attempt: int,
) -> StructuredArtifactResult[ValidatedT]:
    """Validate one agent-produced JSON artifact and atomically publish it.

    The candidate file is preferred because it is the explicit agent contract.
    A structured response is accepted as a recovery source when the file is
    absent or malformed. Only validator-approved data reaches ``final_path``.
    """

    sources: list[tuple[str, str, bool]] = []
    if candidate_path.is_file():
        sources.append(
            (
                "candidate_file",
                candidate_path.read_text(encoding="utf-8", errors="replace"),
                False,
            )
        )
    response = str(response_text or "").strip()
    if response and all(response != text.strip() for _, text, _ in sources):
        sources.append(("agent_response", response, True))

    if not sources:
        raise StructuredArtifactError(
            f"{label} agent produced neither a candidate file nor a JSON response",
            attempt=attempt,
        )

    failures: list[str] = []
    warnings: list[str] = []
    failed_sources: list[tuple[str, str, bool]] = []
    for source_name, text, allow_embedded in sources:
        try:
            payload = parse_json_artifact(text, allow_embedded=allow_embedded)
            validated = validator(payload)
            canonical_payload = canonicalizer(validated)
            if failed_sources:
                archived = _archive_invalid_sources(
                    final_path=final_path,
                    attempt=attempt,
                    sources=failed_sources,
                )
                warnings.append(
                    f"{label} ignored invalid earlier artifact source(s); archived at "
                    + ", ".join(str(path) for path in archived)
                )
            atomic_write_json(final_path, canonical_payload)
            if source_name != "candidate_file":
                warnings.append(
                    f"{label} recovered from agent response because the candidate file was unavailable or invalid"
                )
            return StructuredArtifactResult(
                validated=validated,
                payload=canonical_payload,
                source=source_name,
                final_path=final_path,
                warnings=tuple(warnings),
            )
        except Exception as exc:  # noqa: BLE001 - retain all candidate diagnostics.
            failures.append(f"{source_name}: {_format_validation_error(exc, text)}")
            failed_sources.append((source_name, text, allow_embedded))

    archive_paths = _archive_invalid_sources(
        final_path=final_path,
        attempt=attempt,
        sources=failed_sources,
    )
    repair_source_path = _prepare_repair_source(
        candidate_path=candidate_path,
        sources=failed_sources,
    )
    archive_text = ", ".join(str(path) for path in archive_paths) or "none"
    raise StructuredArtifactError(
        f"{label} artifact remained invalid on attempt {attempt}: "
        + " | ".join(failures)
        + f"; invalid_artifacts={archive_text}",
        attempt=attempt,
        archive_paths=archive_paths,
        failures=tuple(failures),
        repair_source_path=repair_source_path,
    )


def parse_json_artifact(text: str, *, allow_embedded: bool = False) -> Any:
    raw = str(text or "").lstrip("\ufeff").strip()
    if not raw:
        raise ValueError("artifact is empty")

    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced:
        raw = fenced.group(1).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as strict_error:
        if not allow_embedded:
            raise strict_error

    decoder = json.JSONDecoder()
    starts = [index for index, character in enumerate(raw) if character in "{["]
    last_error: json.JSONDecodeError | None = None
    for start in starts:
        try:
            value, end = decoder.raw_decode(raw, start)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        trailing = raw[end:].strip()
        if trailing in {"", "```"}:
            return value
    if last_error is not None:
        raise last_error
    raise ValueError("artifact response does not contain one complete JSON value")


def build_structured_artifact_correction_prompt(
    *,
    label: str,
    candidate_path: Path,
    final_path: Path,
    validation_error: str,
    schema_example: Any,
    semantic_constraints: list[str],
    response_only: bool = False,
) -> str:
    constraints = "\n".join(f"- {item}" for item in semantic_constraints)
    candidate_label = "Candidate path to inspect and replace"
    output_rules = """- Build a Python dict/list and write it with json.dump(..., ensure_ascii=False, indent=2).
- Do not use shell heredoc or hand-built escaped JSON strings.
- After writing, reopen the candidate with json.load() and only finish if it parses.
- Write exactly one JSON value to the candidate path. Do not wrap it in markdown."""
    finish = f"Rewrite {candidate_path}, validate it with Python json.load(), and stop immediately."
    if response_only:
        candidate_label = "Previous candidate path (read-only if present)"
        output_rules = "- Return one complete JSON object as the final response, without markdown or commentary. Do not write files or generate/execute scripts; the caller parses, validates and saves the response."
        finish = "Return the corrected JSON and stop immediately."
    return f"""You are repairing one {label} structured artifact for clawevolve-plan.

This is the only correction attempt. Do not repeat workspace discovery, expand scope,
run tests, access the network, or change any workspace business file.

{candidate_label}: {candidate_path}
Canonical path reserved for the Plan process; do not write it: {final_path}
Validation failure:
{validation_error[:4000]}

Correction requirements:
- Read the existing candidate when present and preserve its supported findings and meaning.
- Correct only JSON syntax, required schema fields, references, and serialization.
- Do not invent inspected files, evidence, cases, targets, or business facts.
{output_rules}
{constraints}

Canonical schema example:
{json.dumps(schema_example, ensure_ascii=False, indent=2)}

{finish}
"""


def _prepare_repair_source(
    *,
    candidate_path: Path,
    sources: list[tuple[str, str, bool]],
) -> Path | None:
    if candidate_path.is_file():
        return candidate_path
    for source_name, text, _allow_embedded in sources:
        if source_name == "agent_response" and text.strip():
            atomic_write_text(candidate_path, text.rstrip() + "\n")
            return candidate_path
    return None


def _archive_invalid_sources(
    *,
    final_path: Path,
    attempt: int,
    sources: list[tuple[str, str, bool]],
) -> tuple[Path, ...]:
    archives: list[Path] = []
    for source_name, text, _allow_embedded in sources:
        suffix = "json" if source_name == "candidate_file" else "response.txt"
        archive = _available_archive_path(
            final_path.with_name(
                f"{final_path.stem}.invalid.attempt-{attempt}.{suffix}"
            )
        )
        atomic_write_text(archive, text.rstrip() + "\n")
        archives.append(archive)
    return tuple(archives)


def quarantine_stale_artifact(path: Path, *, reason: str) -> Path | None:
    """Move an untrusted artifact aside so a new agent run cannot reuse it."""

    if not path.exists():
        return None
    suffix = path.suffix or ".artifact"
    archive = _available_archive_path(
        path.with_name(f"{path.stem}.invalid.{reason}{suffix}")
    )
    path.replace(archive)
    return archive


def _available_archive_path(preferred: Path) -> Path:
    if not preferred.exists():
        return preferred
    for index in range(2, 10_000):
        candidate = preferred.with_name(f"{preferred.stem}-{index}{preferred.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot allocate archive path for {preferred}")


def _format_validation_error(exc: Exception, text: str) -> str:
    if isinstance(exc, json.JSONDecodeError):
        lines = str(text or "").splitlines()
        line = lines[exc.lineno - 1] if 0 < exc.lineno <= len(lines) else ""
        start = max(0, exc.colno - 81)
        end = min(len(line), exc.colno + 80)
        excerpt = line[start:end].replace("\t", " ")
        pointer = " " * max(0, exc.colno - 1 - start) + "^"
        return (
            f"JSONDecodeError: {exc.msg} at line {exc.lineno} column {exc.colno} "
            f"(char {exc.pos}); context={excerpt!r}; pointer={pointer!r}"
        )
    return f"{type(exc).__name__}: {exc}"
