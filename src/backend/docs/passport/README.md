# Passport — a bot's identity credential

**English** | [简体中文](README.zh-CN.md)

What Passport is, what it holds, how a bot gets one and loses one, and the one
rule about updating it that everything else defends against. Written as a
companion to [`docs/mcp/README.md`](../mcp/README.md) — MCP sync writes to
Passport, and §7 below maps the two together — but Passport is its own thing and
this doc stands alone.

Every code block is quoted verbatim from this repository, with a `path:line`
reference you can click through.

---

## 1. The one-sentence version

**A Passport is a bot's identity document.** It answers "who is this bot" with a
credential the bot can present to services, and "what is this bot allowed to
reach" with a list of grants.

The name is the analogy, and it holds up well:

| A person's passport | A bot's Passport |
| --- | --- |
| Identity page — your name, your number | `agent_id`, `agent_code`, `credential_id` |
| The document itself, presented at a border | `token` — handed to the device, sent with outbound calls |
| Visas — which countries you may enter | `resourceManifest` — which MCP servers and CLIs this bot may use |
| Valid / expired / revoked | `PENDING` → `ISSUED` → frozen → destroyed |

The reason a bot needs its own document rather than borrowing its owner's: a bot
acts on its own, on a schedule, in a container, long after the human who created
it has closed the tab. Something has to be able to say "this request came from
bot X, which is authorized for Y" without a human session behind it.

Externally the service is called **AgentPass / tcauthmng**; in this codebase it
sits behind one Protocol:

```python
@runtime_checkable
class PassportPlugin(Plugin, Protocol):
    """Plugin for passport lifecycle management (tcauthmng facade)."""
```

<sub>`src/backend/src/agentclaw/community/plugin_api/passport.py:117`</sub>

---

## 2. What a Passport actually holds

The clearest picture is the shape returned when you read one back. This is the
schema snapshot the gateway contract test locks:

```json
{
  "access_mode": {"type": "string"},
  "agent_code": {"type": "string"},
  "agent_id": {"type": "string"},
  "certificate_url": {"type": "string"},
  "credential_id": {"type": "string"},
  "expire_at": {"type": "string"},
  "mcps": {"type": "array", "items": {"...": "mcp_code / mcp_name / mcp_desc"}}
}
```

<sub>`src/backend/tests/community/contracts/gateway/schema_snapshots/response_data/rule01_GET_api_bots_id_passport.json`
(abridged)</sub>

Three groups, and it's worth keeping them separate in your head:

**1. Identity.** `agent_id` / `agent_code` / `credential_id` / `certificate_url`
/ `expire_at`, plus the `token` you fetch separately. This is the "who".

**2. Scope — the `resourceManifest`.** Two resource types today, MCP servers and
CLIs, each a typed item:

```python
class CliItem(TypedDict, total=False):
    """CLI scope item supplied by AgentPass query responses."""

    cli_code: str | None
    cli_name: str | None
    cli_desc: str | None


class McpScopeItem(TypedDict, total=False):
    """Provider-neutral MCP resource item for Agent Principal updates."""

    mcp_code: str
    mcp_name: str | None
    mcp_desc: str | None
    identity_mode: str
```

<sub>`src/backend/src/agentclaw/community/plugin_api/passport.py:41`</sub>

**3. State.** `PENDING` while approval is outstanding, `ISSUED` once granted,
frozen when the bot goes dormant, gone when the bot is deleted. §4 walks that.

---

## 3. The rule that matters most: updates are overwrites

If you remember one thing from this document, this is it.

`updatePassport` **replaces** each resource list rather than merging into it.
Submit `{mcp_codes: [...]}` alone and the bot's CLI grants are gone — not
rejected, not merged, *silently replaced with nothing*. The type that models the
call says so in its docstring:

```python
class PassportResourceScope(TypedDict, total=False):
    """Complete resourceManifest scope for overwrite-style updatePassport calls.

    AgentPass/tcauthmng treats each resource list in updatePassport as a full
    replacement. Callers that update MCP or CLI scope must pass both lists here
    so one resource type does not accidentally clear the other.
    """

    mcp_codes: list[str]
    mcp_items: list[McpScopeItem]
    cli_items: list[CliItem]
```

<sub>`src/backend/src/agentclaw/community/plugin_api/passport.py:58`</sub>

Two consequences shape the calling code everywhere:

**A. Anyone updating scope must first read back what they aren't changing.**
This is why MCP sync — which cares only about MCP — queries the bot's *CLI*
scope before writing (`docs/mcp/README.md` §6.5):

```python
            # resource_scope 是完整快照：MCP 来自同步结果，CLI 来自当前许可证 + 引擎默认 CLI。
            self.passport_update.update_passport(
                bot_id=bot_id,
                user_id=user_id,
                resource_scope={
                    "mcp_codes": synced_server_codes,
                    "cli_items": cli_items,
                },
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/sync_service.py:913`
— "resource_scope is a complete snapshot: MCP from the sync result, CLI from the
current licence plus the engine defaults."</sub>

**B. Omitting `resource_scope` entirely is the way to say "don't touch the
grants".** A metadata-only update (rename the bot, change its admins) passes
nothing, and the helper that unpacks the field encodes that as a deliberate
`None`:

```python
def unpack_resource_scope(
    resource_scope: PassportResourceScope | None,
) -> tuple[list[McpScopeItem] | None, list[CliItem] | None]:
    """Return DTO-ready MCP/CLI lists, or None pair for non-resource updates.

    Non-resource updates, such as admins or metadata, intentionally omit
    resource_scope so existing MCP/CLI grants are left untouched.
    """
    if resource_scope is None:
        return None, None
    try:
        cli_items = resource_scope["cli_items"]
    except KeyError as e:
        raise ValueError(
            "resource_scope must include mcp_codes and cli_items"
        ) from e
```

<sub>`src/backend/src/agentclaw/community/plugin_api/passport.py:90`</sub>

Note the `KeyError → ValueError`: pass a *partial* scope and it fails loudly.
The only safe states are "complete snapshot" and "nothing at all"; the dangerous
middle is rejected at the boundary. The community implementation calls this
unpacking even though it has no external registry to notify, purely to keep that
check alive in every deployment:

```python
        # No external registry to notify — scope/admin changes are a no-op.
        # ``unpack_resource_scope`` is still called so a malformed scope raises
        # the same ValueError the corp impl would, keeping callers honest.
        unpack_resource_scope(resource_scope)
```

<sub>`src/backend/src/agentclaw/community/plugins/community/passport.py:53`</sub>

---

## 4. The lifecycle

### 4.1 Apply — at bot creation

A bot's Passport is applied for as part of creating the bot, and the very first
bot a user creates takes a different call than subsequent ones (first-time
application involves an approval the user has not yet given):

```python
    apply = (
        passport_plugin.apply_first_agent_passport
        if is_first_bot
        else passport_plugin.apply_agent_passport
    )
```

<sub>`src/backend/src/agentclaw/community/core/bot_management/create_flow.py:204`</sub>

Note what is passed in: `mcp_codes` and `cli_items` are arguments *to the
application*. A bot's initial scope is declared at birth, not bolted on
afterwards.

The resulting `agent_code` is persisted onto the bot record so later code doesn't
have to re-query for it:

```python
def _build_ext(
    *, avatar_url: str | None, agent_code: str | None, issued: bool = False
) -> dict[str, Any] | None:
    """Assemble the bot's ``ext`` payload; ``None`` when there is nothing to store."""
```

<sub>`src/backend/src/agentclaw/community/core/bot_management/create_flow.py:227`</sub>

### 4.2 The consent step — why creation is sometimes two-phase

For desktop bots the application may come back needing a human to approve it.
Creation therefore splits in two: apply, return "needs authorization" with a URL
for the user, then poll:

```python
        # 桌面版两段式：先返回 need_authorization，前端授权后再调 auth-status
        if result.get("need_authorization"):
```

<sub>`src/backend/src/agentclaw/community/adapters/http/desktop/router.py:120`
— "desktop two-phase: return need_authorization first; the frontend calls
auth-status after authorizing."</sub>

```python
        status = auth_status.get("status")

        if status == "PENDING":
            return {
                "success": True,
                "data": {"status": "PENDING", "message": "授权处理中"},
            }

        if status == "ISSUED":
            result = service.create_after_authorization(
```

<sub>`src/backend/src/agentclaw/community/adapters/http/desktop/router.py:190`</sub>

`PENDING` → keep waiting; `ISSUED` → the credential exists and the bot can
finish being created. This is the clearest place in the codebase to see that a
Passport is *granted*, not merely allocated.

### 4.3 Use — the token reaches the device

The credential only matters once the running bot holds it. At publish/bootstrap
the backend fetches the token and passes it down to the device:

```python
            agent_pass_token = self._passport_plugin.query_token(bot_id, owner_id) or ""
```

<sub>`src/backend/src/agentclaw/community/core/service_bot/services/bot_build_service.py:493`</sub>

The comment above that line is worth noting: `查询 passport token（非阻塞，失败不影响
发布）` — "non-blocking, a failure does not block the release". Publishing is not
gated on the credential being fetchable.

### 4.4 Update — scope changes over the bot's life

Covered in §3. Triggered whenever the bot's grants change: MCP sync
(`docs/mcp/README.md` §6.2), skill-set CLI changes
(`adapters/http/skill_center/skillsets.py:1481`), admin or metadata edits.

### 4.5 Freeze / unfreeze — dormancy

A bot that goes dormant has its credential frozen rather than destroyed, so
reactivation restores it. Freezing is deliberately best-effort — the device is
already released by that point, so a failure is a billing concern, not a
correctness one:

```python
        try:
            self._passport_plugin.freeze_agent_passport(
                bot_id=candidate.bot_id,
                owner_workno=candidate.owner_id,
                reason="dormant recycle",
            )
        except Exception as exc:
            logger.warning(
                "[DormantBotService._execute_recycle] passport freeze failed "
                "bot_id=%s: %s — continuing (recycle considered complete)",
```

<sub>`src/backend/src/agentclaw/community/core/bot_dormant/service.py:373`</sub>

Unfreeze is the opposite — strict, with a postcondition, because a bot cannot
boot without a usable token:

```python
        """Bring the credential online and make a runtime token queryable.

        Implementations must return only after ``query_token`` can provide the
        token required by device bootstrap, or raise when that postcondition
        cannot be established.
        """
```

<sub>`src/backend/src/agentclaw/community/plugin_api/passport.py:237`</sub>

That asymmetry is the design: **losing a credential you're no longer using is
tolerable; claiming to have restored one that isn't actually usable is not.**

### 4.6 Destroy — bot deletion

```python
                self._passport_plugin.destroy_passport(bot_id, user_id)
```

<sub>`src/backend/src/agentclaw/community/core/bot_management/services/bot_service.py:3195`</sub>

---

## 5. Agent Principal and identity mode

There is a second, narrower write path alongside `update_passport`:

```python
    def update_mcp_identity_to_agent_principal(
        self,
        *,
        bot_id: str,
        user_id: str,
        mcp_items: list[McpScopeItem],
    ) -> None:
        """Replace the Agent Principal MCP scope while preserving other resources."""
```

<sub>`src/backend/src/agentclaw/community/plugin_api/passport.py:140`</sub>

The distinction from §3's blanket overwrite is in the docstring: this one
replaces the MCP scope **while preserving other resources**. It exists because
`McpScopeItem` carries a field the plain code list cannot — `identity_mode`,
which is `owner` or `caller`:

- **owner** — the bot calls the MCP server as whoever owns the bot.
- **caller** — the bot calls as whichever user is talking to it right now.

That per-server choice is stored in `ac_bot_mcp_call_config` (see
`docs/mcp/README.md` §3.3) and published here. Validation is strict on the way
out, in the plugin itself:

```python
        if any(
            not item.get("mcp_code")
            or item.get("identity_mode") not in {"owner", "caller"}
            for item in mcp_items
        ):
            raise ValueError("invalid MCP identity scope")
```

<sub>`src/backend/src/agentclaw/community/plugins/community/passport.py:170`</sub>

"Caller" mode is what makes a shared service bot possible: it acts with *your*
authority when you talk to it, rather than silently borrowing its owner's.

---

## 6. What Passport is *not*

Three clarifications that save a lot of confusion:

**Passport does not enforce anything at tool-call time.** It is a declaration. On
the MCP path, the thing that actually stops a bot from calling a server is the
whitelist pushed to the device (`filter-servers`) — see `docs/mcp/README.md`
§6.2, where the device write happens *first* and passport is only updated after
it succeeds. Passport is the record of authority; the device is the enforcement
point.

**Passport does not hold MCP credentials.** API keys and headers for MCP servers
live in `ac_user_mcp_config` and are inlined into the device artifact. Passport
holds the bot's *own* credential and the *list* of servers it may use — never the
keys for reaching them.

**A Passport belongs to a bot, not to a user.** It's keyed by `bot_id` with the
owner (`owner_workno`) alongside for authorization. One user with ten bots has
ten Passports.

---

## 7. Where MCP touches Passport

For readers arriving from the MCP walkthrough, the full intersection:

| MCP doc | What it does with Passport | Call |
| --- | --- | --- |
| §6.2 | After the device whitelist lands, publishes the new MCP code list | `update_passport(resource_scope=...)` |
| §6.5 | Reads CLI scope back so the MCP write doesn't erase it | `query_passport_clis` |
| §6.9 | Strips LOCAL/stdio servers — no permission system grants those | `passport_mcp_items_from_entries` |
| §3.3 | Publishes owner-vs-caller identity per server | `update_mcp_identity_to_agent_principal` |

The filter in §6.9 is worth restating from the Passport side, because it explains
*why* the two lists differ:

```python
def filter_passport_mcp_codes(
    mcp_codes: Iterable[str],
    *,
    local_registry: LocalMCPRegistry | None = None,
) -> list[str]:
    """Return MCP codes that should be declared to the passport service.

    LOCAL/stdio MCP servers are runtime-local capabilities. They still need to
    be synced to the device, but the passport service does not own their permission scope.
    """
```

<sub>`src/backend/src/agentclaw/community/core/mcp/services/passport_scope.py:11`</sub>

The device list and the Passport list are deliberately not the same list. A
stdio server running inside the bot's own container isn't something an external
authority grants.

---

## 8. Deployment profiles

Same pattern as the rest of the codebase — one Protocol, three implementations:

| Profile | Implementation | Behaviour |
| --- | --- | --- |
| corp | `ProdPassportPlugin` (not in this repo) | Real tcauthmng / AgentPass calls |
| community | `SelfIssuedPassportPlugin` | Self-issues a deterministic token; scope updates are no-ops |
| local / test | `LocalPassportPlugin` | Mock seam; logs writes, returns a mock token |

The community implementation is the interesting one, because it explains what
Passport is *for* by describing what happens when there's nobody to ask:

```python
"""SelfIssuedPassportPlugin — community bot-credential broker.

The corp ``PassportPlugin`` brokers per-bot "agent passport" credentials through
tcauthmng/AgentPass. A community deployment is the sole authority over its own
bots, so it **self-issues** those credentials locally: a deterministic, stateless
function of ``bot_id`` — no external approval system, no consent step, no I/O.
"""
```

<sub>`src/backend/src/agentclaw/community/plugins/community/passport.py:1`</sub>

```python
def _token_for(bot_id: str) -> str:
    return f"community-passport-{bot_id}"
```

<sub>`src/backend/src/agentclaw/community/plugins/community/passport.py:34`</sub>

A community deployment is its own authority, so consent is vacuous and the token
is an inert bearer placeholder. Everything above still runs — the calls, the
snapshot discipline, the validation — it just resolves locally. The local/test
plugin is explicit that it proves nothing about the real integration:

```python
    """Local Mock 实现 —— 不代表 AgentPass 真集成。

    所有写操作仅打日志、apply_*/query_* 返回 mock token，**不**向 tcauthmng/AgentPass
    发任何请求。本地无法验证 AgentPass 端真实写入/生效；admin 同步的真实回执只能 prod 验证。
    """
```

<sub>`src/backend/src/agentclaw/community/plugins/local/passport.py:32`
— "Local mock — not a real AgentPass integration. All writes only log; `apply_*`
/ `query_*` return a mock token and send nothing to tcauthmng/AgentPass. Real
write-through cannot be verified locally."</sub>

---

## 9. Reading order & test map

1. `plugin_api/passport.py` — the whole contract in one file (283 lines). Read
   the four types at the top before the Protocol.
2. `plugin_api/passport.py:90` — `unpack_resource_scope`, i.e. §3's rule as code.
3. `plugins/community/passport.py` — a complete implementation, small enough to
   read end to end.
4. `core/bot_management/create_flow.py:204` — where a Passport is born.
5. `core/mcp/services/sync_service.py:849` — the most careful caller of
   `update_passport` in the repo, and a good template for writing another one.

| Test | Covers |
| --- | --- |
| `src/backend/tests/community/contracts/test_passport.py` | `PassportPlugin` conformance |
| `src/backend/tests/community/plugins/community/test_passport.py` | Self-issued implementation |
| `src/backend/tests/community/plugins/test_passport_save_sub_resources.py` | Second-level resource sync |
| `src/backend/tests/community/services/test_bot_passport.py` | Bot-lifecycle integration |
| `src/backend/tests/community/core/mcp/services/test_caller_identity_principal_sync.py` | Agent Principal identity scope |
| `src/backend/tests/community/core/bot_management/services/test_default_bot_passport_repair_service.py` | Repair path for missing Passports |

Related docs:

- [`src/backend/docs/mcp/README.md`](../mcp/README.md) — the MCP walkthrough this
  one accompanies.
- `src/backend/specs/2026-07-10-dormant-unfreeze-passport-one/` — the freeze /
  unfreeze postcondition, specified.
