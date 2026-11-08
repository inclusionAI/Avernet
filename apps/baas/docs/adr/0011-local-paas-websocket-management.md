# ADR-0011: Local PaaS management over WebSocket

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

Besides managed cloud providers (ADR-0006), BaaS orchestrates devices on
machines it does not control — user laptops and desktop boxes running a local
`mng` daemon. Those machines sit behind NAT/home Networks, initiate their own
outbound connections, and join/leave unpredictably. The server must track
them, route commands to them, and survive multi-replica deployments where the
WebSocket lands on a different instance than the one serving the API call.

## Decision

Manage local machines through an inbound WebSocket management channel with
server-side connection ownership:

- machines dial the management WebSocket with a `machine_id` (plus
  human-readable `machine_name`), authenticated by session cookie — the
  daemon is always the dialer, so no inbound firewall holes are needed;
- the connection manager (`api/paas/_protocols.py`) tracks per-machine
  liveness with heartbeat sweeps, answers `is_connected`, and routes
  `send_command(machine_id, command)` / request-response calls with
  correlation ids; background sweep tasks run heartbeat detection and
  graceful shutdown closes connections;
- daemon-side state (session relay for bot chats on that machine) persists
  across API calls in the `ws_relay_session` repository, so command execution
  is resumable, not stateless-fragile;
- across replicas, the desktop service route book (`core/service/paas/
  desktop/instance_router`, `worker_router`) maps a machine or bot to the
  owning instance so commands find the right connection even in multi-box
  deployments;
- recommended deployment terminates WebSocket upgrade at the front proxy
  (`proxy_read_timeout`, upgrade headers) as documented in the protocol notes.

## Consequences

**Positive:** NAT traversal for free; heartbeat sweeps give explicit
online/offline signal; multi-replica routing works without sticky sessions at
the API layer; relaying chat via stored relay sessions survives reconnects.

**Negative:** a machine is reachable only while its connection is open (no
store-and-forward for commands to offline boxes); command latency is
bound to the relay hop.

**Risks:** unbounded machine connections on one instance — instance routing
and duplicate-connection rejection (documented policy violation close) are
the guardrails to keep; they are protocol-level rules, so new server
implementations must retain them.

## Evidence

- community: `src/secbaas/community/api/paas/_protocols.py` — `machine_id` connection manager, heartbeat sweeps, command routing
- community: `src/secbaas/community/core/repository/ws_relay_session/` — relay-session persistence
- community: `src/secbaas/community/core/service/paas/desktop/instance_router/` and `worker_router/` — cross-instance routing
- enterprise: `docs/LOCAL_MNG_WEBSOCKET_PROTOCOL.md` — wire protocol (endpoint, message types, close-code policy 1008/1006/1011)
- enterprise: `docs/LOCAL_WS_RELAY_DESIGN.md` — relay design notes