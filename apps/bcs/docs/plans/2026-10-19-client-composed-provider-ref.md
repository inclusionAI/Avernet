# Client-composed Provider references implementation plan

> Execute the approved design inline with focused red/green tests and a final diff review.

**Goal:** Keep slug as a bridge CLI option. Registration APIs accept the final
`provider_bot_ref`; BCN parses and validates the parent identity in both paths.

**Architecture:** For `agentpass-subbots`, split the final reference at the last
colon. Preserve that reference verbatim and store its validated parent separately
in `agent_code`. Other Provider modes retain their existing opaque-ref behavior.
No new database column or migration is needed. Existing derived response metadata
remains compatible. Registration-token authorization is unchanged; parsing is not
live AgentPass ownership verification.

**Tech stack:** Rust domain/core/application/HTTP crates, Python acceptance tests,
OpenAPI YAML/JSON, shared bridge application with OCB AgentPass resolvers.

## 1. Shared identity parser

Files: `crates/contracts/bcs-domain/src/provider_identity.rs` and
`crates/contracts/bcs-domain/tests/provider_subbots.rs`.

- First test final references, parents containing colons, missing components,
  invalid characters, exact length bounds and unchanged ordinary-mode behavior.
- Observe failures with the existing composing implementation.
- Replace composition with a two-argument normalizer that validates and parses.
- Run `cargo test -p bcs-domain --test provider_subbots`.

## 2. Registration entry points and contracts

Files: Provider-admin and scoped registration request DTOs, both application/core
paths, legacy/OpenAPI HTTP adapters, register OpenAPI fragment, published Gateway
schema, Provider console, and their existing tests.

- Remove only request-side `bot_slug` fields and all request forwarding.
- Pass the same complete ref into the shared normalizer in both registration paths.
- Preserve independent UUIDs/tokens, uniqueness and tombstone behavior.
- Exercise both paths with valid and invalid final references; ensure a separate
  slug field cannot rescue a missing suffix.
- Run domain, Provider registration, Provider management, registration facade,
  HTTP route and Provider console tests; compile affected HTTP/bootstrap consumers.
- Update `docs/agentpass-subbots.md` and module boundary notes.

## 3. Bridge client in a separate branch

- Keep A1 free of bridge/CLI implementation files.
- Create a new client branch using A1 as its base, carrying the existing public
  client feature changes from the original combined branch without changing it.
- Before POST, compose and validate the complete ref once from the parent and
  the explicit or persisted random slug. Send neither `bot_slug` nor
  `expected_auth_mode`; validate returned ref and parent against the request.
- Preserve pending-registration retry behavior, local Bot config, Actor injection
  and the existing multi-Bot execution implementation.
- Update public bridge tests/docs and OCB wrapper integration tests/docs on a
  separate OCB client branch; preserve the original OCB worktree and gitlink.

## 4. Verification and delivery

- Run the live SQLite Singlebox subbot story for both registration paths.
- Validate public schema; check only intended JSON changes are included.
- Record any pre-existing test failures against the unchanged base.
- Review exact changed paths, source size and `git diff --check`.
- Commit/push each branch with `--no-verify` per standing user instruction.
- Update A1 PR in English and publish the client follow-up with its A1 dependency.
