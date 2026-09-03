# Public API — `GET /bots/{bot_id}/skills` Returns Every Skill the Bot Has

## Summary

`GET /openapi/v1/bots/{bot_id}/skills` answers with Local Skills only. A caller
asking "what can this bot do?" gets back the subset the bot uploaded itself, and
nothing about the `git://` market Skills and `center://` Skills the bot actually
runs because a SkillSet puts them there.

This makes the operation answer its whole question: every Skill the addressed
bot has, whether the bot owns the row outright or a SkillSet bridges it, with
`active` meaning the same thing for all of them.

## Motivation

**The endpoint's name and its answer disagree.** The route is
`/bots/{bot_id}/skills` and its response model is `Skill` — not `LocalSkill`.
Nothing in the wire says "Local only"; the restriction lives in a
`git_path LIKE 'local://%'` predicate three layers down. A caller has no way to
discover it and no other operation to ask instead: there is no "list this bot's
Skills" anywhere on the surface.

**A bot's Skills come from two places, and the listing knows one.** A bot
reaches a Skill either by owning its `ac_skill` row (`bolt_id` — every Local
upload) or through an `ac_skill_set` bound to it. The second route is how every
shared asset arrives, and it is invisible here today.

**`active` is already a single fact; the listing just cannot see all of it.**
`ac_bot_skill_installation` holds the bot's active desired state, and both
activate/deactivate commands write it. The runtime projection reads it *and*
active-SkillSet membership. Ordinary SkillSet members are materialized into
Installation lazily — only before a runtime reconcile or a Service Bot build —
so a bot can be running a Skill that has no Installation row yet. A listing that
reads Installation alone would call such a Skill inactive while the bot is
running it.

**The default-set exclusion is the owner's real answer.** Removing a Skill from
a Default SkillSet writes an exclusion row rather than deleting the membership.
A listing that ignores exclusions would show back the exact Skills the owner
removed.

## User Stories

- As a product frontend, I want one call that returns everything the addressed
  bot can do, so a capability page does not have to union three sources.
- As an API caller, I want `active` to mean the same thing for a market Skill as
  for an uploaded one, so I can render one list.
- As a bot owner, I want a Skill I removed from the Default SkillSet to stay
  gone from the listing.
- As a bot owner, I want a Local Skill I deactivated to stay inactive — a
  listing must never turn it back on.
- As a bot owner, I want a Skill added to one of my SkillSets through the
  internal API to show up here with the right `active`, without my having to
  activate it a second time through this API.

## Acceptance Criteria

### Which rows the listing contains

1. The listing includes every `ac_skill` row the bot owns (`bolt_id` +
   `user_id`), whatever its source prefix — not only `local://`.
2. The listing includes every Skill bridged to the bot by a SkillSet the bot
   has: its own ordinary Sets, its own legacy Default Set, and the platform
   Default Set for its engine.
2b. The listing includes every Skill the bot activated directly. Activating a
   shared asset that belongs to no SkillSet writes an `ac_bot_skill_installation`
   row and nothing else — the Skill row still names another owner and another
   bot — so this is a third way the bot reaches a Skill, and the runtime
   already projects it.
3. **`ac_default_skillset_skill_exclusion` is the only thing that removes a
   row.** A Skill the owner excluded from a Default Set is absent; nothing else
   about a Skill — its source prefix, which Set reaches it, whether it is
   active — takes it out of the listing. A Skill the bot owns outright is still
   held by criterion 1 even when an exclusion row names it, because criterion 1
   does not go through a Set. An excluded member's Installation row is left
   alone in both directions: removing it from the Set is what makes the Skill
   directly controllable again, so activate/deactivate owns it from then on and
   the repair must not speak for it.
4. Each Skill appears once, however many routes reach it.

### What `active` means, and why the repair runs first

5. `active` in the response — and the `active=true|false` **filter** — is read
   from `ac_bot_skill_installation` and nothing else. One fact, one place.
6. Because of criterion 5, that table must be right *before* the filter runs.
   So the operation first completes the bridge: a Skill a SkillSet brings to
   the bot has no row of its own tying it to that bot, and the repair writes
   one. A member of an active Set gains the row it was missing; a member of an
   inactive Set loses the row it should not have. This is why the write exists
   — an unrepaired Installation table makes `active=true` silently omit Skills
   the bot is running, and `active=false` return Skills it is not.
7. The repair is scoped to SkillSet membership. A Skill the bot owns outright
   and no Set reaches is untouched: its Installation row is exactly what the
   last activate/deactivate command left, and no listing changes it.
8. `active`, `keyword`, and pagination apply to the whole merged list —
   deduplicated and filtered before the page is cut, so `total` counts matches,
   not candidates.

### Unchanged

9. The response schema, envelope, status codes, and authorization are the same.
10. Repeating the call changes nothing further: the repair is convergent.

## Out of Scope

- A source field on the `Skill` response. The additive `source=LOCAL` query
  filter is supported for the legacy-compatible “Bot uploads” page; it filters
  exact Bot-owned `local://` rows without changing the default complete-list
  semantics. Naming every returned row's source remains out of scope.
- `GET /skills/{skill_id}` and the three asset operations under it. They stay
  Local-only; only the collection changes.
- Runtime reconciliation. The repair writes desired state; it never touches
  symlinks, Passport, or a device. It does not need to: what it writes is what
  `list_bot_active_assets` already projects from the same membership, so the
  bot's runtime does not change — only the listing's account of it becomes
  accurate.
- ~~The Default-Set exclusion's absence from the runtime projection.~~
  **Brought into scope, 2026-08-23, at the domain owner's direction.** It was
  listed here as pre-existing and out of scope, on the reasoning that a listing
  is the wrong place to change what a bot runs. Review showed that reasoning
  did not survive the rest of the change: once the guards honour an exclusion,
  `POST /skills/{id}/deactivate` on an excluded Repo Skill succeeds while
  `list_bot_active_assets` keeps projecting it, so the command reports success
  without holding — the same defect this change exists to remove. The
  projection now applies exclusions to non-Local Default members, which is the
  rest of the rule `includes_default_skill_member` already states and that both
  the listing and `SkillSetControlPlaneRepository.list_skills` already apply.

  This changes what a bot runs: a Repo Skill its owner excluded stops being
  projected at the next reconcile. That is what excluding it meant.

- **A `center://` membership that names a row instead of an identity.** No
  membership writer populates `ac_skill_set_skill.skill_uuid`, so the
  `center://` half of the resolution joins on a column that is always NULL and
  reaches nothing. That is what `get_skills_in_set_for_env` already does, and
  the listing matches it deliberately — one resolution, not two. Fixing it
  means changing the membership writers, which is its own piece of work.

- **Carrying a Default exclusion across a replaced Center row.**
  `ac_default_skillset_skill_exclusion` names its Skill by numeric `skill_id`
  while a `center://` membership resolves by `skill_uuid`. Within one tenant
  and env `uk_skill_uuid` keeps at most one row per identity, so a *new*
  published version cannot coexist with the excluded one and the comparison
  cannot drift that way. It can drift the other way: if the excluded row is
  deleted and the identity re-created, the exclusion points at an id nothing
  resolves to and the re-created Skill comes back un-excluded. Carrying it
  needs a `skill_uuid` column on the exclusion table — a schema change, and
  its own piece of work.

## Decisions

1. **The repair runs in a GET.** `core/skill_center/README.md` states that the
   materializer "never … runs in HTTP GET/list" paths. This change makes the
   listing an exception and says so in that README. The alternative — deriving
   `active` as "installed OR claimed by an active Set" and never writing —
   would have to push that same derivation into the `WHERE` clause, because
   `active` is a filter that runs before paging: `total` and the page boundary
   both depend on it. That is two ways to compute one fact, one of them living
   in SQL. Repairing first keeps `active` a single column lookup and keeps
   Installation the authority the rest of the domain already treats it as.
2. **The repair deletes as well as inserts.** `set_active(False)` already
   removes its members' rows; the delete half only closes a crash window —
   without it a listing would report a Skill active after its Set was
   deactivated by a command that died mid-way.

   This is only safe because membership is the *sole* authority for a Skill
   inside a Set. Review found it was not: `_reject_ordinary_skill_set_member`
   refused a direct activate/deactivate for a member of an ordinary Set but
   permitted one for a member of a Default Set — so a deactivate could delete
   an Installation row that the next listing put straight back, undoing the
   user's command with no error. The command now refuses direct state for a
   member of **any** Set that reaches the bot, Default Sets included
   (`_reject_skill_set_member`). With one authority there is no second writer
   for the repair to contradict, and the repair needs no exception for
   bot-owned rows.

   The Repo activation path has a second, separate guard
   (`_require_no_normal_skill_set_membership`) that carried the same exemption,
   and it is removed there too. That one matters more, not less: a Default Set
   is exactly where shared `git://` Repo Skills live, so it is the path where
   the defect actually bites.
3. **The repair has no per-source branch.** A Default SkillSet carries `git://`
   Repo Skills only — stated by the domain owner, 2026-08-23. So the question
   "should a `local://` member of a Default Set be installed?" has no instance,
   and the repair does not ask it. Every non-excluded member of an active Set
   is installed, whatever its prefix.

   Two pieces of dead code state a rule for that non-existent case and should
   not be read as contradicting this: `includes_default_skill_member`
   (no callers) and `local_skill_upload_service._ensure_default_set_membership`
   (no callers). The live `list_bot_active_assets` filter that drops `local://`
   from a Default Set is inert for the same reason. None of them is touched
   here.
4. **A Skill in both an active and an inactive Set counts as active.** The
   surface's own invariant is that a resource belongs to at most one ordinary
   Set, so this is a repair choice for malformed data, and the safe direction is
   not to uninstall something a live Set claims.
