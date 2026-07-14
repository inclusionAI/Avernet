# Tasks: Consolidate engine_overrides composition into one delivery seam

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Base branch: `dev`. Branch: `claude/engine-overrides-composition-seam-rogld5`.
> Every task must leave the full `tests/community` suite green (`pytest` under the
> `test` profile) and touch only what it names.
>
> **Behavior-pinning safety net:** `tests/community/endpoints/test_publish_per_stage_channels.py`
> asserts per-stage channel delivery on the **real BaaS HTTP payload** (not on
> mocked kwargs). It must stay green and **unmodified** across every task — it is
> the proof that the release/restart paths remain behavior-preserving.

## Task 1: Add `DeliveryArtifact` payload type + `compose` — [ ]
- **Goal:** Introduce the pure delivery-payload wrapper and the single combiner
  (restamp + overlay), with no callers yet. Additive, suite stays green.
- **Files:** `core/service_bot/services/deploy/engine_ext_stage.py`;
  `tests/community/core/service_bot/services/deploy/test_engine_ext_stage.py`
  (new, or the existing engine_ext_stage test module if present).
- **Done when:**
  - [ ] `@dataclass(frozen=True) DeliveryArtifact` with a single field
        `config_artifact: dict | None`, in `engine_ext_stage.py`.
  - [ ] `DeliveryArtifact.compose(base, stage, overrides)` returns
        `cls(apply_engine_overrides(restamp_stage(base, stage), overrides))`.
  - [ ] Unit tests: compose restamps `engine_ext.stage`; overlays channels when
        overrides present; no-ops to `config_artifact=None` for `base=None` (ARCA);
        pre-feature (`overrides=None`) → restamp only, base channels unchanged.
  - [ ] Full suite green.
- **Depends on:** —

## Task 2: Add the seam producers on `PublishExtState` — [ ]
- **Goal:** Add `compose_live` / `compose_stored` — the only place flow code reads
  `ext['config_artifact']` for delivery. Additive (old `artifact_for_stage` stays
  until Task 5), suite stays green.
- **Files:** `core/service_bot/services/publish_flow/ext_state.py`;
  `tests/community/core/service_bot/services/test_publish_flow_service.py`
  (or a focused ext_state test).
- **Done when:**
  - [ ] `compose_live(publish_record, stage) -> tuple[DeliveryArtifact, dict | None]`
        — reads `stage_overrides` (live), composes, returns `(delivery, overrides)`.
  - [ ] `compose_stored(ext, stage) -> DeliveryArtifact` — reads the stored slot
        (`(ext.get("engine_overrides_by_stage") or {}).get(stage.value)`), composes.
  - [ ] Unit tests: `compose_live` returns the applied overrides as element 2 and a
        composed payload; ARCA (no config_artifact) → `config_artifact=None`,
        overrides `None`; `compose_stored` reads the slot, tolerates JSON-null slot,
        pre-feature (no slot) → base restamped only.
  - [ ] Full suite green.
- **Depends on:** Task 1

## Task 3: Type-enforce the BaaS boundary + route all four paths through the seam — [ ]
- **Goal:** Make `BotBuildService`'s delivery methods take `delivery: DeliveryArtifact`
  and rewrite every delivery call site to obtain it from the seam. Atomic: the
  signature change and all call-site migrations land together. Rollback begins
  composing here (the #168 behavior change). Suite green at task end.
- **Files:** `services/bot_build_service.py`; `publish_flow/release_stage.py`,
  `publish_flow/eval_publish_mixin.py`, `publish_flow/restart_mixin.py`,
  `publish_flow/rollback_ops_mixin.py`;
  `tests/.../services/test_bot_build_service_teclaw_routing.py`,
  `tests/.../services/test_publish_flow_service.py`.
- **Done when:**
  - [ ] `release` / `release_async` / `upgrade` / `upgrade_async` take
        `delivery: DeliveryArtifact` (not `config_artifact`); internal
        `config_artifact = delivery.config_artifact`; teclaw guards become
        `if not delivery.config_artifact:`. Async wrappers self-forward `delivery`.
  - [ ] `release_stage.first_release` + `upgrade_release`:
        `delivery, overrides = self._ext_state.compose_live(publish_record, spec.stage)`;
        pass `delivery=delivery`; persist via `record_release_ext(engine_overrides=overrides)`
        and `persist_stage_promotion(engine_overrides=overrides)`.
  - [ ] `eval_publish_mixin`: `delivery, _ = self._ext_state.compose_live(...)`; pass
        `delivery=delivery` (keep the raw `config_artifact` read for the presence guard).
  - [ ] `restart_mixin._restart_bot_async`:
        `delivery = self._ext_state.compose_stored(publish_record.ext or {}, stage)`;
        pass `delivery=delivery` on the upgrade **and** the BOT_NOT_FOUND release fallback.
  - [ ] `rollback_ops_mixin.execute_rollback`:
        `delivery = self._ext_state.compose_stored(target_ext, PublishStage.ONLINE)`;
        pass `delivery=delivery` (keep the raw read for the presence guard).
  - [ ] `test_bot_build_service_teclaw_routing.py`: wrap `_ARTIFACT` in
        `DeliveryArtifact(_ARTIFACT)`; the no-artifact-raises cases pass
        `DeliveryArtifact(None)`; downstream `create_teclaw_bot`/`update_teclaw_bot`
        `config_artifact` assertions unchanged.
  - [ ] `test_publish_flow_service.py`: the `...await_args.kwargs["config_artifact"]`
        assertions (verify/online first-release + upgrade, restart, eval, ARCA)
        become `kwargs["delivery"].config_artifact`; `test_provider_behavior.py`
        checked and updated only if it asserts a delivery kwarg.
  - [ ] `test_execute_rollback_with_config_artifact` reworked from a raw `"s3://…"`
        string to a dict artifact asserting composed delivery
        (`delivery.config_artifact["engine_ext"]["stage"] == "release"` + stored
        online overrides overlaid).
  - [ ] `test_publish_per_stage_channels.py` still green **unmodified**.
  - [ ] Full suite green.
- **Depends on:** Task 2

## Task 4: Rollback regression coverage (folds in #168) — [ ]
- **Goal:** Lock in the newly-composed rollback behavior with unit tests mirroring
  #168, so the subsumed fix is regression-covered in this repo.
- **Files:** `tests/.../services/test_publish_flow_service.py`.
- **Done when:**
  - [ ] Test: rollback delivers the target's **stored** online overrides (card id)
        and the live `ChannelEngineOverridesReader` is **not** called (stored, not
        live — the card-A-not-B guarantee).
  - [ ] Test: target with no `engine_overrides_by_stage` → base delivered
        unchanged (restamp only, no overlay) — pre-feature backward-compat.
  - [ ] Full suite green.
- **Depends on:** Task 3

## Task 5: Remove the now-dead composition helpers — [ ]
- **Goal:** Delete the per-path composition copies the seam replaced, so there is
  one composition path and no way back to the old inline style.
- **Files:** `publish_flow/ext_state.py`, `publish_flow_service.py`.
- **Done when:**
  - [ ] `PublishExtState.artifact_for_stage` removed (folded into
        `DeliveryArtifact.compose`); no remaining callers.
  - [ ] Facade `_stage_overrides` / `_artifact_for_stage` delegators removed;
        no remaining callers (grep clean across `src/` and `tests/`).
  - [ ] Full suite green.
- **Depends on:** Task 3

## Task 6: Whole-suite verification, end-to-end check, draft PR — [ ]
- **Goal:** Prove the change end-to-end and open the PR.
- **Files:** — (verification + PR).
- **Done when:**
  - [ ] Full `tests/community` suite green under the `test` profile.
  - [ ] `verify` skill run on the rollback path (the one behavior change): drive a
        rollback and observe the composed delivery (stored online channels + `release`
        stamp) reach the BaaS payload.
  - [ ] Draft PR opened linking `Fixes #173` (and `#168`), subscribed via
        `subscribe_pr_activity`.
- **Depends on:** Tasks 3–5
