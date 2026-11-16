# Edge-owned friendship cutover implementation plan

**Goal:** Switch legacy friend routes to edge-owned membership and requests without history migration or schema changes.
**Architecture:** The compatibility facade projects ConnectService pages into the existing legacy DTO. Bootstrap injects only connect and registry; no legacy list delegate. Withdraw only the two explicitly authorized, unexecuted PR migrations.
**Tech Stack:** Rust, Tokio, SQLite/MySQL migration runners.

## 1. Pin the approved contract with failing tests
- [x] Replace migration import expectations in `bcs-db-migrations/src/migrations/friend_cutover_tests.rs` with unchanged-history/no-import assertions.
- [x] Add edge-only Bot/Human list, self-only access, DB failure and old-request invalidation tests under `bcs-edge-permission/src/tests/`.
- [x] Run `BCS_STATIC_VERSION=1 cargo test --locked -j 2 -p bcs-db-migrations friend_cutover` and the focused facade tests; record expected failures.

## 2. Withdraw the unauthorized-for-release migration behavior
- [x] Remove MySQL 046 / SQLite 044, their registration/startup guard, and obsolete migration helpers. Restore only the affected tail assertions/admin full-chain flow to the pre-repair versions.
- [x] Keep all earlier migration SQL/checksums untouched. Test normal reruns against legacy rows and existing new requests.

## 3. Switch the compatibility friend list
- [x] Remove `legacy_list_delegate` from `EdgeConnectFriendService` and its bootstrap/test callers. Retain self-only authorization and hidden-target behavior.
- [x] Read `list_friends_paginated` in pages of 100, bounded by the first page's total; batch `try_get_many` and strict runtime activity, retain Bot-only live registry projection and effective-online semantics. No legacy read fallback.
- [x] Keep request authorization/error and collaboration writeback repairs; verify revoke and fresh create paths.
- [x] Add a durable bootstrap regression with old pending rows and HTTP not-found/new request/list assertions.

## 4. Verify and update owning knowledge
- [x] Run edge-permission/store, friend, invitation, migrations, HTTP/admin and focused bootstrap suites plus workspace compilation.
- [x] Check diff whitespace, changed source size, links and migration diff scope. Update rollout/context and mark preceding spec superseded only for changed decisions.
- [x] Report actual passes, failures and gaps; leave changes on the existing branch for review.
