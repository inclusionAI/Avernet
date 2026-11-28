"""Platform acceptance doubles; no cross-app test imports."""

from avernet_evolution.core.evolution_strategy import Candidate, RunSummary

IMAGE = "registry.example.com/evolution/worker@sha256:" + "a" * 64


class FakeSandbox:
    def __init__(self) -> None:
        self.created: list = []
        self.executed: list[tuple[str, str, int]] = []
        self.destroyed: list[str] = []
        self.on_exec = None
        self.create_error: Exception | None = None

    async def create(self, spec):
        self.created.append(spec)
        if self.create_error is not None:
            raise self.create_error
        return f"sbx_{len(self.created)}"

    async def exec(self, sandbox_id: str, command: str, *, timeout_ms: int) -> int:
        self.executed.append((sandbox_id, command, timeout_ms))
        if self.on_exec is not None:
            await self.on_exec()
        return 0

    async def destroy(self, sandbox_id: str) -> None:
        self.destroyed.append(sandbox_id)


class WorkspaceOnlyStrategy:
    async def run(self, ctx):
        ws = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/r1")
        await ws.write("persona/SOUL.md", b"## Truths\n- plain language\n")
        patch = await ws.to_patch(rationale="harden")
        await ctx.candidates.submit(
            Candidate(
                patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence)
            )
        )
        return RunSummary(summary="done", metrics={"candidates": 1.0})
