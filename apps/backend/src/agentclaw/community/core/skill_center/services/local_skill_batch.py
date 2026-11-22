"""Dispatch a bounded Local Skill batch and drain calls already underway."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
import hashlib
from typing import TypeVar

from agentclaw.community.core.ports.skill_package_upload_port import (
    LocalSkillPackageReceipt,
    LocalSkillPackageWriteResult,
    PackageMetadata,
    PackagePublication,
)
from agentclaw.community.core.skill_center.skill_package import ValidatedSkillPackage


@dataclass(frozen=True, slots=True)
class PreparedLocalSkill:
    package: ValidatedSkillPackage
    digest: str

    @classmethod
    def from_package(cls, package: ValidatedSkillPackage) -> PreparedLocalSkill:
        digest = "sha256:" + hashlib.sha256(package.canonical_zip).hexdigest()
        return cls(package, digest)


def package_result(
    name: str, digest: str | None, publication: PackagePublication,
    metadata: PackageMetadata = PackageMetadata.NOT_ATTEMPTED,
) -> LocalSkillPackageWriteResult:
    return LocalSkillPackageWriteResult(
        LocalSkillPackageReceipt(name, digest, publication, metadata)
    )


_Package = TypeVar("_Package")


async def dispatch_local_skill_batch(
    packages: Sequence[_Package],
    unattempted: Sequence[LocalSkillPackageWriteResult],
    *,
    publish: Callable[[_Package, Callable[[], bool]], Awaitable[LocalSkillPackageWriteResult]],
    window: int,
) -> tuple[LocalSkillPackageWriteResult, ...]:
    """Stop scheduling on the first incomplete result, draining other slots."""
    if not packages:
        return ()
    pending = iter(enumerate(packages))
    results = list(unattempted)
    stopped = False

    def is_stopped() -> bool:
        return stopped

    async def worker() -> None:
        nonlocal stopped
        while not stopped:
            entry = next(pending, None)
            if entry is None:
                return
            index, package = entry
            try:
                result = await publish(package, is_stopped)
            except BaseException:
                # Stop further dispatch even when a bug or cancellation
                # escapes the per-package error classification.
                stopped = True
                raise
            results[index] = result
            if result.upload is None:
                stopped = True

    workers = asyncio.gather(
        *(worker() for _ in range(min(window, len(packages)))),
        return_exceptions=True,
    )
    cancelled = False
    while not workers.done():
        try:
            await asyncio.shield(workers)
        except asyncio.CancelledError:
            # A transport may complete a thread-backed request even after its
            # awaiter is cancelled. Keep every dispatched call and lease alive.
            stopped = True
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    for error in workers.result():
        if isinstance(error, BaseException):
            raise error
    return tuple(results)
