# Plan: Per-Bot Startup Script

## Approach

Add a **second lifecycle stage** rather than extending the existing one. The
platform's `after_create_cmd_hook` keeps carrying bootstrap → engine → service →
watchdog exactly as today; the bot's own script becomes a separate
`startup_script` on the same `DeployConfig`, dispatched by BaaS *after* a device
reaches `ACTIVE`, reporting to its own callback that never touches publish state.
Whether a provider can run it becomes a declared property of `PaasService`
instead of a `provider_type` string comparison in the device service, so the
public API can answer "unsupported" instead of silently doing nothing. Avernet
stays the source of truth for the script body; BaaS persists it per device in
`extra_config` (as it already does for the platform hook) and re-dispatches it on
every start path, including the native-restart platforms that skip it today.

## Affected Components

- `src/backend/.../adapters/http/openapi_v1/bots/router.py` — 4 new public endpoints
- `src/backend/.../adapters/http/openapi_v1/bots/schemas.py` — request/response models
- `src/backend/.../core/bot_startup_script/` — **new** core service, repository, DDL
- `src/backend/.../api/bot_startup_script_service.py` — **new** Service API Protocol
- `src/backend/.../core/service_bot/services/baas_service.py:724` — pass script to BaaS on create/restart
- `src/baas/.../api/device_manage/_deploy_config.py:423` — new `DeployConfig` fields
- `src/baas/.../api/paas/_protocols.py:32` — capability on `PaasService`
- `src/baas/.../core/service/paas/_start_hook_dispatcher.py` — parameterize stage + callback target
- `src/baas/.../core/service/device_manage/_device_service.py` — single dispatch seam, incl. `_native_restart_device:322`
- `src/baas/.../core/service/device_startup_script/` — **new** run store + stage service
- `src/baas/.../adapters/web/routers/bot_service/` — script write-through + run query + callback
- `src/gateway/configs/schemas/bots.openapi.json` — regenerated artifact

## Data Model Changes

```sql
-- src/backend/src/agentclaw/community/core/bot_startup_script/sql/2026_08_10_bot_startup_script.sql
CREATE TABLE `ac_bot_startup_script` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `env`           VARCHAR(20)  NOT NULL,
  `bot_id`        VARCHAR(256) NOT NULL,
  `entity_id`     VARCHAR(1024) NOT NULL,
  `script`        MEDIUMTEXT   NOT NULL COMMENT '脚本正文，删除即删行',
  `script_sha256` CHAR(64)     NOT NULL COMMENT '与 BaaS 侧 run 记录对账',
  `size_bytes`    INT          NOT NULL,
  `modifier`      VARCHAR(1024) NOT NULL COMMENT '审计：最后写入者',
  `gmt_create`    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified`  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_env_bot` (`env`, `bot_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 启动脚本';
```

```sql
-- src/baas/sqls/2026_08_10_device_startup_run.sql
CREATE TABLE `baas_device_startup_run` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `tenant`        VARCHAR(64)  NOT NULL,
  `env`           VARCHAR(16)  NOT NULL,
  `device_uuid`   VARCHAR(128) NOT NULL,
  `run_id`        VARCHAR(64)  NOT NULL COMMENT '每次容器启动一个，回调幂等键',
  `script_sha256` CHAR(64)     NOT NULL,
  `status`        VARCHAR(16)  NOT NULL COMMENT 'RUNNING|SUCCESS|FAILED|TIMEOUT|DISPATCH_FAILED',
  `exit_code`     INT          DEFAULT NULL COMMENT 'RUNNING 期间为空，是有意状态',
  `stdout`        MEDIUMTEXT   DEFAULT NULL,
  `stderr`        MEDIUMTEXT   DEFAULT NULL,
  `truncated`     TINYINT(1)   NOT NULL DEFAULT 0,
  `started_at`    DATETIME     NOT NULL,
  `finished_at`   DATETIME     DEFAULT NULL,
  `gmt_create`    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified`  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tenant_device_run` (`tenant`, `device_uuid`, `run_id`),
  KEY `idx_device_started` (`tenant`, `device_uuid`, `started_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='设备启动脚本执行记录';
```

Retention is a cron sweep on `started_at`; no backfill — both tables start empty
and absence means "never set" / "never ran".

## API / Interface Changes

```python
# src/backend/.../adapters/http/openapi_v1/bots/router.py  (new routes)
@router.get("/{bot_id}/startup-script", response_model=Envelope[StartupScript])
@router.put("/{bot_id}/startup-script", response_model=Envelope[StartupScript])
@router.delete("/{bot_id}/startup-script", response_model=Envelope[Deleted])
@router.get("/{bot_id}/startup-script/runs", response_model=Envelope[Page[StartupScriptRun]])
```

```python
# src/backend/.../adapters/http/openapi_v1/bots/schemas.py (new)
class StartupScript(BaseModel):
    bot_id: str
    script: str            # "" when never set — absence is not an error
    size_bytes: int
    updated_by: str
    updated_at: datetime
    supported: bool          # see _resolve_support below — NOT provider capability alone
    unsupported_reason: str  # "" when supported

class StartupScriptRun(BaseModel):
    instance_id: str       # device_uuid — one row per instance of a scaled bot
    run_id: str
    status: Literal["running", "success", "failed", "timeout", "dispatch_failed"]
    exit_code: int | None  # None only while RUNNING — an intentional state
    stdout: str
    stderr: str
    truncated: bool
    started_at: datetime
    finished_at: datetime | None  # None only while RUNNING
```

```jsonc
// PUT /openapi/v1/bots/{bot_id}/startup-script → 200
// body: { "script": "#!/bin/bash\nset -e\n…" }
{ "code": 200000, "message": "OK", "request_id": "…",
  "data": { "bot_id": "…", "size_bytes": 812, "supported": true,
            "unsupported_reason": "", "updated_by": "…", "updated_at": "…" } }
// 413 when the body exceeds the size limit (names the limit)
// 409 when this bot's provider cannot execute scripts (unsupported_reason set)
```

```diff
# src/baas/.../api/paas/_protocols.py:32 — PaasService
+    async def supports_startup_script(self) -> bool:
+        """Whether this provider can run a per-bot startup script."""
+
+    async def run_startup_script(
+        self, paas_device_id: str, script: str, run_id: str,
+        timeout_seconds: int, envs: dict[str, str],
+    ) -> None:
+        """Dispatch the user stage. Fire-and-forget; result arrives by callback."""
```

`_teclaw_paas_service.py:231` returns `False` / raises `UnsupportedOperation`;
Arca, K8s, Docker, Poolab return `True` and inherit the exec-based default.

```diff
# src/baas/.../api/device_manage/_deploy_config.py:423 — DeployConfig
     after_create_cmd_hook: str | None = Field(default=None, ...)
+    startup_script: str | None = Field(
+        default=None, description="Per-bot user stage; runs after the device is ACTIVE"
+    )
+    startup_script_sha256: str | None = Field(default=None)
+    startup_script_timeout_seconds: int = Field(default=600, ge=1, le=3600)
+    startup_script_secret_envs: dict[str, str] | None = Field(
+        default=None, description="env name -> secret NAME, resolved at dispatch"
+    )
```

```jsonc
// POST /api/v1/devices/startup-script-callback  (new, BaaS)
// Deliberately not /api/v1/publish/device-callback: that one requires publish_id>0
// and drives publish state. A restart has no publish, and a user-script failure
// must not fail a publish.
{ "device_uuid": "…", "tenant": "…", "run_id": "…", "result_status": "FAILED",
  "exit_code": 127, "stdout": "…", "stderr": "…", "truncated": false }
```

```diff
# src/baas/.../adapters/web/routers/bot_service/management_router.py:445 — restart
- class RestartBotRequest(BaseRequest): operator: str; request_id: str
+ class RestartBotRequest(BaseRequest):
+     operator: str; request_id: str
+     startup_script_patch: StartupScriptPatch | None = None  # latest script, applied before start
```

## Key Files & Functions

```python
# src/backend/.../api/bot_startup_script_service.py (new — Service API Protocol)
# Impl: core/bot_startup_script/services/startup_script_service.py::BotStartupScriptService
@runtime_checkable
class BotStartupScriptServiceProtocol(Protocol):
    def get(self, bot_id: str, owner_id: str) -> StartupScriptRecord: ...
    def put(self, bot_id: str, owner_id: str, script: str, modifier: str) -> StartupScriptRecord: ...
    def delete(self, bot_id: str, owner_id: str) -> None: ...
    def list_runs(self, bot_id: str, owner_id: str, limit: int, offset: int) -> list[StartupScriptRun]: ...
```

The concrete service must **not** inherit the Protocol — that forces a
`core → api` import the layering rule forbids (`api/README.md:21`). The repo's
link between the two is a registry that imports both symbols in one file, which
is also what makes the pair navigable; register there rather than inventing a
new mechanism, and give the Protocol real signatures (the registry is explicitly
for Protocols that have them — `*args/**kwargs` makes its signature check
vacuous).

```diff
# src/backend/tests/community/architecture/test_service_api_conformance.py:76 — _PAIRS
  _PAIRS = [
      (EngineConfigServiceProtocol, EngineConfigService),
+     (BotStartupScriptServiceProtocol, BotStartupScriptService),
  ]
```

```python
# src/baas/tests/architecture/check_protocols/api/paas/check_paas_service.py (extend)
# BaaS's equivalent: a mypy-checked binding, one file per Protocol.
# Today it binds Arca only; the new capability methods must be exercised on every
# implementation, so add a binding per provider rather than widening this one.
_teclaw: PaasServiceProtocol = TeClawPaasService(...)   # declares False
_k8s: PaasServiceProtocol = K8sPaasService(...)         # declares True
```

```python
# src/backend/.../core/bot_startup_script/services/_support.py (new)
def _resolve_support(bot: dict) -> tuple[bool, str]:
    """Support keys on the PROVIDER, not the bot type.

    Personal and service bots share one create path — _allocate_via_baas accepts
    both (baas_device_service.py:295) and calls the same _build_create_bot_payload,
    which sets after_create_cmd_hook unconditionally. Bot type does not select a
    provider either: the template comes from template_config.template_uid, so a
    personal bot and a service bot can land on the same provider or different ones.
    """
    if not provider_supports_startup_script(bot):     # BaaS capability, cached
        return False, f"provider {bot['provider_type']} cannot execute scripts"
    return True, ""
```

```diff
# src/backend/.../core/service_bot/services/baas_service.py:724 — _build_create_bot_payload
         deploy_config = BotDeployConfig(
             after_create_cmd_hook=start_up_cmd,
             after_create_hook_wait_seconds=10,
+            # User stage — kept OUT of after_create_cmd_hook on purpose: that field is
+            # `&&`-chained (see _get_start_cmd:2241) and its failure fails the device.
+            startup_script=startup_script,
+            startup_script_sha256=startup_script_sha256,
```

```python
# src/baas/.../core/service/device_startup_script/_stage.py (new)
class StartupScriptStage:
    """The one seam. Called wherever a device becomes reachable."""
    async def on_device_active(self, tenant: str, device_uuid: str) -> None:
        # 1. load deploy_config.startup_script from baas_device.extra_config
        # 2. skip when absent, or when provider .supports_startup_script() is False
        # 3. open a RUNNING row (run_id = uuid4), resolve secret envs
        # 4. facade.run_startup_script(...)  — result arrives by callback
```

```diff
# src/baas/.../core/service/device_manage/_device_service.py:1138 — no-hook fast path
     repo.update_device(..., status=DeviceStatus.ACTIVE.value)
+    await self._startup_stage.on_device_active(tenant, device_uuid)
```

```diff
# src/baas/.../core/service/device_manage/_device_service.py:380 — _native_restart_device
     target_status = DeviceStatus.PENDING if has_async_callback else DeviceStatus.ACTIVE
     repo.update_device(..., status=target_status.value, err_msg=None)
+    if target_status is DeviceStatus.ACTIVE:
+        # K8S/DOCKER/POOLAB reach ACTIVE here and never re-ran any hook before this.
+        await startup_stage.on_device_active(tenant, device_uuid)
```

The third call site is the publish device-callback path, where a device driven by
the platform hook turns `ACTIVE` (`handle_device_callback`) — same one-line call.

```diff
# src/baas/.../core/service/paas/_start_hook_dispatcher.py:39 — WRAPPER_TEMPLATE
-cat > "$HOOK_SCRIPT_FILE" << 'HOOK_SCRIPT_EOF'
-{{ rendered_hook }}
-HOOK_SCRIPT_EOF
+printf '%s' {{ hook_script_b64 }} | base64 -d > "$HOOK_SCRIPT_FILE"
```

Heredoc → base64 is load-bearing now: `rendered_hook` was platform-generated
(`:70`), and this stage puts caller-authored text in the same position.

## Dependencies

None. No new packages; `jinja2`, the secret SPI, and the task queue are all
already in use.

## Risks & Mitigations

- **Risk:** caller-authored text reaches a generated shell wrapper — heredoc
  delimiter escape, quoting, `$(...)` in the body.
  **Mitigation:** transfer base64-encoded, never interpolate the body into shell
  syntax; a test asserts a body containing `HOOK_SCRIPT_EOF` and `$(id)` round-trips
  byte-exact.
- **Risk:** a long or hung script pins a hook-executor thread (pool is 20,
  `_hook_executor.py:20`).
  **Mitigation:** dispatch is already `nohup` fire-and-forget; the wrapper owns the
  timeout via `timeout(1)` and reports `TIMEOUT` itself.
- **Risk:** scale-out creates instances from the bot's stored config, so a script
  updated after publish reaches new instances only on the next push.
  **Mitigation:** backend pushes the script on create *and* restart; a run row
  records `script_sha256`, so the API can show an instance running a stale script
  rather than hiding it.
- **Risk:** `BaasContainerInitializer` (the post-create exec sequence that runs for
  every BaaS-allocated binding) does the steps commented out of `_get_start_cmd` —
  sync service, engine dirs, skill symlinks, codefuse token — and it runs on
  **create only**. A user script that depends on those is depending on create-time
  state that a restart does not rebuild.
  **Mitigation:** out of scope to change, but documented in the API docs as a
  property of the environment the script runs in.
- **Risk:** secrets leaking into run output.
  **Mitigation:** secrets arrive as env vars resolved at dispatch, never in the
  body; stored stdout/stderr are masked against the resolved values before persist.
- **Risk:** the new stage runs on every start for every bot that has a script,
  adding a container round-trip to every restart.
  **Mitigation:** the stage no-ops without a stored script (the overwhelming
  majority), and the whole dispatch sits behind a system-config kill switch.

## Alternatives Considered

- **Append the script to `after_create_cmd_hook`.** Smallest diff, rejected: the
  chain is `&&`-joined and a non-zero hook exit marks the device `FAILED`
  (`_device_service.py:1080`), so a typo in a user script would stop the engine
  from starting. The spec requires the opposite.
- **Reuse `/api/v1/publish/device-callback`.** Rejected: `publish_id` is
  `gt=0`-required (`_models.py:516`) and the handler drives publish/batch/bot
  state, so a restart has nothing to send and a bad script would fail a publish.
- **Store the script in BaaS keyed by bot.** Rejected: duplicates the source of
  truth and puts a user-facing field's audit trail in the wrong service. BaaS
  keeps carrying it in `extra_config` exactly as it carries the platform hook.
- **Keep `if provider_type == "TECLAW"` branching.** Rejected: that pattern is
  what produced today's silent skips (`:1086`, `:1101`), which is precisely what
  the spec forbids for a public surface.
- **Run the stage before the engine is serving.** Rejected: it makes the script a
  readiness dependency, which is the coupling the spec removes.

## Rollout

Additive: absent script ⇒ zero behavior change on every path.

```bash
# 1. DDL first — both services read their table on the first request
mysql < src/backend/.../core/bot_startup_script/sql/2026_08_10_bot_startup_script.sql
mysql < src/baas/sqls/2026_08_10_device_startup_run.sql
# 2. BaaS before backend: backend sends startup_script in the create payload,
#    and an older BaaS would drop the field silently.
deploy baas && deploy backend
# 3. Publish the regenerated public schema (gateway serves + validates from it)
python src/backend/scripts/dump_openapi.py > src/gateway/configs/schemas/bots.openapi.json
bash src/gateway/scripts/dump_and_publish.sh
```

Kill switch: `baas_system_config` key `startup_script_stage_enabled` (default
`false` until the first end-to-end run). Disabled ⇒ the stage no-ops and the API
reports `supported: false`.

## Test Strategy

```python
# src/backend/tests/community/adapters/http/openapi_v1/test_bots_startup_script.py
def test_get_returns_empty_script_for_bot_that_never_set_one(): ...
def test_put_rejects_over_size_limit_with_413_naming_the_limit(): ...
def test_put_records_modifier_and_timestamp(): ...
def test_non_operator_gets_403(): ...
def test_reports_unsupported_for_a_teclaw_backed_bot(): ...
def test_runs_endpoint_returns_one_row_per_instance(): ...
```

```python
# src/baas/tests/unit/core/service/device_startup_script/test_stage.py
def test_no_script_configured_is_a_noop(): ...
def test_unsupported_provider_records_no_run_and_does_not_dispatch(): ...
def test_failed_run_leaves_device_ACTIVE(): ...        # the spec's core promise
def test_callback_is_idempotent_per_run_id(): ...
def test_script_body_with_heredoc_delimiter_round_trips(): ...
def test_secret_values_are_masked_in_persisted_output(): ...
```

```python
# src/baas/tests/unit/core/service/device_manage/test_device_service.py  (extend)
def test_native_restart_dispatches_startup_stage_for_k8s(): ...   # today: never runs
def test_native_restart_dispatches_startup_stage_for_docker(): ...
def test_arca_restart_dispatches_startup_stage_exactly_once(): ...  # restart = destroy+create
```

```python
# src/baas/tests/contract/spi/test_paas_service_conformance.py  (extend)
def test_every_paas_service_declares_startup_script_capability(): ...
```
