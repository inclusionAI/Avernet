# Plan: `GET /bots/{bot_id}/skills` Returns Every Skill the Bot Has

## Approach

Three existing seams do the work; none is replaced.

1. **The SkillSet control plane answers "which Skills does a SkillSet bridge to
   this bot, and should each be installed?"** — one new read,
   `bot_skillset_bridge`, next to `ensure_active_skillset_installations`. It
   already owns which Sets a bot has, how a Default Set is scoped, and where the
   exclusion rows live, so re-deriving any of that elsewhere would be a second
   authority.
2. **The Installation repository applies the repair.** `install` / `uninstall` /
   `list_installed_skill_ids` already exist and are already the write path every
   command uses. The service diffs and calls them; no new persistence.
3. **The Skill repository pages the merged list.** The existing
   `list_bot_local_skills` becomes `list_bot_skills`, taking the bridged ids and
   widening its predicate from "Bot-owned and `local://`" to "Bot-owned **or**
   bridged". `active` still comes from the same Installation `EXISTS` subquery.

`LocalSkillQueryService` sequences them: authorize (unchanged), resolve the
bridge, repair, page.

Rejected: a new `BotSkillListService`. It would duplicate
`_require_view_access`, and `LocalSkillQueryService` is already exactly "the
public Bot-scoped Skill read service" — only its Local-only predicate was
narrower than its job.

## Affected Components

**Repository contract and persistence**

- `core/repository/skill_set_control_plane_types.py` — **new type**
  `BotSkillSetBridge(members, activate, deactivate)`.
- `core/repository/protocols/skill_set_control_plane.py` — **new method**
  `bot_skillset_bridge(bot_id, owner_id, engine_type, default_engine_types)`.
- `core/repository/implementations/skill_center/skill_set_control_plane.py` —
  implements it, plus a private `_bot_sets` helper (own ordinary Sets + own
  Default Set + platform Default). Reuses `excluded_skill_ids` and
  `global_default_scope` from `default_skillset_projection.py`.
- `core/repository/protocols/skill_center.py` +
  `implementations/skill_center/skill.py` — `list_bot_local_skills` →
  `list_bot_skills(…, skill_set_member_ids)`. `_public_local_skill` →
  `_public_bot_skill` (it no longer projects only Local rows).

**Service**

- `api/local_skill_query_service.py` — `list_local_skills` → `list_bot_skills`.
- `core/skill_center/services/local_skill_query_service.py` — takes the control
  plane repository and the Installation repository; `_require_view_access`
  returns the bot it already loads, so the engine and env come from it without a
  second read. `get_local_skill` is untouched.
- `di/modules/skill_center_module.py` — two constructor arguments on the
  existing provider.

**Adapter**

- `adapters/http/openapi_v1/skills/router.py` — call the renamed method; the
  handler docstring stops saying "local". The deprecated shim at
  `openapi_v1/deprecated/skills.py` delegates to this handler and inherits the
  change; it names this route as its replacement, so the two must not diverge.

**Docs**

- `core/skill_center/README.md` — the "never runs in HTTP GET/list" sentence
  becomes accurate: name this listing as the exception, and record that it also
  deletes and reads exclusions (`spec.md` *Decisions* 1–2).

Read, not modified:
`core/repository/implementations/skill_center/skill.py:list_bot_active_assets`
(the runtime's reachability, which the bridge must agree with),
`core/workspace/skill_layout.py:runtime_layout_engine_for_bot`.

Deliberately untouched, and not to be mistaken for contradicting the bridge:
`installation_compatibility.includes_default_skill_member` and
`local_skill_upload_service._ensure_default_set_membership`, both of which have
no callers (`spec.md` *Decisions* 3).

## Data Model Changes

None. `ac_skill`, `ac_skill_set`, `ac_skill_set_skill`,
`ac_bot_skill_installation`, and `ac_default_skillset_skill_exclusion` are all
read and written through their existing columns.

## API / Interface Changes

**Wire**: none. Same path, same query parameters, same `Envelope[Page[Skill]]`,
same status codes, same authorization dependency. Only the row set widens and
`active` becomes answerable for rows that could not appear before.

**`BotSkillSetBridge`** — three id sets, with the invariants stated on the
dataclass: `activate ∪ deactivate ⊆ members` and `activate ∩ deactivate = ∅`.
No branch on a Skill's source prefix (`spec.md` *Decisions* 3).

**`list_bot_skills` predicate** — `Skill.env == current AND ((bolt_id = bot AND
user_id = owner) OR id IN bridged)`, then `keyword`, then `active`, then
`ORDER BY gmt_modified DESC, id DESC`, then the page. `total` is the count after
filtering. Tenant isolation stays where it is: the ORM read guard on `Skill`,
`SkillSet`, `SkillSetSkill`, and `BotSkillInstallation`.

## Sequencing

The bridge read, the repair, and the paging query are three independent edits
behind one service call. The service is switched last, so no half-state is
reachable from HTTP: until the final task the router still calls the old method.

## Risks

- **Write in a GET.** Concurrent listings race on the same `install` /
  `uninstall`; `install` already treats a lost race as "already present" and
  `uninstall` deletes by identity, so the repair converges either way.
- **The repair reactivates something the owner turned off.** It cannot, given
  that a Default Set carries Repo Skills only (`spec.md` *Decisions* 3): a
  Skill the repair installs is one an active Set claims. A Skill no Set reaches
  is never written at all (*Acceptance* 7), which is the test that pins it.
- **Cost per request.** One extra Set scan plus one membership scan per Set, on
  a page that is already two queries. Bounded by the bot's own Sets.
