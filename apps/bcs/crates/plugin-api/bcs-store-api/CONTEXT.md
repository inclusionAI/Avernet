# bcs-store-api Context

## Provides

- Body-free ordinary-pool observations; providers report current resource facts,
  while consumers own admission thresholds and recovery policy.

- Independent DocumentCas and StrictLease contracts; their presence does not
  widen AtomicJson or ordinary TTL guarantees.
- DocumentCas includes consistent path projection and ordered patch through
  portable defaults over one consistent read and an atomic exact-version replace.
- StorePlugin capability declaration and independent ordinary/versioned KV,
  atomic JSON, sorted-set and stream operation traits.
- Shared version, expiry, operation outcome and uncertainty contracts.
- The [README](README.md) owns semantics, provider requirements and test mapping.

## Consumes and dependencies

Only async-trait, serde_json, thiserror and the Rust standard library.
Forbidden: service/domain models, concrete providers, network clients, private
SDKs, delivery adapters, bootstrap and environment access. Repositories consume
these traits; composition roots select implementations.

## Lifecycle and isolation

Trusted in-process plugin contract. Configuration and initialization belong to
bootstrap/provider implementations; capability checks precede accepting work.
Drain/cancel before teardown; release blocked read connections. Provider access is
restricted to its configured storage namespace. No dynamic loader is introduced.

## Change impact

This pre-merge contract revision splits KvStorePlugin/VersionedKvStorePlugin,
separates VersionedKv base operations from strict ConditionalKv,
adds optional versioned TTL/conditional deletion, and changes conditional delete
to bool. Providers and harness callers must migrate together. Future
service-store implementations must coordinate changes to versions,
atomicity, stream cursor semantics, supported capabilities and error outcomes.
Object/file storage and best-effort caches retain their existing API boundaries.
DocumentCas providers can inherit path projection/patch, but must mount the
expanded conformance suite; native overrides preserve the same whole-document
version and failure semantics. First creation is version 1.

## Verification

Reusable harnesses: `bcs-test-support::contract::plugin::store`.
Run the commands in the README; real provider validation remains required before
claiming storage durability or backend conformance.
