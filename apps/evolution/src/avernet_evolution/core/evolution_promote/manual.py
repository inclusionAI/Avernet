"""Control-plane-only human authorization, never an evaluation result or Worker capability."""

import uuid
from datetime import UTC, datetime
from typing import Any

from avernet_genome.canonical import canonicalize, sha256_digest
from avernet_genome.models import BotRef, ProvenanceKind, RevisionStatus

from ..evolution_run.errors import IdempotencyKeyReused
from ..evolution_run.ports import GenomePort, KillSwitchesPort
from ..evolution_run.run_service import RunService
from .manual_contract import ManualDelivery
from .manual_store import ManualAuthorizationStore
from .service import LocalPromotionService, PromotionNotReady


class ManualPromotionService:
    def __init__(
        self,
        *,
        store: ManualAuthorizationStore,
        runs: RunService,
        genome: GenomePort,
        delivery: ManualDelivery,
        checks: LocalPromotionService | None,
        kills: KillSwitchesPort,
    ):
        self.store: ManualAuthorizationStore = store
        self.runs, self.genome, self.delivery = runs, genome, delivery
        self.checks, self.kills = checks, kills

    async def _check(
        self, bot: BotRef, candidate_id: str, *, run_id: str, revision: str, parent: str
    ) -> dict[str, Any]:
        run = await self.runs.get(bot, run_id)
        candidate = self.runs.store.get_candidate(run_id, candidate_id)
        if (
            run.status.value != "completed"
            or candidate is None
            or candidate.revision != revision
            or run.parent_revision != parent
        ):
            raise PromotionNotReady("authorization requires a completed Run and its exact recorded candidate")
        if (
            await self.kills.paused()
            or await self.kills.bot_frozen(bot)
            or await self.kills.strategy_disabled(run.strategy, run.strategy_version)
        ):
            raise PromotionNotReady("evolution is paused, frozen or disabled")
        target = await self.genome.resolve_revision(bot, revision)
        meta = target.revision
        if (
            meta.id != revision
            or meta.parent != parent
            or meta.status is not RevisionStatus.CANDIDATE
            or meta.created_by.kind is not ProvenanceKind.STRATEGY_RUN
            or meta.created_by.run_id != run.id
            or meta.created_by.actor != run.strategy_ref
            or meta.patch_digest != candidate_id
        ):
            raise PromotionNotReady("candidate provenance or revision status does not match the Run")
        if (await self.genome.resolve_revision(bot, "active")).revision.id != parent:
            raise PromotionNotReady("active changed; this authorization cannot override a stale parent")
        if await self.genome.drift(bot) is not None:
            raise PromotionNotReady("out_of_band_change: reconcile live drift before authorizing")
        verdict_status = "pending"
        floor_result, floor_problems = "not_run", ["no static floor scanner is configured"]
        if self.checks is not None:
            if self.checks.incident is not None and await self.checks.incident(bot):
                raise PromotionNotReady("active incident blocks human promotion")
            verdict = self.checks.verdicts.load(run_id, candidate_id, run.verification_profile)
            if verdict is not None:
                if (verdict.bot_owner_id, verdict.bot_id, verdict.revision, verdict.parent_revision) != (
                    bot.owner_id,
                    bot.bot_id,
                    revision,
                    parent,
                ):
                    raise PromotionNotReady("verdict identity mismatch")
                if not verdict.floor.passed:
                    raise PromotionNotReady(
                        "recorded floor failed; human authorization cannot override safety"
                    )
                verdict_status = verdict.status.value
                if verdict_status == "reject" or any(s.newly_failing for s in verdict.splits.values()):
                    raise PromotionNotReady(
                        "a rejected verdict or known failing evaluation cannot be overridden"
                    )
            floor = await self.checks.check_floor(bot, candidate_id)
            floor_result, floor_problems = floor.result, list(floor.problems)
            if floor.failed:
                raise PromotionNotReady("static floor failed; human authorization cannot override safety")
            gate = self.checks.store.get(run_id, candidate_id)
            if gate is not None and any(
                c.status.value == "fail" and c.name != "verdict" for c in gate.checks
            ):
                raise PromotionNotReady("known failing platform check cannot be overridden")
            review = self.checks.store.get_review(run_id, candidate_id)
            if review is not None and review.status.value in {"rejected", "superseded"}:
                raise PromotionNotReady("candidate review was rejected or superseded")
        return {"verdict": verdict_status, "floor": floor_result, "floor_problems": floor_problems}

    async def authorize(
        self,
        bot: BotRef,
        candidate_id: str,
        *,
        run_id: str,
        revision: str,
        parent: str,
        reason: str,
        actor: str,
        user_id: str,
        key: str,
    ) -> dict[str, Any]:
        if not reason.strip() or not key.strip():
            raise ValueError("human authorization requires a reason and idempotency key")
        fingerprint = sha256_digest(
            canonicalize(
                {
                    "candidate": candidate_id,
                    "run": run_id,
                    "revision": revision,
                    "parent": parent,
                    "reason": reason,
                    "actor": actor,
                    "user_id": user_id,
                }
            )
        )

        def replay():
            previous = self.store.replay(bot, key)
            if previous is not None:
                if previous[0] != fingerprint:
                    raise IdempotencyKeyReused("key reused for a different human authorization")
                return previous[1]
            return None

        previous = replay()
        if previous is not None:
            return {"authorization": previous}
        latest = self.store.get(bot, candidate_id)
        if latest is not None:
            delivery = await self.delivery.get(bot, latest["id"])
            if delivery is None or delivery["status"] != "apply_failed":
                raise PromotionNotReady("candidate already has an authorization; recover or Apply it first")
        facts = await self._check(bot, candidate_id, run_id=run_id, revision=revision, parent=parent)
        body = {
            "id": uuid.uuid4().hex,
            "basis": "human",
            "candidate_id": candidate_id,
            "run_id": run_id,
            "revision_id": revision,
            "parent_revision_id": parent,
            "strategy": (await self.runs.get(bot, run_id)).strategy_ref,
            "actor": actor,
            "user_id": user_id,
            "reason": reason.strip(),
            "authorized_at": datetime.now(UTC).isoformat(),
            "checks_at_authorization": facts,
        }
        with self.store.conn:
            self.store.conn.execute("BEGIN IMMEDIATE")
            previous = replay()
            if previous is not None:
                return {"authorization": previous}
            current = self.store.get(bot, candidate_id)
            if (current["id"] if current else None) != (latest["id"] if latest else None):
                raise PromotionNotReady("another authorization was recorded; refresh before continuing")
            self.store.save(bot, key, fingerprint, body)
            self.runs.store.events.write_human_authorized(
                run_id=run_id,
                details={
                    "authorization_id": body["id"],
                    "candidate_id": candidate_id,
                    "actor": actor,
                    "basis": "human",
                    "revision": revision,
                    "parent": parent,
                },
            )
        return {"authorization": body}

    async def state(self, bot: BotRef, candidate_id: str) -> dict[str, Any]:
        authorization = self.store.get(bot, candidate_id)
        return {
            "authorization": authorization,
            "promotion": (await self.delivery.get(bot, authorization["id"]) if authorization else None),
        }

    async def apply(
        self, bot: BotRef, candidate_id: str, *, authorization_id: str, user_id: str
    ) -> dict[str, Any]:
        authorization = self.store.get(bot, candidate_id, authorization_id)
        if authorization is None:
            raise PromotionNotReady("human authorization not found for this candidate")
        if authorization["user_id"] != user_id:
            raise PermissionError("Apply requires the operator who authorized this candidate")
        # Recover a lost response before checking active/drift (successful Apply changed both).
        previous = await self.delivery.get(bot, authorization_id)
        if previous is not None:
            return {"authorization": authorization, "promotion": previous}
        await self._check(
            bot,
            candidate_id,
            run_id=authorization["run_id"],
            revision=authorization["revision_id"],
            parent=authorization["parent_revision_id"],
        )
        promoted = await self.delivery.apply(bot, authorization)
        return {"authorization": authorization, "promotion": promoted}
