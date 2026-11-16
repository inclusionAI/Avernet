use std::time::Duration;

/// A logical storage version, not wall time or a business commit sequence.
/// Zero is reserved. Versions never wrap or regress, including after deletion,
/// expiration, migration and acknowledged-write failover. Providers retain the
/// high-water mark/tombstone needed to reject stale writes (ABA protection).
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
    Kv,
    ConditionalKv,
    AtomicJson,
    ZSet,
    VersionedZSet,
    Stream,
    StreamGroups,
}

/// Metadata only; operation traits are independently injectable as trait objects.
/// ConditionalKv requires Kv; VersionedZSet requires ZSet; StreamGroups requires
/// Stream. Declarations are stable for the lifetime of this plugin instance.
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

/// All successful writes advance version, including writes of identical data.
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
