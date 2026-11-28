# Direct A2A implementation amendment (2026-11-27)

This section records the Direct slice in [PR #4509](https://github.com/inclusionAI/Avernet/pull/4509).
It supplements the [original proposal](https://github.com/inclusionAI/Avernet/blob/642b631686abd2ec277d42b1be181cab43ddc6e0/apps/bcs/specs/2026-11-26-distributed-runner-spi/spec.md)
and supersedes the corresponding proposal details for that slice only; it does
not claim that the full multi-mode proposal, production integration or deployment
is complete. Current contracts and owning crate contexts remain authoritative.

- **Section 6.1 authorization is deferred**, tracked in [#4544](https://github.com/inclusionAI/Avernet/issues/4544). Gateway calls the required
  `DistSessionDirectoryPort::authorize` on each send and human reply before
  durable admission. The Direct directory delegates unchanged caller scope and
  target to a required host-supplied `DistSessionAuthorizerPort`. This slice
  defines no Bot, Human, visibility, friendship, organization or delegated
  identity policy, and provides no default allow. Production exposure requires
  the host's real authorizer and content interception/audit before Inbox writes,
  including replies. Supplying a permissive test stub does not meet that gate.
- **Directory ownership:** target validation and immutable configuration belong
  to the feature-gated bootstrap [Direct directory](../../crates/bootstrap/bcs/src/dist_direct_a2a/directory.rs),
  not the single-process message-flow crate. Policy and configuration remain
  independently injectable behind their existing contracts.
- **Unpublished v1:** SessionSpec, DispatchSpec and Checkpoint remain version 1
  in `bcn:v1`. The revised display shape requires a fresh distributed namespace;
  this is not a v2 rollout or an implicit migration of prototype records.
- **Configuration:** the unused `load_mode_config` method is removed. Worker
  resolves immutable Blob references and validates config once per held slot.
  `validate_session` is required and returns the prepared erased config through
  the existing typed runner adapter. No second public ErasedRunner is added.
- **Single-mode deployment:** every Worker sharing a namespace installs the
  same mode/version. StateMachine and multi-mode routing are separate work.
- **Output:** `DisplayContentFormat`, `UpsertParts` and `AppendParts` preserve
  file/data/text parts through projection, Blob, follow and archive. Only one
  plain text part uses the string path; multi-part boundaries are retained.
  Final status messages fill an absent answer, while explicit Final artifacts
  remain authoritative. A2A schema validation stays in the output adapter;
  shared projection treats part objects as opaque values.
- **Interaction recovery:** a whole-task Snapshot explicitly reporting Working
  closes a pending authorization/input interaction and publishes Working with
  no interactions. A status-less or unspecified Snapshot does not prove that
  the question has been answered.

Validation and remaining deployment boundaries are recorded in the PR. Runtime
contracts and reusable context live in the [bootstrap composition](../../crates/bootstrap/bcs/CONTEXT.md#distributed-direct-a2a),
[Direct runner](../../crates/services/bcs-direct-a2a-runner/CONTEXT.md), and
[output adapter](../../crates/adapters/output/bcs-dist-a2a-output/README.md).
