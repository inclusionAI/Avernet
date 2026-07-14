# Plan: Consolidate engine_overrides composition into one delivery seam

## Approach

Introduce a single **delivery-composition seam** with three parts:

1. **A `DeliveryArtifact` value object** — a *pure* wrapper of the composed payload
   handed to BaaS (`config_artifact` only). Lives in the `deploy` layer so both the
   seam (publish-flow) and `BotBuildService` can import it with no cycle.
2. **Two producers on `PublishExtState`** — `compose_live` (release + eval) and
   `compose_stored` (restart + rollback). These are the *only* place flow code
   reads `ext['config_artifact']` for delivery, and they make the one legitimate
   per-path choice (live channel re-fetch vs. stored per-stage slot) explicit.
3. **A type-enforced BaaS boundary** — `BotBuildService.release / release_async /
   upgrade / upgrade_async` accept `delivery: DeliveryArtifact` instead of
   `config_artifact: dict | None`, so a raw artifact is un-passable from flow code.

All four delivery call sites are rewritten to obtain a `DeliveryArtifact` from the
seam and pass it through. The restamp/overlay/store primitives (`restamp_stage`,
`apply_engine_overrides`, `store_stage_overrides`, `stamp_stage_on_stored_artifact`)
are unchanged and stay the building blocks the seam composes with. The former
combiner `PublishExtState.artifact_for_stage` is **folded into**
`DeliveryArtifact.compose` and removed, along with the dead facade delegators
(`_stage_overrides`, `_artifact_for_stage`).

Composition is behavior-preserving on the release/restart/eval paths (same source,
same primitives, just relocated behind the seam). Rollback changes: it moves from
"ship raw" to `compose_stored(target_ext, ONLINE)` — folding in #168.

**Overrides ownership (decision B).** Only the **release** path persists the applied
overlay into `engine_overrides_by_stage` (for a future restart/rollback to
reproduce). Rather than bundle that raw overlay onto the delivery payload — where
eval/restart/rollback would ignore it — `DeliveryArtifact` stays a pure payload and
the live producer hands the overrides back **only to the caller that needs them**:
`compose_live` returns `(DeliveryArtifact, overrides)`; the release path unpacks
both, eval discards the second; `compose_stored` returns a bare `DeliveryArtifact`
(restart/rollback never persist). The asymmetry lives where the real asymmetry is
(only release persists), not on the boundary type.

## The `DeliveryArtifact` type

New, in the `deploy` layer (alongside the composition vocabulary it belongs to). A
pure payload wrapper whose `compose` classmethod is the single combiner (restamp +
overlay), so the raw `overrides` is passed exactly once:

```python
@dataclass(frozen=True)
class DeliveryArtifact:
    """The composed artifact handed to the BaaS delivery boundary for one stage.

    ``config_artifact`` is the fully composed payload — engine_ext.stage restamped
    to the target stage and that stage's DingTalk channel engine_overrides overlaid.
    It is the ONLY thing flow code may hand to BotBuildService.release/upgrade, and a
    DeliveryArtifact is obtainable only via ``compose`` (from the PublishExtState
    seam). ``None`` for the ARCA mount path (no config_artifact)."""
    config_artifact: dict | None

    @classmethod
    def compose(cls, base, stage, overrides) -> "DeliveryArtifact":
        """Restamp engine_ext.stage for ``stage`` and overlay ``overrides``' channels.
        No-ops (payload stays ``base``) for ARCA / no stored overrides."""
        return cls(apply_engine_overrides(restamp_stage(base, stage), overrides))
```

**Placement decision:** `services/deploy/engine_ext_stage.py`. That module already
owns the delivery vocabulary this type belongs to (`restamp_stage`,
`apply_engine_overrides`, the `PublishStage → engine stage-string` map), and
`bot_build_service` already imports from `deploy/` (`provider_resolver`), so this
introduces no new cross-layer dependency and no import cycle (publish-flow →
bot_build_service → deploy; deploy imports neither).

## The seam (producers on `PublishExtState`)

```python
def compose_live(self, publish_record, stage) -> tuple[DeliveryArtifact, dict | None]:
    """LIVE re-fetch of the stage's channels (release + eval). Returns the composed
    payload AND the raw overrides applied, so the release path can persist them
    without a second read; eval discards the second element."""
    overrides = self.stage_overrides(publish_record, stage)
    base = (publish_record.ext or {}).get("config_artifact")
    return DeliveryArtifact.compose(base, stage, overrides), overrides

def compose_stored(self, ext, stage) -> DeliveryArtifact:
    """STORED per-stage slot (restart + rollback) — reproduce what was promoted.
    These paths only read the slot; they never persist, so no overrides handback."""
    overrides = (ext.get("engine_overrides_by_stage") or {}).get(stage.value)
    return DeliveryArtifact.compose(ext.get("config_artifact"), stage, overrides)
```

- `compose_live` preserves the exact logic release/eval use today (`stage_overrides`
  already returns `None` for ARCA → `compose(None, …)` → `config_artifact=None`).
- `compose_stored` preserves restart's exact logic today
  (`restart_mixin.py:245‑248`), including the `or {}` guard against a JSON-null slot.
- Both yield `config_artifact=None` for ARCA — the boundary's teclaw guard
  (`if not delivery.config_artifact`) is only reached on the teclaw branch, so ARCA
  (non-teclaw) ignores it exactly as today.

## The BaaS boundary retype

`BotBuildService` (`bot_build_service.py`) — the four delivery methods swap
`config_artifact: Optional[Dict]` for `delivery: DeliveryArtifact` and read
`delivery.config_artifact` internally:

- `release(…, delivery: DeliveryArtifact, ext_info=…)` — `release:428`; internal
  `config_artifact = delivery.config_artifact`; teclaw guard becomes
  `if not delivery.config_artifact:` (`release:491`).
- `release_async(…, delivery, ext_info=…)` — `release_async:582`; forwards `delivery`.
- `upgrade(…, delivery: DeliveryArtifact)` — `upgrade:1057`; same internal swap +
  guard (`upgrade:1098`).
- `upgrade_async(…, delivery)` — `upgrade_async:1227`; forwards `delivery`.

No internal callers of the sync `release`/`upgrade` exist (verified), so the only
callers to update are the async wrappers (self-forward) and the flow call sites.

## Key files & functions

| File | Change |
|---|---|
| `services/deploy/engine_ext_stage.py` | **Add** `DeliveryArtifact` (pure `config_artifact` wrapper + `compose` classmethod, folding in the former `artifact_for_stage`). |
| `publish_flow/ext_state.py` | **Add** `compose_live` (→ `(DeliveryArtifact, overrides)`) / `compose_stored` (→ `DeliveryArtifact`); import `DeliveryArtifact`. **Remove** `artifact_for_stage` (folded into `compose`). `stage_overrides` / `store_stage_overrides` / `stamp_stage_on_stored_artifact` unchanged. |
| `services/bot_build_service.py` | Retype `release`/`release_async`/`upgrade`/`upgrade_async` to `delivery: DeliveryArtifact`; internal `delivery.config_artifact`; guards. |
| `publish_flow/release_stage.py` | `first_release` + `upgrade_release`: `delivery, overrides = ext_state.compose_live(...)`; pass `delivery`; persist `overrides`. Drop the `PublishExtState.artifact_for_stage` static call. |
| `publish_flow/eval_publish_mixin.py` | `delivery, _ = self._ext_state.compose_live(...)`; pass `delivery`. (Keep the raw `config_artifact` read for the build-artifact presence guard only.) |
| `publish_flow/restart_mixin.py` | `delivery = self._ext_state.compose_stored(publish_record.ext or {}, stage)`; pass `delivery` on both the upgrade and the BOT_NOT_FOUND release fallback. |
| `publish_flow/rollback_ops_mixin.py` | `delivery = self._ext_state.compose_stored(target_ext, PublishStage.ONLINE)`; pass `delivery`. (Keep the raw read for the presence guard only.) **Behavior change (#168).** |
| `publish_flow_service.py` | **Remove** dead `_stage_overrides` / `_artifact_for_stage` delegators. |

`persist_stage_promotion` / `record_release_ext` / `store_stage_overrides` /
`stamp_stage_on_stored_artifact` are unchanged — the release path still passes the
stage overrides to persistence, now via the second element of `compose_live`.

## Data model changes

None. The `ext` JSON keys (`config_artifact`, `engine_overrides_by_stage`,
`binding`, `publish`, …) written and read are unchanged. No migration.

## Test strategy

- **`tests/community/endpoints/test_publish_per_stage_channels.py`** — the #168-style
  end-to-end proof; asserts on the real BaaS HTTP payload (`_delivered_artifact`
  digs into the create call's JSON), not on kwargs. **Unchanged, must stay green** —
  the strongest evidence the release/restart paths are behavior-preserving.
- **`tests/…/services/test_publish_flow_service.py`** — the many
  `…await_args.kwargs["config_artifact"]` assertions become
  `kwargs["delivery"].config_artifact` (verify/online first-release + upgrade,
  restart, eval, ARCA). Mechanical, value-preserving.
  - `test_execute_rollback_with_config_artifact` — currently asserts a raw
    `"s3://…"` string passes through; **reworked** to a dict artifact asserting
    composed delivery (`engine_ext.stage == "release"` + stored online overrides
    overlaid).
  - **Add** rollback unit coverage mirroring #168: (a) stored online overrides are
    delivered + reader not called (stored, not live); (b) target with no
    `engine_overrides_by_stage` → base delivered unchanged (restamp only).
- **`tests/…/services/test_bot_build_service_teclaw_routing.py`** — calls sync
  `release`/`upgrade` directly: wrap `_ARTIFACT` in `DeliveryArtifact(_ARTIFACT, …)`;
  the no-artifact-raises cases pass `DeliveryArtifact(None, None)`. Assertions on the
  downstream `create_teclaw_bot`/`update_teclaw_bot` `config_artifact` kwarg unchanged.
- **`test_provider_behavior.py`** — check for any `config_artifact` kwarg on delivery
  mocks; update if present (persist-side is untouched, so likely no change).
- Full `tests/community` suite green before push; run the verify skill on the
  rollback path (the one behavior change) to observe composed delivery end-to-end.

## Risks & mitigations

- **Broad but mechanical test churn** from renaming the boundary kwarg. Mitigation:
  the value assertions are preserved; the end-to-end payload test is untouched and
  pins real behavior.
- **Rollback behavior change** could surprise if a caller depended on raw delivery.
  Mitigation: this is the #168 fix, approved; realistic online artifacts are already
  `stage=release`, so the restamp is idempotent and only the (previously dropped)
  channel overlay is added.
- **Layer placement of `DeliveryArtifact`.** Mitigation: placed in `deploy/`, the
  layer both sides already depend on downward — no cycle.

## Alternatives considered

- **Convention-only seam** (call sites pass `delivery.config_artifact`, BaaS keeps
  its dict signature). Rejected in issue #173: does not make raw un-passable.
- **Compose inside `release_async`/`upgrade_async`** (boundary takes
  `(base, stage, overrides_source)`). Rejected: pushes the live-vs-stored channel
  decision and the channel reader dependency down into `BotBuildService`, widening
  its responsibilities; the seam-on-`PublishExtState` keeps that in the flow layer.
