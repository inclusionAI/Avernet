# Plan: Per-Bot Startup Script

> **DESCOPED AT REVIEW — the `last-start` endpoint.** Everything below about
> `last-start`, `StartInstanceResult` and the run reader was built and then
> removed: resolving which start to report from the bot record only works for a
> personal bot or a *draft* service bot, so a published service bot would get an
> empty answer that looks like a real one. Read those sections as history, not as
> the shipped design. The three CRUD operations are unaffected.

## Approach

Backend-only. The bot's script is stored in a new backend table and **appended to
the string the backend already composes** in `_get_start_cmd`, so it travels to
BaaS in the existing `after_create_cmd_hook` field, is written into the container
by the existing wrapper, and runs before the existing callback reports the start.
No BaaS change, no new execution stage, no new callback, no new provider
capability.

Two things make the append safe, and both are load-bearing rather than
defensive: the body is **base64-encoded in Python** so it is never spliced into
shell syntax, and the platform's exit status is **captured before** the script
runs and re-asserted after it, so a user script can neither change the boot
outcome nor mask a boot failure.

## Affected Components

- `src/backend/.../core/bot_startup_script/` — **new** ORM model + service
- `src/backend/.../core/repository/protocols/bot/` — **new** repository contract
- `src/backend/.../core/repository/implementations/bot/` — **new** ORM body
- `src/backend/.../api/bot_startup_script_service.py` — **new** Service API Protocol
- `src/backend/.../core/service_bot/services/baas_service.py:2207` — compose the segment
- `src/backend/.../core/devices/services/baas_device_service.py:328` — fetch the script into `payload_kwargs`
- `src/backend/.../adapters/http/openapi_v1/bots/{router,schemas}.py` — 4 endpoints
- `src/backend/.../adapters/http/openapi_v1/admission.py` — 4 admission entries
- `src/gateway/configs/schemas/bots.openapi.json` — regenerated artifact

**Not touched:** `src/baas` — no `DeployConfig` field, no dispatcher change, no
callback, no DDL, no `PaasService` capability. This is the whole point of the
option.

## Data Model Changes

```sql
-- src/backend/src/agentclaw/community/core/bot_startup_script/sql/2026_08_10_bot_startup_script.sql
CREATE TABLE `ac_bot_startup_script` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `env`           VARCHAR(20)   NOT NULL,
  `bot_id`        VARCHAR(256)  NOT NULL,
  `entity_id`     VARCHAR(1024) NOT NULL,
  `script`        MEDIUMTEXT    NOT NULL COMMENT '脚本正文，删除即删行',
  `size_bytes`    INT           NOT NULL,
  `modifier`      VARCHAR(1024) NOT NULL COMMENT '审计：最后写入者',
  `gmt_create`    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified`  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_env_entity_id_bot_id` (`env`, `entity_id`, `bot_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Bot 启动脚本';
```

The key is `(env, entity_id, bot_id)`, matching `ac_bot_restart_lock`
(`bot_management/repository/models.py:84`) — the repo's existing per-bot side
table. `entity_id` is a **storage** key only: it is resolved server-side from the
bot record and never appears as a request parameter or a response field, per the
group contract.

No run-record table: run results come from the existing publish records.

## API / Interface Changes

```python
# src/backend/.../adapters/http/openapi_v1/bots/router.py  (new routes)
@router.get("/{bot_id}/startup-script", response_model=Envelope[StartupScript],
            dependencies=_GRANT_CHECKED)
@router.put("/{bot_id}/startup-script", response_model=Envelope[StartupScript],
            dependencies=_GRANT_CHECKED)
@router.delete("/{bot_id}/startup-script", response_model=Envelope[Deleted],
               dependencies=_GRANT_CHECKED)
@router.get("/{bot_id}/startup-script/last-start",
            response_model=Envelope[Page[StartInstanceResult]],
            dependencies=_GRANT_CHECKED)
```

Named `last-start`, not `runs`: it reports the **whole start sequence**, not the
script alone, and the name should not promise otherwise.

```python
# src/backend/.../adapters/http/openapi_v1/bots/schemas.py (new)
class StartupScriptWrite(BaseModel):
    """PUT request body — the ONLY client-supplied field."""
    script: str

class StartupScript(BaseModel):
    """Response only. Every field below is server-derived."""
    bot_id: str
    script: str            # "" when never set — absence is not an error
    size_bytes: int
    updated_by: str        # from the request principal at write time, never the body
    updated_at: datetime   # from gmt_modified, never the body
    supported: bool
    unsupported_reason: str  # "" when supported

class StartInstanceResult(BaseModel):
    instance_id: str       # device_uuid
    status: Literal["success", "failed", "pending"]
    exit_code: int | None  # None while pending — an intentional state
    stdout: str            # COMBINED platform + script output, ~3.4KB cap
    stderr: str            # ~512B cap
    truncated: bool
```

```diff
# src/backend/.../adapters/http/openapi_v1/admission.py — ADMISSION
  ("PUT", "/openapi/v1/bots/{bot_id}/engine-config"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
+ ("GET", "/openapi/v1/bots/{bot_id}/startup-script"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
+ ("PUT", "/openapi/v1/bots/{bot_id}/startup-script"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
+ ("DELETE", "/openapi/v1/bots/{bot_id}/startup-script"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
+ ("GET", "/openapi/v1/bots/{bot_id}/startup-script/last-start"): AdmissionMode.GRANT_CHECKED_OWN_BOT,
```

Every operation must appear in that table exactly once — `test_principal_seam.py`
refuses a route that is not in it.

## Key Files & Functions

The composition. `_get_start_cmd` currently returns one line; it becomes a small
multi-line script, which the wrapper's heredoc carries unchanged:

```diff
# src/backend/.../core/service_bot/services/baas_service.py:2241 — _get_start_cmd
-        return (
-            f"{bootstrap_cmp} && ({install_engine_cmd}) && "
-            f" {start_cmd} && {watchdog_cmd}"
-        )
+        chain = (
+            f"{bootstrap_cmp} && ({install_engine_cmd}) && "
+            f" {start_cmd} && {watchdog_cmd}"
+        )
+        if not startup_script:
+            return chain            # byte-identical to today
+        return (
+            f"{chain}\n"
+            f"__OCB_RC=$?\n"
+            f'if [ "$__OCB_RC" -eq 0 ]; then\n'
+            f"{self._get_startup_script_segment(startup_script)}\n"
+            f"fi\n"
+            f"exit $__OCB_RC\n"
+        )
```

`__OCB_RC` is the whole safety argument. The wrapper takes `EXIT_CODE=$?` from
the hook script's **last** command (`_start_hook_dispatcher.py:228`), so without
capturing and re-asserting the platform's status, a trailing `|| true` would make
every start report SUCCESS — including boots that failed. Capture, run, `exit
$__OCB_RC`.

```python
# same file (new helper)
def _get_startup_script_segment(self, script: str, timeout_seconds: int = 300) -> str:
    """Emit the user stage: decode, run under a timeout, never fail the boot."""
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    path = "/tmp/ocb_startup_script.sh"
    log = "/home/admin/logs/startup_script.log"
    return (
        f"  ( echo {b64} | base64 -d > {path}"
        f" && timeout {timeout_seconds} bash {path} >> {log} 2>&1 ) || true"
    )
```

base64's alphabet is `A-Za-z0-9+/=` only, so the blob is inert in shell —
no quoting to get wrong, no metacharacters, and no `{...}` for BaaS's
`_safe_format_hook` (`_device_service.py:59`) to substitute inside the body.

```diff
# src/backend/.../core/service_bot/services/baas_service.py:634
         start_up_cmd = self._get_start_cmd(
             bot_id=bot_id, owner_id=owner_id, ...,
+            startup_script=startup_script,
         )
```

```diff
# src/backend/.../core/devices/services/baas_device_service.py:328 — payload_kwargs
             "mount_home_dir_storage": True,
+            "startup_script": self._startup_script_service.get_body(bolt_id),
         }
```

The repository follows the consolidated layout — contract under
`protocols/bot/`, ORM body under `implementations/bot/`, and the implementation
declares the Protocol as a base so `@abstractmethod` binds:

```python
# src/backend/.../core/repository/protocols/bot/startup_script.py (new)
@runtime_checkable
class BotStartupScriptRepository(Protocol):
    @abstractmethod
    def get(self, *, env: str, entity_id: str, bot_id: str) -> StartupScriptRecord | None: ...
    @abstractmethod
    def upsert(self, *, env: str, entity_id: str, bot_id: str,
               script: str, size_bytes: int, modifier: str) -> StartupScriptRecord: ...
    @abstractmethod
    def delete(self, *, env: str, entity_id: str, bot_id: str) -> None: ...
```

```python
# src/backend/.../core/repository/implementations/bot/startup_script.py (new)
class BotStartupScriptRepositoryImpl(
    BotStartupScriptRepository,      # ← the base is what gives @abstractmethod force
):
    @inject
    def __init__(self, db: DatabasePlugin) -> None: ...
```

```python
# src/backend/.../api/bot_startup_script_service.py (new — Service API Protocol)
# No @abstractmethod here: the concrete service must not inherit this Protocol
# (core → api is forbidden), so it would bind nothing. See Alternatives Considered.
# Impl: core/bot_startup_script/services/startup_script_service.py::BotStartupScriptService
@runtime_checkable
class BotStartupScriptServiceProtocol(Protocol):
    def get(self, bot_id: str, owner_id: str) -> StartupScriptRecord: ...
    def put(self, bot_id: str, owner_id: str, script: str, modifier: str) -> StartupScriptRecord: ...
    def delete(self, bot_id: str, owner_id: str) -> None: ...
    def get_body(self, bot_id: str) -> str: ...   # "" when unset; used at payload build
    def last_start(self, bot_id: str, owner_id: str) -> list[StartInstanceResult]: ...
```

The concrete service must **not** inherit the Protocol (`core → api` import is
forbidden — `api/README.md:21`); register the pair instead:

```diff
# src/backend/tests/community/architecture/test_service_api_conformance.py:76 — _PAIRS
+     (BotStartupScriptServiceProtocol, BotStartupScriptService),
```

Support is answered per bot, from the binding, and both unsupported cases are
real deployments rather than hypotheticals:

```python
# src/backend/.../core/bot_startup_script/services/_support.py (new)
def resolve_support(bot: dict, binding: DeviceBinding | None) -> tuple[bool, str]:
    """Only bots whose container is provisioned through the shared start
    sequence can run a script — i.e. device_provider == "baas"."""
    if is_teclaw_bot(bot.get("active_engine")):
        # TeclawProvisionService.provision skips DeviceService.apply_device
        # (bot_service.py:1445), so there is no deploy_config to carry a script.
        return False, "teclaw bots are provisioned without a start sequence"
    provider = getattr(binding, "device_provider", None)
    if provider != BAAS_DEVICE_PROVIDER:
        # Legacy bots created before the BaaS rollout talk to ARCA directly and
        # never build an after_create_cmd_hook through _build_create_bot_payload.
        return False, f"bots on the {provider!r} device provider have no start sequence"
    return True, ""
```

`PUT` on an unsupported bot is **refused**, not stored — a stored script that can
never run is the silent no-op this design exists to prevent, and staging one
ahead of a migration is not a real workflow. `GET` still answers (empty script,
`supported: false`, reason), and `last-start` returns an empty list.

```jsonc
// PUT /openapi/v1/bots/{bot_id}/startup-script → 409 on an unsupported bot
{ "code": 409001, "message": "teclaw bots are provisioned without a start sequence",
  "data": null, "request_id": "…" }
```

`last_start` reads what already exists — no new storage:

```python
# core/bot_startup_script/services/_last_start.py (new)
# binding.device_props["publish_id"] → BaasService.get_publish_progress(
#     publish_id, include_devices=True) → per-device result_message,
# which serialize_hook_result packed as {"exit_code", "stdout", "stderr"}
# (publish_manage/_models.py:535). Parse and surface per instance.
```

## Dependencies

None. `base64` is stdlib.

## Risks & Mitigations

- **Risk:** a trailing `|| true` swallows the platform's failure and every device
  reports SUCCESS.
  **Mitigation:** `__OCB_RC` capture + `exit $__OCB_RC`. A test asserts that a
  failing platform chain still yields a non-zero hook exit **with** a script present.
- **Risk:** script body breaks the shell composition.
  **Mitigation:** base64; a test round-trips a body containing quotes, `$(id)`,
  `HOOK_SCRIPT_EOF` and `{token}` and asserts byte-exact execution.
- **Risk:** a slow script pushes the start past Avernet's create budget
  (`_CREATE_PUBLISH_TIMEOUT_SECONDS = 600`), failing the create from the poller's
  side even though the container is fine.
  **Mitigation:** `timeout 300` on the user stage, documented; the platform stage
  is a dispatch chain and completes fast, leaving headroom.
- **Risk:** the caller cannot tell their script's failure from a platform
  failure — the accepted cost of this option.
  **Mitigation:** the script's own output goes to a dedicated log
  (`/home/admin/logs/startup_script.log`) so it is at least separable **inside**
  the container; the API says plainly that `last-start` covers the whole sequence.
- **Risk:** the script silently stops re-running for a bot whose provider
  restarts in place.
  **Mitigation:** none available in this option; stated in the docs as a known
  limitation, inherited from the platform hook.

## Alternatives Considered

- **A separate `DeployConfig.startup_script` field with its own BaaS execution
  stage, callback and run table.** Full separation of status, output, timeout and
  masking — and roughly five times the work, across two services. Deferred; this
  plan is deliberately the cheap option, and the spec records what it gives up.
- **Appending with `&&`.** A failing script would abort the chain, and a failing
  script *before* the platform steps would stop the engine from starting. The
  script goes last, guarded, and cannot participate in the chain's outcome.
- **`|| true` without capturing the platform status.** Simplest to write and
  silently breaks FAILED detection for every bot with a script.
- **`@abstractmethod`: used in the repository layer, not in `api/`.** The two
  layers have opposite conventions and both are deliberate.
  `core/repository/README.md:8` states it outright — protocols carry
  "`@abstractmethod` throughout" and implementations "each declaring its
  Protocol(s) as a base" (e.g. `DeviceRepository(DeviceBindingRepository)` at
  `implementations/devices/device.py:145`). Inheritance is legal there because
  both sides live inside `core`, and `@abstractmethod` does real work: a missing
  member fails at construction naming itself, rather than as an `AttributeError`
  at the call site.
  `api/` is the inverse: the concrete service must **not** inherit its Protocol,
  because that would force a `core → api` import the layering rule forbids
  (`api/README.md:21`), and zero of the ~60 Protocols there use `@abstractmethod`
  — it would bind nothing. Conformance is checked instead by the `_PAIRS`
  registry, which compares parameter names, kinds, defaults and coroutine status.
  So: `@abstractmethod` on the new **repository** Protocol, none on the Service
  API Protocol.
- **Passing the body as a shell-quoted string instead of base64.** `shlex.quote`
  would survive Python composition, but the body still meets BaaS's
  `_safe_format_hook` placeholder substitution before it runs. base64 is inert to
  both.

## Rollout

Backend-only, additive, no ordering constraint between services.

```bash
mysql < src/backend/.../core/bot_startup_script/sql/2026_08_10_bot_startup_script.sql
deploy backend
python src/backend/scripts/dump_openapi.py > src/gateway/configs/schemas/bots.openapi.json
bash src/gateway/scripts/dump_and_publish.sh
```

A bot with no stored script returns `chain` unchanged, so every existing bot's
start sequence is byte-identical. That is the rollback story: clear the script.

## Test Strategy

```python
# src/backend/tests/community/core/service_bot/services/test_baas_service_start_cmd.py (extend)
def test_no_script_returns_todays_string_byte_identical(): ...
def test_script_is_base64_encoded_and_never_interpolated(): ...
def test_body_with_quotes_dollar_paren_and_heredoc_delimiter_round_trips(): ...
def test_body_containing_token_placeholder_is_not_substituted(): ...
def test_platform_failure_still_exits_nonzero_when_script_present(): ...
def test_script_is_skipped_when_platform_chain_failed(): ...
def test_segment_wraps_the_script_in_timeout(): ...
```

```python
# src/backend/tests/community/adapters/http/openapi_v1/test_bots_startup_script.py
def test_get_returns_empty_script_for_bot_that_never_set_one(): ...
def test_put_rejects_over_size_limit_with_413_naming_the_limit(): ...
def test_put_records_modifier_and_timestamp(): ...
def test_routes_declare_grant_checked_and_appear_in_admission(): ...
def test_non_operator_gets_403(): ...
def test_teclaw_bot_reports_unsupported(): ...
def test_last_start_returns_one_entry_per_instance(): ...
```
