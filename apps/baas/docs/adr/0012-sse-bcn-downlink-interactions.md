# ADR-0012: SSE downlink streaming for BCN interactions

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

Bot conversations are streaming and interactive: answers arrive token by
token, the bot can ask the user structured questions (ask-user) mid-flight,
and either side can abort. Downlink delivery must multiplex these concerns
over plain HTTP without client-side WebSocket infrastructure, and must give
every interaction a citable identity for resolution and audit.

## Decision

Use **Server-Sent Events** as the downlink transport for BCN
(bot-communication) interactions, with a conversational interaction identity
layer:

- downlink responses stream from a dedicated SSE service
  (`core/service/sse/` with a converter registry + default converter) and are
  exposed through the `api/sse` routes; the BCN service
  (`core/service/bcn/` + `uplink/`) owns the uplink/downlink bridge to bot
  engines (`_bcn_service.py`);
- headers are the framing device: answer-level output uses a structured
  answer header rather than ad-hoc message parsing (per the
  `bcn-answer-header` design notes);
- ask-user interactions carry structured options (including custom
  free-text values) and resolve through explicit interaction resolution keyed
  by `interaction_id` (`resolve_input.interaction_id` in
  `_bcn_service.py`) — resolution is an API action, not implicit state;
- every downlink interaction uses a **public interaction id** (distinct from
  internal record ids) so external consumers can poll/cite/abort safely;
- abort is **bot-dimension scoped** via `BotRunAbortSurface` — an abort
  targets one bot's current run, not the tenant or the stream
  (`ChatAbortInput` / `ChatAbortResult` in `_bcn_service.py`), and
  interaction warnings during resolution are logged with the public id.

The design evolved through the dated BCN/interaction design notes (see
Evidence); this ADR records the converged decision.

## Consequences

**Positive:** one-way streaming over plain HTTP works through corporate
proxies that block WebSockets; interaction identity makes ask-user flows
debuggable; bot-dimension abort cannot cascade across tenants; SSE converter
registry allows per-event-type rendering without protocol changes.

**Negative:** SSE is downlink-only — uplink actions need normal API calls;
reconnection/replay semantics must be handled at the application layer.

**Risks:** clients treating the SSE stream as chat-state authority;
mitigated by the interaction-resolution API being the only legitimate way to
use ask-user values.

## Evidence

- community: `src/secbaas/community/core/service/sse/` — SSE converter registry and default converter
- community: `src/secbaas/community/core/service/bcn/_bcn_service.py` and `bcn/uplink/` — interaction resolution, `ChatAbortInput/Result`, `BotRunAbortSurface`, `baas_interaction_id`
- community: `src/secbaas/community/api/sse/` and `api/bcn/`, `api/bot_interaction/` — downlink routes
- community: `docs/2026-08-19-baas-bcn-interaction-sse-design.md` — SSE interaction design
- community: `docs/2026-08-20-bcn-answer-header-design.md`, `docs/2026-08-20-bcn-interaction-resolve-design.md` — header framing and resolution semantics
- community: `docs/2026-08-24-bcn-ask-user-custom-values-design.md` — ask-user custom values
- community: `docs/2026-09-01-bcn-downlink-chat-abort-bot-dimension-semantics.md` — bot-dimension abort semantics