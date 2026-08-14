# Plan: stored-slot override composition on the rollback delivery path

Translation of the approved plan in
[#168 (comment)](https://github.com/inclusionAI/Avernet/issues/168#issuecomment-4968630365).
A validated implementation exists as a working patch (red-on-HEAD / green-with-fix
verified); this plan documents it.

## Root cause recap

Three delivery paths compose the delivered artifact inconsistently:

| Path | Overrides used | Where |
|---|---|---|
| Release | live re-fetch + `_artifact_for_stage` | `publish_flow/release_stage.py` |
| Restart | stored `ext.engine_overrides_by_stage[stage]` + `_artifact_for_stage` | `publish_flow/restart_mixin.py:245-248` |
| **Rollback** | **none — raw artifact** | `publish_flow/rollback_ops_mixin.py:76,114` |

Not a regression: the pre-#105 monolith delivered the rollback artifact raw
identically; #105 added stored-slot composition to release/restart only.

## Change

### 1. `publish_flow/rollback_ops_mixin.py` — `execute_rollback`

Before the `upgrade_async` call (after the bot lookup), mirror restart:

```python
stored_overrides = (target_ext.get("engine_overrides_by_stage") or {}).get(
    PublishStage.ONLINE.value
)
config_artifact = self._artifact_for_stage(
    config_artifact, PublishStage.ONLINE, stored_overrides
)
```

- Stored slot (what the target version promoted), **never** a live re-fetch.
- `or {}` tolerates a JSON-null `engine_overrides_by_stage` in a raw ext blob
  (same guard as restart).
- Pre-feature record → `stored_overrides is None` → `apply_engine_overrides`
  no-ops → raw artifact unchanged.
- ARCA → `config_artifact is None` → `_artifact_for_stage` no-ops.
- `restamp_stage` to `engine_ext.stage="release"` is benign (a previously-online
  artifact already carries it) and makes the three paths uniform.
- The composed artifact is delivery-only; `target_ext["config_artifact"]`
  persisted at step 6 stays the raw stored snapshot (helpers never mutate input).

### 2. Tests — `tests/community/core/service_bot/services/test_publish_flow_service.py`

- **New** `test_execute_rollback_delivers_stored_online_overrides_not_live`:
  target ext stores `verify`+`online` slots, base artifact carries stale draft
  channels, a live reader mock would return wrong channels. Assert delivered
  `engine_overrides` == stored online slot (card A), `engine_ext.stage ==
  "release"`, and `reader.overrides_for_stage` not called.
- **Rework** `test_execute_rollback_with_config_artifact` into the backward-compat
  case: its artifact was an opaque string (`"s3://…"`), which cannot flow through
  `_artifact_for_stage` (dict-typed, same contract restart already imposes) —
  becomes a dict; assert delivered unchanged.

### 3. Tests — `tests/community/endpoints/test_service_bot_rollback.py`

New scenario `rollback_delivers_stored_online_channel_overrides` (pattern from
`test_publish_per_stage_channels.py`): V1 ext stores online overrides with
`card_template_id=card-A`; live channel table row holds `card-B`. Rollback V2 →
assert the BaaS `/update` payload's
`config.deploy_config.teclaw_bot_config.engine_overrides` carries card A and
`engine_ext.stage == "release"`; V1 lands at SUCCESS.

## Risks / edge cases

- Stored artifact with un-enriched `engine_ext` (no `stage` key): `restamp_stage`
  no-ops by design; overlay still applies.
- `engine_overrides_by_stage` present but missing the `online` key: `.get` →
  `None` → no-op (a never-promoted-online target cannot be a rollback target
  anyway — it must have held an online binding).
- Existing rollback scenarios (`_ARTIFACT` with `engine_ext: {}`, no stored slots)
  are unaffected → proves no behavior change outside the new path.

## Verification

Targeted: the three touched test scopes. Full: `tests/community` suite.
Delivery: commit on `claude/jolly-ptolemy-86ti1s`, push with
`AVERNET_PRE_PUSH_MERGE_TARGET=origin/REL20260715`, draft PR → `REL20260715`
(`Fixes #168`), then `subscribe_pr_activity`.
