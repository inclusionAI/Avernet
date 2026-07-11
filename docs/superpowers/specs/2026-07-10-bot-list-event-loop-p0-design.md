# Bot List Event-Loop P0 Fix

## Problem

`GET /api/bots/by-owner-or-collaborator` is an async route that performs a
synchronous bot-list build on the event-loop thread. The list build also fans
out live BaaS status queries for desktop bots and waits up to five seconds.
When BaaS is slow, the worker cannot serve unrelated requests such as
`GET /api/bots/ceiling`.

## Scope

This P0 change has two parts:

1. Run the complete synchronous list build, including path enrichment, outside
   the event-loop thread.
2. Stop querying BaaS from the owner-or-collaborator list. The list returns the
   persisted desktop-bot status maintained by the existing reconciliation
   path. Single-bot operations that explicitly need live status continue to use
   `resolve_desktop_live_status`.

The change does not alter API schemas, authentication, policy lookup, bot
switch behavior, permission queries, or frontend polling.

## Design

The HTTP adapter delegates synchronous response-data construction to a small
helper through `asyncio.to_thread`. The helper owns the repository/service call,
engine path enrichment, and default-bot calculation, so no synchronous portion
of the list build remains on the event loop.

`BotService.list_bots_by_owner_or_collaborator` no longer calls the bulk live
status merge. The unused bulk thread-pool implementation and its five-second
budget are removed. `resolve_desktop_live_status` remains available because
single-bot safety checks have different freshness requirements.

## Failure Behavior

Repository, permission, and path failures keep their current HTTP error
mapping. Removing best-effort BaaS enrichment means a BaaS outage cannot delay
or fail this list endpoint; persisted status is returned instead.

## Verification

- A service test proves list reads preserve persisted desktop status and never
  invoke the BaaS status client.
- An async router test blocks synchronous list construction in a worker thread
  and proves an event-loop heartbeat still executes before the list is
  released.
- Existing bot-management endpoint and desktop-status resolution tests remain
  green.
