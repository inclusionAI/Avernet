# Human self friend authorization implementation plan

**Goal:** Restore Human actor-self access to legacy friend routes without
weakening live Bot management checks.

**Architecture:** Keep the existing shared HTTP authentication guard. Branch on
the loaded actor kind; Human authorization is exact canonical identity equality,
while Bot authorization continues through the injected authority port.

**Tech stack:** Rust, Axum, Tokio, existing BCS service traits and test fixtures.

## Steps

- [x] Establish the existing `authority_cutover` baseline using
  `cargo test --locked --offline --manifest-path apps/bcs/Cargo.toml -p bcs-http --test authority_cutover`.
- [x] Add `crates/adapters/http/bcs-http/tests/friends_actor_auth_contract.rs`.
  Reuse `support::bot_use_cases::RecordingBotQueryService` for typed actor
  lookup results, a recording FriendService for caller forwarding, static
  authenticated Human identity, and existing lazy/failing authority hooks.
  Route the reported GET and every shared friend guard entrypoint. Include
  other-Human, anonymous, missing-actor and Human-like Bot ID denial tests.
- [x] Run the new suite before the fix; confirm Human self access fails with
  HTTP 403 (or 500 with the failing authority hook).
- [x] In `src/routes/friends.rs`, bind `Ok(bot)` and add this branch before the
  unchanged Bot authority check:
  ```rust
  if bot.actor_kind == bcs_service_api::ActorKind::Human {
      return if actor_id == format!("human_{staff_no}") {
          Ok(())
      } else {
          Err(ActorOwnershipError::Denied)
      };
  }
  ```
- [x] Run the new tests and existing authority suite; verify current Bot
  owner/manager access, revocation, and internal-error propagation.
- [x] Run all `bcs-http` tests, inspect the focused diff, run `git diff --check`,
  and verify every added/modified source file stays below 1,000 lines.
- [x] Record validation evidence in the spec and report the branch and results.
  Per the follow-up request, commit the fix, push with `--no-verify`, and open
  a PR targeting `dev`. Do not merge.
