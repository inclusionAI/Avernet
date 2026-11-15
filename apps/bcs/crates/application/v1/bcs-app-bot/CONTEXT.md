# bcs-app-bot Context

## Provides

- `BotSelfServiceImpl` authenticates an Agent identity token through the injected
  `AgentIdentityPort`, then asks the registry Core for persisted registration.
  Invalid/non-Agent identities, verifier unavailability, ambiguous registration,
  and storage failure stay distinct application errors. Missing registration is
  a successful read with no Bot, and token refresh does not change identity.

- `BotServiceImpl`, the transport-agnostic implementation of the BCN V1 Bot
  control-plane Service API.
- Human-Principal authorization, Bot/Human projections, candidate visibility,
  reachability computation, and owner-scoped updates.
- Candidate friend relationships come only from approved Connect/edge
  relationships. Both `/candidates` and the deprecated `eligible-candidates`
  alias share one strict, bounded read of approved default-profile Bot edges
  through the fallible `ConnectService` page API. The first page fixes the
  request's total; later page drift, short pages, duplicate identities or
  persistence failures fail the whole request instead of returning a partial
  or empty friend set. No cross-request friend cache exists, and candidate or
  projection reads are accounted separately from friend-page SQL. The read
  still costs O(F) returned rows and memory for F friends, and each paged
  JOIN/COUNT may rescan the same edge/profile set, so the observed
  `max(1, ceil(F/100))` page-query count does not by itself prove
  large-scale performance.
- `/candidates/search` shares the same single strict approved-edge read per
  request and reuses that set across the semantic and name-fallback branches;
  empty queries skip the friend read entirely.
- Shared Bot responses (including `/bots/mine`) expose the current `avatar_url`.
  Projection reads only returned physical Bot IDs through one batched registry
  Core call after authorization/filtering/pagination. Empty and Human-only pages
  issue no avatar read. Missing avatars and Humans serialize null; read failures
  propagate. Mine reads avatars only after accepting the sampled page, including
  its drift retry and composition comparison. Rejected attempts issue no avatar
  read and retain `bot_list_changed`; an accepted page's avatar failure remains
  an application error. Projection uses that attempt's runtime sampling and
  relation labels. Other Bot queries reuse the same collected physical IDs for
  runtime activity and avatars.

## Consumes

- `bcs-service-api` application contracts, Core service contracts, and shared
  transport-agnostic contract types.
- The non-repository `AgentIdentityPort`, implemented by deployment adapters.
- Pure utility crates for asynchronous traits and standard collections.

## Allowed dependencies

- `service-api/*`
- Utility crates such as `async-trait`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*`
- Store or Legacy service implementations outside tests
- Repository ports in production code; persistence orchestration belongs behind
  a Core service contract

## Configuration

- The composition root injects the environment and Core service
  implementations.
- This crate must not select implementations or inspect environment variables.

## Runtime ownership

This crate owns the V1 Bot control-plane use-case facade, mounted behind the
trusted Human Principal verification boundary. The separate internal self facade
requires Agent identity verification itself and supports Agents before registration.

Bot-authority additions (bot-authority spec §5/§6) stay inside this crate:
`BotAuthorityServiceImpl` serves the twelve frozen
`/api/v1/collaboration/` authority routes from one injected Core/Hook
pair. Admission is dual without fallback order violations — Human first
(keeping the invite-code gate exactly as configured), then a Trusted App
whose allow rule (env/tenant/bot_ids/actions) comes from the composition
root as an already-validated `AuthorityServicePolicy`; a Human rule is
never routed to an App fallback and vice versa. The adapter never
constructs authority mutations: the application builds them from the
verified identity. Unknown-action/wildcard scope rules are configuration
load errors, not runtime permissions.

## Tests

- `cargo test --package bcs-app-bot --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-app-bot --all-targets --manifest-path src/bcs/Cargo.toml`

## Parent discovery and selected Bot

`get_agent_me` verifies the injected Agent identity and returns a bounded page of
undeleted Bot summaries, including offline and disabled affiliations. Zero or
multiple Bots succeed without returning credentials. `get_me_with_actor` retains
a single-Bot projection, using a concrete Static Token or verified parent plus
optional Actor; subbot parent credentials cannot implicitly select a child.
