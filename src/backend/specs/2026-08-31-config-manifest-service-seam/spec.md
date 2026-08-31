# Bot Config — One Seam for the Five Categories Apply Touches

## Summary

Manifest apply will reach the same five bot-configuration categories the public
API already exposes — `skills`, `identity`, `resources`, `mcp`, `engine_config`.
The rules those categories enforce are not, today, in a place apply can reach:
they are module-private functions inside `openapi_v1` router bodies, called from
handler code and callable from nowhere else.

This moves them out — into `core`, one declared home per category, with a table
that names them and a test that holds the router and the table on the *same
function object*. Nothing about what any check decides changes. Every caller who
is admitted today is admitted afterwards, with the same status code and the same
message, and no existing test is modified.

This is the mechanism W4's materializers consume. It ships before them for the
reason W10's note in the work-items doc gives: doing it afterwards means writing
these checks twice and deleting one copy.

## Motivation

**Apply runs the same rules or it drifts.** `POST
/openapi/v1/bots/{bot_id}/config-manifest/apply` will converge a bot's skills,
identity files, resources, MCP servers and engine config toward a document.
Every one of those writes has a rule attached to it on the public surface — a
path that may not escape the workspace, a file that may not be written because
it is read-only, a package that must really be a ZIP, a bot that must resolve
for the named owner. Apply performing those writes *without* those rules is a
second, weaker surface onto the same state. Apply performing them with a
hand-copied second implementation is the same thing on a delay: two copies of a
security rule diverge, and the one that diverges silently is the one nobody is
reading.

**The rules are structurally unreachable.** This is not a matter of
inconvenience. `_safe_path`, `_require_path`, `_reject_read_only` and
`_file_coords` are underscore-private module functions in
`resources/router.py`. `_require_addressed_bot`, `_require_skills_grant` and
`_directory_relative_paths` are the same in `skills/router.py`.
`_engine_config_target` and the ownership guard spelled
`bot_service.get_bot(bot_id, owner_id)` are inline in `bots/engine_config.py`.
Reaching any of them from `core` would be an adapter import from core —
backwards through the layer boundary, and forbidden.

**The architecture already says these are in the wrong place.** Rule 7 permits
an adapter to do request parsing, auth interpretation, protocol validation,
serialization and error mapping. It forbids the adapter to own domain policy.
"A workspace path may not contain `..`", "a dotfile is not writable through this
API", "a bot's engine config is addressed by the entity on its record" are
domain policy by any reading — they are true of the bot's workspace, not of
HTTP. They sit in adapters because that is where the endpoints that needed them
were written, and no second caller existed to force the question. Apply is the
second caller.

**Four groups compute the same triple four different ways, and nothing shows
it.** `(entity_type, entity_id, engine_type)` is what every one of these
categories needs to address a bot's storage. `resources` builds it from
`("staff", owner_id, resolve_runtime_engine_for_bot(...))` with no ownership
guard at all. `engine_config` builds it from the bot record's own `entity_id`,
`entity_type` and `active_engine`, behind `bot_service.get_bot`. `identity` and
`mcp` hardcode `"staff"` and use `owner_id`, and need no engine. Those are four
different answers to one question, each invisible inside a different private
function. Making them visible is worth doing on its own; the fact that apply
would otherwise have to guess which one to copy is what makes it urgent.

**The precedent exists and is one year old at most.** #1323 built the
collaborator authorization seam and #1362 moved 87 operations onto it. The shape
is settled: one table, one thing that reads it, and omission that fails
structurally rather than by convention. This feature is that shape applied to a
different question, deliberately, so that the surface has two seams and not
three shapes.

**And there is a third caller, which is why the seam cannot be shaped around a
bot record.** W13 (#1696) creates a bot *with* a manifest, and its acceptance
criteria require the manifest to be validated at **preflight** — in
`create_bot_with_authorization`, beside the quota, name and engine checks, and
*before* Passport is requested. At that moment there is no bot record: phase 1
allocates a `bot_id` and mints a Passport identity, and the record is not
written until phase 2 (`complete_bot_authorization`), after the user clicks the
authorization link. The code says so itself — *"No token yet → authorization
pending; nothing is created."*

A seam that resolves everything through `bot_service.get_bot(bot_id, owner_id)`
is unusable there. W13 would find it unusable, write a second validation copy,
and reproduce the exact defect this feature exists to prevent — this time on the
path where being wrong is most expensive, because the alternative to catching it
at preflight is catching it *after* the user has completed a Passport
authorization that cannot be un-burned.

The three coordinates every category needs — `entity_type`, `entity_id`,
`engine_type` — are all available in phase 1. They are on `BotCreateSpec`, as
request parameters. They are simply not on a record yet. So the seam must take
them as values rather than fetch them, which is a shape decision, not a feature.

## User Stories

- As the engineer building apply (W4), I want to call the same check the
  category endpoint calls, so that "apply enforces what the API enforces" is a
  property of the code rather than a claim in a review.
- As the engineer building create-with-manifest (W13), I want to run every
  per-category validation at preflight with no bot record in existence, so that
  an invalid manifest is refused before the user is sent through a Passport
  authorization that cannot be taken back.
- As a backend engineer adding a rule to one of these five categories, I want
  one place to add it, so that adding it to the endpoint and forgetting apply is
  not possible.
- As a reviewer, I want to see every rule a category enforces in one list, so
  that "what governs writes to a bot's resources?" is a question with one
  answer.
- As a security reviewer, I want the router and apply proven to hold the *same
  function object*, so that they cannot drift onto two implementations that
  merely look alike.
- As an integrator calling the public API today, I want this change to be
  invisible to me — the same requests admitted, the same requests refused, the
  same status codes and messages.

## Acceptance Criteria

These are W10's four criteria from `docs/bot-config-manifest/work-items.zh-CN.md`
§5, restated with the evidence each one is proven by.

### The seam

- [ ] For each of the five categories, the checks the public API enforces are
      declared in one module under `core`, and both the router and a non-HTTP
      caller reach them by importing that module. A non-HTTP caller needs no
      `Request`, no FastAPI dependency resolution, and no running app to get the
      same answer the router gets.
- [ ] Every check that guards a public-API **write** in these five categories is
      reachable from outside its router. No module-private function in
      `resources/router.py`, `skills/router.py`, `identity/router.py`,
      `mcp/router.py` or `bots/engine_config.py` performs a check that a handler
      is the only possible caller of.
- [ ] The declared checks raise domain errors from `core`, never
      `HTTPException`. The routers keep their existing error mapping, which is
      how the status codes stay identical.

### Usable before a bot exists

- [ ] Every category's **validation** runs with no bot record — given the
      declared values and the coordinates as arguments, it reaches no
      repository and no `get_bot`. This is what lets W13 validate at preflight.
- [ ] The coordinates a category needs are produced by **two constructors
      returning one type**: one reading an existing bot record, one reading the
      create request's parameters. Not two coordinate types, and not two
      validation paths — the same rule W1 imposes on the capability resolver
      (*one function, two entry points; never two implementations*).
- [ ] A test constructs coordinates from request parameters alone and runs every
      category's validators against them, with no bot record anywhere in the
      fixture. If that test needs a record to pass, the split did not happen.
- [ ] The record-reading constructor keeps today's ownership guard exactly.
      Splitting the shape must not weaken the existing-bot path, which is the
      one that ships now.

### The table, and the guarantee it carries

- [ ] A table names, for each of the five categories, the checks that govern it.
      A category apply touches that is absent from the table fails a test that
      names it.
- [ ] The table and the routers are proven to hold **the same function object**,
      not two functions with the same behaviour. This is the criterion that
      makes the seam load-bearing rather than decorative: a future edit to the
      router's copy cannot leave the table's copy behind, because there is one
      copy.
- [ ] `engine_config` carries a row even though W4 excludes it from phase 1
      (§4, X2/T3). The seam is where the category will plug in when its
      materializer returns; leaving it out now means discovering it is missing
      later.

### Nothing moves

- [ ] Every request the five groups admit today is admitted afterwards, and
      every request refused is refused, with the same status code and the same
      message. Proven by the existing endpoint tests, which pass **unmodified**.
- [ ] No existing test file is edited. If one must be, the change is not inert
      and the reason is stated rather than absorbed.
- [ ] The four different `(entity_type, entity_id, engine_type)` resolutions are
      preserved as four, not merged into one. Merging them would change
      behaviour for at least three groups; see *Decisions* 3.
- [ ] `AUTHORIZATION`, `ADMISSION` and the collaborator seam are untouched. No
      row changes mode, no route changes its bar.

## Decisions

Settled here rather than left open:

1. **Apply is an ordinary operation on this surface, so it needs no invented
   actor.** `POST .../config-manifest/apply` carries its own `ADMISSION` and
   `AUTHORIZATION` rows (W4 adds them) and its caller is adjudicated by the
   existing collaborator seam at the door, exactly like every other operation.
   There is no system principal, no stored manifest author, and no second
   identity concept. The seam's functions therefore take `caller_id` and
   `owner_id` as ordinary arguments, supplied by whichever public operation is
   driving — apply's route, W13's create route, or the lifecycle routes W8
   wires. This was the one genuine fork in the feature and it is closed: the
   answer is that there was no fork, because apply is an HTTP operation like the
   rest.

2. **The collaborator level check is not re-implemented here, and must not be —
   because apply declares an owner-level bar of its own.** It is already
   declared once, in `authorization.py`, and enforced by `PublicAPIRoute` for
   every operation including apply's. A per-category re-check inside the seam
   would be a second adjudication of a question that already has one home — the
   precise defect this whole line of work exists to remove. What moves into the
   seam is the *ownership and addressing* guard the category handlers perform on
   top of it (`bot_service.get_bot(bot_id, owner_id)` and the entity-coordinate
   resolution), which the collaborator seam does not perform and never has.

   This is sound only given *Apply Declares Its Own Bars* below. `Check(OWNER)`
   means every caller who reaches apply could have performed each of its writes
   directly, so there is nothing left for a per-category level check to refuse.
   Lower that bar and the reasoning collapses: per-category adjudication becomes
   a real requirement of this seam and this decision reopens. That is what the
   dominance test in that section exists to catch, and why it is named as W4's
   work rather than left to whoever notices.

3. **The four divergent target resolutions are preserved, not unified.** They
   really do differ — `resources` performs no ownership guard, `engine_config`
   reads the bot record's own entity, `identity` and `mcp` hardcode `"staff"`
   against `owner_id`. Unifying them would change who is admitted on at least
   three groups, which this change is forbidden to do. So each moves as it is,
   into its own category's module, where the difference is at last *visible*.
   Naming the divergence is this feature's contribution; resolving it is a
   behaviour change and belongs to whoever takes it, with its own argument. It
   is recorded in `plan.md` as a follow-up rather than dropped.

4. **The routers are rewired in this change, not left as adopters-in-waiting.**
   The collaborator seam deliberately shipped with no adopter (#1323 *Decisions*
   4) because adopting it was a behaviour change per group. This seam is
   different: adopting it is a move, not a decision, and the acceptance
   criterion "no check reachable only from inside a router body" is unsatisfiable
   without the rewire. A seam that both callers do not actually use is a third
   copy, not a seam.

5. **Validation is separated from coordinate resolution, because one needs a
   bot record and the other must not.** The checks split cleanly in two:
   *validation* (is this path safe, is this file writable, is this file type
   allowed, does this skill belong to this bot) depends only on declared values,
   and *coordinate resolution* (`entity_type`, `entity_id`, `engine_type`, and
   the ownership guard that comes with reading a record) depends on where those
   values come from. Only the second has two sources — a bot record for an
   existing bot, a `BotCreateSpec` for one being created — so only the second
   gets two constructors, and they return the same type.

   This is shaped in now rather than retrofitted for W13, for the reason the
   whole feature exists: a seam W13 cannot call is a seam W13 will duplicate.
   Retrofitting it later means changing every call site after they exist instead
   of before, and the cost today is one extra constructor per category.

   It is worth being precise about what this does *not* claim: creating a bot
   with a manifest is W13's work, not this feature's. What is in scope here is
   only that the seam's shape does not make W13 impossible to write against.

6. **Category checks stay per-category rather than behind one uniform
   protocol.** A resources path check, a skills package check and an identity
   file-type check have nothing in common but their category; forcing them
   through one signature would invent an abstraction to fit three unlike things
   (Rule 19 — abstract after two examples, and these are not two examples of one
   thing). What is shared is the *table* that names them and the guarantee that
   the router and apply hold the same object, which is where the value is.

## Apply Declares Its Own Bars

Not implemented here — this feature writes no `AUTHORIZATION` or `ADMISSION`
row. It is recorded because it was settled while specifying this seam, it is
what makes *Decisions* 2 sound, and the rows are written in three **other**
sessions. Losing it between them is what this section prevents.

**The rule is the one `admission.py` already states:** *"an operation's mode
follows from its shape — which identities it takes, and how it resolves the bot
it acts on — not from taste."* So a manifest operation declares what applying a
manifest requires, decided on its own shape. It is **not** derived as a maximum
over the categories it happens to touch — that rule would need recomputing every
time a category moved, and a bar nobody recomputes is a bar that silently rots.

### The declaration

| | Value |
| --- | --- |
| `ADMISSION` | `AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT` |
| `AUTHORIZATION` | `Check(PermissionLevel.OWNER, EDIT_LOCK)` |

**`Check(OWNER)`** — applying a manifest rewrites a bot's whole configuration:
its skills, identity files, resources, MCP servers and startup script. That is
an owner-level act on its own terms, independent of what any one category's
endpoint requires. `OWNER_SCOPED` was rejected: it is explicitly scaffolding
(*"no collaborator dimension has been decided … → becomes `Check(level)` when
\#906 / \#907 decide the bar"*), and a new operation should not be born into a
mode the table is migrating away from.

**`EDIT_LOCK`** — apply is a broad mutation, and every comparable one on this
surface carries the lock (channel writes, `lifecycle/advance`, skill-set
activate). Applying a manifest over another collaborator's in-flight edits is
the exact collision the lock exists to stop. Bots with no collaborators pass
without one, so the personal-bot path is unaffected.

**`GRANT_CHECKED_ADDRESSED_BOT` is not a free choice — it follows from
`Check`.** `require_check`'s gate declares `OwnerIdDep` → `resolve_owner_id` →
`AddressedBotGrantDep`, so any row carrying `Check(...)` pulls in
`require_granted_addressed_bot`, and `test_admission_inventory.py` holds each
route to the dependency its mode calls for. The surface has exactly two coherent
pairings — `Check(...)` with `GRANT_CHECKED_ADDRESSED_BOT`, and `OWNER_SCOPED`
with `GRANT_CHECKED_OWN_BOT` — and mixing one from each is what
`principal.py`'s own docstring calls the surface's oldest defect. Naming this
because the pairing looks like two decisions and is one.

### The safety net that makes an independent bar safe

A bar decided on its own terms is more durable than a derived one, but it gives
up the property the derived rule had for free: that apply can never exceed the
categories it materializes. Recover it with a test rather than a rule anyone has
to remember:

- [ ] **(W4)** For every category apply can materialize, apply's declared bar is
      at least the bar of that category's own write operations, and its
      admission mode is no wider. Adding a category whose endpoints require more
      than apply does fails this test.

Without it, a later well-meant "let collaborators use manifests, drop apply to
`MEMBER`" silently grants `MEMBER` the ability to overwrite a bot's `SOUL.md`
through a manifest — which `PUT /openapi/v1/bots/{bot_id}/identity/{file_type}`
refuses them, because that operation is owner-only. Three of the six categories
are owner-only today; the manifest must not become the way around them.

### The rows, and whose session writes each

| Row | Whose | Note |
| --- | --- | --- |
| `PUT /openapi/v1/bots/{bot_id}/config-manifest` | W1 | §2.6 makes `PUT` take effect immediately, so it **is** an apply trigger and takes apply's bars. Easy to miss: it reads like an ordinary document write |
| `POST /openapi/v1/bots/{bot_id}/config-manifest/apply` | W4 | Plus the dominance test above |
| W13's create endpoint | W13 | The caller creates their own bot, so ownership holds by construction; the manifest rides the create request's own admission |

`DELETE` of the manifest materializes nothing (W4: *删除 manifest 什么都不删*), so
it carries no lower bound from this section and is decided on its own shape like
any other operation.

## In Scope

- One `core` module per category holding the checks that category's public
  endpoints enforce, moved verbatim.
- The validate / resolve split, and the second coordinate constructor that reads
  create-request parameters instead of a bot record.
- The table naming them, and the test that proves router and table share one
  function object.
- Rewiring the five router groups to call the moved functions.
- The structural test that no in-scope router keeps a handler-only check.
- A `README.md` with a Context Boundary block for the new module, per
  `docs/arch/context-boundary-format.md`.

## Out of Scope

- **Changing what any check decides.** Inert on arrival is the whole
  constraint.
- **Unifying the four target resolutions**, per *Decisions* 3.
- **Any manifest code** — no schema, no storage, no apply, no `core/bot_config_manifest/`.
  Those are W1 and W4. This change is consumed by them and knows nothing about
  them.
- **Creating a bot with a manifest.** That is W13 (#1696) and it is a large item
  in its own right — a new async public endpoint, a polling status surface,
  manifest storage before the bot record exists, and integration with the
  two-phase Passport flow. What this feature owes W13 is a seam it can call at
  preflight; building the endpoint is not this change's work, and `create_flow.py`
  is not touched here.
- **The collaborator authorization seam**, `AUTHORIZATION`, `ADMISSION`, and the
  app-grant dependencies. Untouched, per *Decisions* 2. In particular this
  feature writes **no** manifest rows — *Apply Declares Its Own Bars* records
  what they must be, for W1, W4 and W13 to honour; it does not implement them.
- **The other router groups.** Only the five categories apply touches.
- **The internal API and the console routers.** Nothing on those surfaces
  changes, including `adapters/http/resources/file_router.py`, whose
  `_resolve_params` the resources group mirrors.
- **`cli_tools`**, which has no endpoint today and no materializer until W9.

## Open Questions

None outstanding. The actor question is closed by *Decisions* 1.

## Follow-ups

- **The four target resolutions** (*Decisions* 3). Once visible in one place,
  deciding whether `resources` should carry the ownership guard the other three
  carry is a real question with a real answer — and a behaviour change that
  needs its own spec.
- **W4** consumes this. Its materializers for `mcp` and `script` are the first
  callers; `identity` and `skills` follow in W5, `resources` in W6, and
  `engine_config` whenever X2/T3 lets it back in.
- **W13** consumes the record-free half at preflight. Nothing here builds its
  endpoint; this only guarantees the seam is callable from a place where no bot
  record exists.
