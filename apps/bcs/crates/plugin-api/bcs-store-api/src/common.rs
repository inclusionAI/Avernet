use std::time::Duration;

/// A positive storage version. Basic ConditionalKv versions advance within a live
/// key incarnation; deletion/recreation may reuse them. MonotonicKv, AtomicJson
/// and VersionedZSet additionally retain history across deletion and expiry.
/// Never interpret this token as wall time or a business commit sequence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Version(pub u64);

#[derive(Debug, Clone, PartialEq)]
pub struct Versioned<T> {
    pub value: T,
    pub version: Version,
}

/// Optional capabilities are explicit: unsupported operations fail before any
/// mutation. A declaration applies to this configured provider/namespace, not
/// every deployment of the same backend product.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StoreCapability {
    /// Persistent whole-document create/read/exact-version replacement. No TTL,
    /// deletion, retained history after external deletion, or Newer is implied.
    DocumentCas,
    /// Provider-timed, non-revivable registration lifetimes, including retirement.
    StrictLease,
    Kv,
    /// Versioned creation/read, unconditional deletion and TTL query; no CAS.
    VersionedKv,
    /// Strict exact-version update of a live value; requires VersionedKv.
    ConditionalKv,
    /// All versioned expiry modes and atomic version-checked TTL updates.
    VersionedKvTtl,
    /// Atomic version check + deletion; does not imply retained history.
    VersionedKvConditionalDelete,
    /// Retained history across versioned mutations, including deletion/expiry,
    /// plus caller-assigned newer versions. TTL/delete extensions are independent.
    MonotonicKv,
    AtomicJson,
    ZSet,
    VersionedZSet,
    Stream,
    StreamGroups,
    /// Atomic per-key read/trim evidence; not required for basic Stream or groups.
    StreamRecovery,
}

/// Metadata only; operation traits are independently injectable as trait objects.
/// Kv and VersionedKv are independent. ConditionalKv requires VersionedKv.
/// VersionedKvTtl,
/// VersionedKvConditionalDelete and MonotonicKv each require ConditionalKv.
/// VersionedZSet requires ZSet; StreamGroups and StreamRecovery independently
/// require Stream. Declarations are stable for the lifetime of this instance.
pub trait StorePlugin: Send + Sync + 'static {
    fn supports(&self, capability: StoreCapability) -> bool;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Expiry {
    /// Preserve an existing deadline; a newly created key is persistent.
    Preserve,
    Persistent,
    /// Positive duration. Round UP to backend precision, never expire early.
    After(Duration),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyTtl {
    Missing,
    Persistent,
    ExpiresIn(Duration),
}

/// Checked against the current live value AND retained version history.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WriteCondition {
    Any,
    /// Missing/expired value; retained version history is NOT reset.
    Absent,
    /// Requires a live value at this exact version.
    Version(Version),
    /// Supplied version must exceed the historical high-water mark, including
    /// tombstones. On success this becomes the new version; equality is rejected.
    Newer(Version),
}

/// Successful conditional writes advance version, including identical data.
/// Retention across key incarnations depends on the selected capability.
/// For Any/Absent/Version the provider chooses a strictly higher version; for
/// Newer it uses the supplied version. Exhaustion fails without changing data.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WriteOutcome {
    Applied {
        version: Version,
    },
    /// Definitely no change by THIS attempt; never proof that a prior one applied.
    ConditionNotMet,
}
