"""In-process strategy platform for tests and local runs (03-strategy.zh-CN.md §8.4).

Everything here is a *test* implementation of the platform side, so a strategy can
be exercised without `apps/evolution`. It never touches a live bot:

* ``StaticRevision``  — a frozen parent revision (files + spec)
* ``LocalWorkspace`` — copy-on-write sandbox; ``to_patch()`` turns edits into a GenomePatch
* ``LocalCandidates`` — records candidates, returns content-hash ids, serves verdicts
* ``build_context``  — assembles a StrategyContext whose ungranted capabilities refuse

Capabilities that need a real provider (models, agents, evaluation, experience) raise
``CapabilityNotGranted`` unless granted, and ``NotImplementedError`` when granted but
no provider was supplied — the strategy still type-checks against the real port.
"""

from __future__ import annotations

import asyncio
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from avernet_genome.canonical import canonicalize, sha256_digest
from avernet_genome.patch_models import GenomePatch
from avernet_genome.text_diff import unified_diff

from .errors import BudgetExhausted, CapabilityNotGranted
from .models import (
    Budget,
    Candidate,
    Completion,
    Operation,
    OperationStatus,
    RunSummary,
    Verdict,
    VerdictStatus,
)

PERSONA_PREFIX = "persona/"
SKILL_PREFIX = "skills/"
RESOURCE_PREFIX = "resources/"


def candidate_id_of(candidate: Candidate) -> str:
    """The candidate id: content hash of the patch (03-strategy.zh-CN.md §2.6)."""
    return sha256_digest(canonicalize(candidate.patch.to_json()))


@dataclass(frozen=True)
class StaticRevision:
    """A frozen parent revision: the read-only ``ctx.parent`` view."""

    id: str
    seq: str
    files: dict[str, str] = field(default_factory=dict)
    spec: dict[str, Any] = field(default_factory=dict)

    async def read(self, path: str) -> bytes:
        try:
            return self.files[path].encode("utf-8")
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    async def lineage(self, depth: int = 10) -> list[str]:
        return [self.id][:depth]


class LocalWorkspace:
    """Copy-on-write sandbox of one revision; edits stay here until ``to_patch``."""

    def __init__(self, base: StaticRevision, workspace_id: str | None = None) -> None:
        self.id = workspace_id or f"ws_{secrets.token_hex(4)}"
        self.base = base.id
        self._base_files = {path: text.encode("utf-8") for path, text in base.files.items()}
        self._files = dict(self._base_files)

    async def read(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    async def write(self, path: str, content: bytes) -> None:
        self._files[path] = content if isinstance(content, bytes) else content.encode("utf-8")

    async def to_patch(self, *, rationale: str, evidence: tuple[str, ...] = ()) -> GenomePatch:
        ops: list[dict[str, Any]] = []
        added = sorted(set(self._files) - set(self._base_files))
        removed = sorted(set(self._base_files) - set(self._files))
        changed = sorted(
            p for p in set(self._files) & set(self._base_files) if self._files[p] != self._base_files[p]
        )

        added_skills: dict[str, dict[str, str]] = {}
        new_resources: list[str] = []
        for path in added:
            if path.startswith(PERSONA_PREFIX):
                ops.append(
                    {
                        "op": "persona.add",
                        "type": path[len(PERSONA_PREFIX) :],
                        "content": self._files[path].decode("utf-8"),
                    }
                )
                continue
            if path.startswith(SKILL_PREFIX):
                relative = path[len(SKILL_PREFIX) :]
                name = relative.split("/", 1)[0]
                added_skills.setdefault(name, {})[relative] = self._files[path].decode("utf-8")
                continue
            if path.startswith(RESOURCE_PREFIX):
                new_resources.append(path)
                continue
            raise NotImplementedError(f"local to_patch cannot add {path}")
        for path in removed:
            if path.startswith(PERSONA_PREFIX):
                ops.append({"op": "persona.remove", "type": path[len(PERSONA_PREFIX) :]})
                continue
            if path.startswith(RESOURCE_PREFIX):
                ops.append({"op": "resource.remove", "path": path})
                continue
            raise NotImplementedError(f"local to_patch cannot remove {path}")
        base_skill_names = {
            path[len(SKILL_PREFIX) :].split("/", 1)[0]
            for path in self._base_files
            if path.startswith(SKILL_PREFIX)
        }
        for name, files in added_skills.items():
            ops.append(
                {
                    "op": "skill.add" if name not in base_skill_names else "skill.update",
                    "name": name,
                    "files": files,
                }
            )
        for path in new_resources:
            ops.append(
                {
                    "op": "resource.add",
                    "path": path,
                    "content": self._files[path].decode("utf-8"),
                }
            )
        for path in changed:
            before = self._base_files[path].decode("utf-8")
            after = self._files[path].decode("utf-8")
            if path.startswith(SKILL_PREFIX):
                relative = path[len(SKILL_PREFIX) :]
                ops.append(
                    {
                        "op": "skill.update",
                        "name": relative.split("/", 1)[0],
                        "file_diffs": [{"path": relative, "diff": unified_diff(before, after)}],
                    }
                )
                continue
            if path.startswith((PERSONA_PREFIX, RESOURCE_PREFIX)):
                ops.append(
                    {
                        "op": "file.edit",
                        "target": path,
                        "edits": [{"kind": "diff", "diff": unified_diff(before, after)}],
                    }
                )
                continue
            raise NotImplementedError(f"local to_patch cannot edit {path}")

        if not ops:
            raise ValueError("workspace unchanged: nothing to turn into a patch")
        return GenomePatch.model_validate(
            {
                "patch_schema": 1,
                "base": self.base,
                "ops": ops,
                "rationale": rationale,
                "evidence": list(evidence),
            }
        )


class LocalWorkspaceFactory:
    """``materialise(revision, key)`` idempotent per key, as the platform promises."""

    def __init__(self, base: StaticRevision) -> None:
        self._base = base
        self._by_key: dict[str, LocalWorkspace] = {}

    async def materialise(self, revision: str, *, key: str) -> LocalWorkspace:
        if key not in self._by_key:
            self._by_key[key] = LocalWorkspace(self._base, workspace_id=f"ws_{key}")
        return self._by_key[key]


class LocalCandidates:
    def __init__(self) -> None:
        self.submitted: dict[str, Candidate] = {}
        self.verdicts: dict[str, Verdict] = {}

    async def submit(self, candidate: Candidate) -> str:
        cid = candidate_id_of(candidate)
        self.submitted[cid] = candidate
        return cid

    async def verdict(self, candidate_id: str) -> Verdict:
        return self.verdicts.get(candidate_id, Verdict(candidate=candidate_id, status=VerdictStatus.PENDING))

    def decide(self, candidate_id: str, verdict: Verdict) -> None:
        if verdict.candidate != candidate_id:
            raise ValueError("verdict.candidate must be the candidate id")
        self.verdicts[candidate_id] = verdict


class LocalBudgetMeter:
    def __init__(self, budget: Budget) -> None:
        self._initial = budget
        self._spent_usd = budget.spent_usd
        self._spent_tokens = budget.tokens

    def remaining(self) -> Budget:
        return Budget(
            max_usd=self._initial.max_usd,
            max_wall_clock_s=self._initial.max_wall_clock_s,
            max_rollouts=self._initial.max_rollouts,
            max_tokens=self._initial.max_tokens,
            spent_usd=self._spent_usd,
            tokens=self._spent_tokens,
        )

    async def charge(self, usd: float, reason: str) -> None:
        if self._spent_usd + usd > self._initial.max_usd:
            raise BudgetExhausted(f"charging {usd} for {reason!r} would exceed the run budget")
        self._spent_usd += usd


class LocalRunLog:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, dict[str, object]]] = []

    async def info(self, message: str, **fields: object) -> None:
        self.entries.append(("info", message, fields))

    async def warn(self, message: str, **fields: object) -> None:
        self.entries.append(("warn", message, fields))


class LocalArtifacts:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def put(self, name: str, content: bytes) -> str:
        artifact_id = f"art_{secrets.token_hex(4)}"
        self.blobs[artifact_id] = content
        return artifact_id


class LocalCancellation:
    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled

    def is_set(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


class LocalOperations:
    def __init__(self) -> None:
        self.ops: dict[str, Operation] = {}
        self._seq: dict[str, int] = defaultdict(int)

    def record(self, op: Operation) -> None:
        self.ops[op.id] = op

    async def get(self, op_id: str) -> Operation:
        try:
            return self.ops[op_id]
        except KeyError as exc:
            raise KeyError(op_id) from exc

    async def cancel(self, op_id: str) -> None:
        op = await self.get(op_id)
        self.ops[op_id] = Operation(
            id=op.id,
            kind=op.kind,
            status=OperationStatus.CANCELLED,
            result=op.result,
            error=op.error,
            cost_usd=op.cost_usd,
        )

    async def wait(self, op_id: str, *, poll_s: float = 5) -> Operation:
        while True:
            op = await self.get(op_id)
            if op.status in (
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
                OperationStatus.CANCELLED,
            ):
                return op
            await asyncio.sleep(poll_s)


class _Unavailable:
    """Stands in for a capability with no local provider.

    Ungranted: ``CapabilityNotGranted`` (the platform's refusal, §4.3).
    Granted: ``NotImplementedError`` — the run may use it, but this local platform
    has no provider, so a conformance check reports it as skipped rather than failed.
    """

    _capability = "unknown"

    def __init__(self, granted: bool) -> None:
        self._granted = granted

    def _refuse(self) -> None:
        if self._granted:
            raise NotImplementedError(f"no local provider for {self._capability}")
        raise CapabilityNotGranted(self._capability)


class _UnavailableModels(_Unavailable):
    _capability = "models@1"

    async def complete(
        self, *, messages: list[Any], model: str | None = None, max_tokens: int = 4096
    ) -> Completion:
        self._refuse()


class _UnavailableExperience(_Unavailable):
    _capability = "experience.sessions@1"

    async def sessions(self, *, days: int, limit: int = 500, revision: str | None = None) -> list[Any]:
        self._refuse()

    async def feedback(self, *, days: int, kinds: list[str] | None = None, limit: int = 500) -> list[Any]:
        self._refuse()


class _UnavailableAgents(_Unavailable):
    _capability = "agents@1"

    async def start(
        self,
        definition: str,
        *,
        workspace: Any,
        prompt: str,
        idempotency_key: str,
        timeout_s: int = 1800,
    ) -> str:
        self._refuse()


class _UnavailableEvaluation(_Unavailable):
    _capability = "evaluation.train@1"

    async def add_train_cases(self, cases: list[dict[str, Any]]) -> list[str]:
        self._refuse()

    async def start_train(self, workspace: Any, *, idempotency_key: str) -> str:
        self._refuse()


def _models(granted: list[str]) -> Any:
    return _UnavailableModels("models@1" in granted)


def _experience(granted: list[str]) -> Any:
    return _UnavailableExperience("experience.sessions@1" in granted or "experience.feedback@1" in granted)


def _agents(granted: list[str]) -> Any:
    return _UnavailableAgents("agents@1" in granted)


def _evaluation(granted: list[str]) -> Any:
    return _UnavailableEvaluation("evaluation.train@1" in granted)


@dataclass
class LocalContext:
    """The ``StrategyContext`` a local run hands to a strategy."""

    run_id: str
    params: dict[str, Any]
    attempt: int
    granted: list[str]
    parent: StaticRevision
    workspace: LocalWorkspaceFactory
    budget: LocalBudgetMeter
    log: LocalRunLog
    artifacts: LocalArtifacts
    cancelled: LocalCancellation
    operations: LocalOperations
    candidates: LocalCandidates
    models: Any
    experience: Any
    agents: Any
    evaluation: Any

    def requires(self, capability: str) -> None:
        if capability not in self.granted:
            raise CapabilityNotGranted(capability)


def build_context(
    *,
    parent: StaticRevision,
    run_id: str = "run_local",
    params: dict[str, Any] | None = None,
    attempt: int = 1,
    granted: list[str] | None = None,
    budget: Budget | None = None,
    models: Any | None = None,
    experience: Any | None = None,
    agents: Any | None = None,
    evaluation: Any | None = None,
    workspace: Any | None = None,
    candidates: Any | None = None,
    operations: Any | None = None,
    log: Any | None = None,
    artifacts: Any | None = None,
) -> LocalContext:
    """Assemble a local context; undeclared capabilities refuse on use."""

    granted = list(granted or [])
    return LocalContext(
        run_id=run_id,
        params=dict(params or {}),
        attempt=attempt,
        granted=granted,
        parent=parent,
        workspace=workspace or LocalWorkspaceFactory(parent),
        budget=LocalBudgetMeter(budget or Budget(max_usd=100.0, max_wall_clock_s=7200)),
        log=log or LocalRunLog(),
        artifacts=artifacts or LocalArtifacts(),
        cancelled=LocalCancellation(),
        operations=operations or LocalOperations(),
        candidates=candidates or LocalCandidates(),
        models=models or _models(granted),
        experience=experience or _experience(granted),
        agents=agents or _agents(granted),
        evaluation=evaluation or _evaluation(granted),
    )


def run_once(strategy: Any, ctx: LocalContext) -> RunSummary:
    """Dispatch one strategy dispatch in this process (test helper)."""

    summary = asyncio.run(strategy.run(ctx))
    if not isinstance(summary, RunSummary):
        raise TypeError(f"strategy.run returned {type(summary)!r}, expected RunSummary")
    return summary
