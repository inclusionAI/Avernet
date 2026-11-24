//! Key names of the distributed executor plane.
//!
//! Shard keys: `bcn:v1:<cluster>:<namespace>:s<shard>:<kind>[:<id>]`, e.g.
//! `bcn:v1:c01:main:s0042:inbox:s0042.7f3a9c`. Cross-shard keys:
//! `bcn:v1:cmd:<cluster>:<namespace>:<pool>:p<n>`,
//! `bcn:v1:held:<cluster>:<namespace>:<node>:<incarnation>`,
//! `bcn:v1:hb:<cluster>:<namespace>:<a|b>:<node>:<incarnation>`,
//! `bcn:v1:<cluster>:<namespace>:member(s):…`.
//!
//! There is no hash tag (`{…}`) anywhere: a brace in a name would create one, so
//! every segment rejects `{` and `}`.

use bcs_dist_model::{Did, InputId, Instance, PoolId, ShardId, ShardParseError, Sid, Tid};
use bcs_service_api::port::repo::distributed::common::StoreError;

const PREFIX: &str = "bcn:v1";
const MAX_SEGMENT_BYTES: usize = 256;

/// Copy of a heartbeat; the two copies are judged together (decision 15).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HeartbeatCopy {
    A,
    B,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KeyLayout {
    cluster: String,
    namespace: String,
}

impl KeyLayout {
    /// `cluster` and `namespace` come from configuration, e.g. `c01` and `main`.
    pub fn new(cluster: &str, namespace: &str) -> Result<Self, StoreError> {
        segment(cluster)?;
        segment(namespace)?;
        if cluster.contains(':') || namespace.contains(':') {
            return Err(invalid("layout_segment_contains_colon"));
        }
        Ok(Self {
            cluster: cluster.to_owned(),
            namespace: namespace.to_owned(),
        })
    }

    /// The shard a session belongs to: the `s<digits>` before the first `.`.
    pub fn shard_of(sid: &Sid) -> Result<ShardId, StoreError> {
        shard_of_prefixed(&sid.0)
    }

    /// Text form of a shard, at least four digits: `s0042`.
    pub fn shard_text(shard: ShardId) -> String {
        format!("s{:04}", shard.0)
    }

    pub fn owner(&self, shard: ShardId) -> String {
        format!("{}:owner", self.shard_prefix(shard))
    }
    pub fn active(&self, shard: ShardId) -> String {
        format!("{}:active", self.shard_prefix(shard))
    }
    pub fn wake(&self, shard: ShardId) -> String {
        format!("{}:wake", self.shard_prefix(shard))
    }
    pub fn sched(&self, shard: ShardId) -> String {
        format!("{}:sched", self.shard_prefix(shard))
    }

    pub fn spec(&self, sid: &Sid) -> Result<String, StoreError> {
        self.by_session("spec", sid)
    }
    pub fn ckpt(&self, sid: &Sid) -> Result<String, StoreError> {
        self.by_session("ckpt", sid)
    }
    pub fn control_attempt(
        &self,
        sid: &Sid,
        id: &bcs_dist_model::RequestId,
    ) -> Result<String, StoreError> {
        segment(&id.0)?;
        let digest = crate::identity::digest_fields(&[sid.0.as_bytes(), id.0.as_bytes()]);
        Ok(format!(
            "{}:control-attempt:{}",
            self.shard_prefix(Self::shard_of(sid)?),
            crate::identity::hex_of(&digest)
        ))
    }
    pub fn control_isolation(
        &self,
        sid: &Sid,
        id: &bcs_dist_model::RequestId,
    ) -> Result<String, StoreError> {
        Ok(format!("{}:isolation", self.control_attempt(sid, id)?))
    }
    pub fn control_recovery(
        &self,
        sid: &Sid,
        id: &bcs_dist_model::RequestId,
    ) -> Result<String, StoreError> {
        Ok(format!("{}:recovery", self.control_attempt(sid, id)?))
    }
    pub fn closure_progress(
        &self,
        sid: &Sid,
        id: &bcs_dist_model::RequestId,
    ) -> Result<String, StoreError> {
        segment(&id.0)?;
        let digest = crate::identity::digest_fields(&[sid.0.as_bytes(), id.0.as_bytes()]);
        Ok(format!(
            "{}:closure:{}",
            self.shard_prefix(Self::shard_of(sid)?),
            crate::identity::hex_of(&digest)
        ))
    }
    pub fn closure_certificate(
        &self,
        sid: &Sid,
        id: &bcs_dist_model::RequestId,
    ) -> Result<String, StoreError> {
        Ok(format!("{}:certificate", self.closure_progress(sid, id)?))
    }
    pub fn inbox(&self, sid: &Sid) -> Result<String, StoreError> {
        self.by_session("inbox", sid)
    }
    pub fn ev(&self, sid: &Sid) -> Result<String, StoreError> {
        self.by_session("ev", sid)
    }
    /// A task id starts with its session id, so it also names the shard.
    pub fn task(&self, tid: &Tid) -> Result<String, StoreError> {
        self.by_prefixed_id("task", &tid.0)
    }
    pub fn task_terminal_append(&self, tid: &Tid) -> Result<String, StoreError> {
        self.by_prefixed_id("task-terminal-append", &tid.0)
    }
    pub fn task_terminal_publish(&self, tid: &Tid) -> Result<String, StoreError> {
        self.by_prefixed_id("task-terminal-publish", &tid.0)

    }
    /// A dispatch id starts with its task id, so it also names the shard.
    pub fn disp(&self, did: &Did) -> Result<String, StoreError> {
        self.by_prefixed_id("disp", &did.0)
    }
    /// `admit` lives in the shard of the session the input was admitted to.
    pub fn admit(&self, sid: &Sid, input_id: &InputId) -> Result<String, StoreError> {
        let shard = Self::shard_of(sid)?;
        segment(&input_id.0)?;
        Ok(format!("{}:admit:{}", self.shard_prefix(shard), input_id.0))
    }

    /// Command-stream partition: `bcn:v1:cmd:c01:main:a2a:p0`.
    pub fn command(&self, pool: &PoolId, partition: u32) -> Result<String, StoreError> {
        segment(&pool.0)?;
        Ok(format!(
            "{PREFIX}:cmd:{}:{}:{}:p{partition}",
            self.cluster, self.namespace, pool.0
        ))
    }
    /// A Connector's held index: `bcn:v1:held:c01:main:conn-03:1794370950`.
    pub fn held(&self, instance: &Instance) -> Result<String, StoreError> {
        let (node, inc) = instance_segments(instance)?;
        Ok(format!(
            "{PREFIX}:held:{}:{}:{node}:{inc}",
            self.cluster, self.namespace
        ))
    }
    /// `bcn:v1:hb:c01:main:a:worker-07:1794370900`.
    pub fn heartbeat(
        &self,
        copy: HeartbeatCopy,
        instance: &Instance,
    ) -> Result<String, StoreError> {
        let (node, inc) = instance_segments(instance)?;
        let copy = match copy {
            HeartbeatCopy::A => "a",
            HeartbeatCopy::B => "b",
        };
        Ok(format!(
            "{PREFIX}:hb:{}:{}:{copy}:{node}:{inc}",
            self.cluster, self.namespace
        ))
    }
    /// `bcn:v1:c01:main:member:worker:worker-07:1794370900`.
    pub fn member(&self, role: &str, instance: &Instance) -> Result<String, StoreError> {
        segment(role)?;
        let (node, inc) = instance_segments(instance)?;
        Ok(format!(
            "{PREFIX}:{}:{}:member:{role}:{node}:{inc}",
            self.cluster, self.namespace
        ))
    }
    /// `bcn:v1:c01:main:members:worker`.
    pub fn members(&self, role: &str) -> Result<String, StoreError> {
        segment(role)?;
        Ok(format!(
            "{PREFIX}:{}:{}:members:{role}",
            self.cluster, self.namespace
        ))
    }

    /// Stable unambiguous namespace identity for public durable providers.
    pub fn namespace_identity(&self) -> String {
        format!("{PREFIX}:{}:{}", self.cluster, self.namespace)
    }

    /// Stable task listing, ordered lexicographically by immutable tid.
    pub fn tasks(&self, scope: &bcs_dist_model::TaskScope) -> Result<String, StoreError> {
        segment(&scope.tenant.0)?;
        segment(&scope.principal.0)?;
        let digest = crate::identity::digest_fields(&[
            scope.tenant.0.as_bytes(),
            scope.principal.0.as_bytes(),
        ]);
        Ok(format!(
            "{}:tasks:{}",
            self.namespace_identity(),
            crate::identity::hex_of(&digest)
        ))
    }
    pub fn session_tasks(&self, sid: &Sid) -> Result<String, StoreError> {
        self.by_session("tasks", sid)
    }

    pub fn task_display_ev(&self, tid: &Tid) -> Result<String, StoreError> {
        self.by_prefixed_id("task-display-ev", &tid.0)
    }
    pub fn output_head(&self, sid: &Sid) -> Result<String, StoreError> {
        self.by_session("output-head", sid)
    }
    pub fn task_retention(&self, tid: &Tid) -> Result<String, StoreError> {
        self.by_prefixed_id("task-retention", &tid.0)
    }
    pub fn effect_completion(
        &self,
        effect: &bcs_dist_model::EffectId,
    ) -> Result<String, StoreError> {
        Ok(format!(
            "{}:{}:{}",
            self.by_session("effect-completion", &effect.sid)?,
            effect.commit.0,
            effect.index
        ))
    }
    pub fn output_trim(&self, sid: &Sid) -> Result<String, StoreError> {
        self.by_session("output-trim", sid)
    }

    pub fn schedule(&self, sid: &Sid) -> Result<String, StoreError> {
        self.by_session("schedule", sid)
    }
    pub fn dispatch_journal(&self, shard: ShardId) -> String {
        format!("{}:dispatch-journal", self.shard_prefix(shard))
    }
    pub fn dispatch_scan(&self, shard: ShardId) -> String {
        format!("{}:dispatch-scan", self.shard_prefix(shard))
    }
    /// One durable admission document keyed by the caller scope and InputId.
    /// The key is independent of the target session so retries with the same
    /// scoped InputId always find the same repair record.
    pub fn admission(
        &self,
        scope: &bcs_dist_model::TaskScope,
        id: &InputId,
    ) -> Result<String, StoreError> {
        segment(&scope.tenant.0)?;
        segment(&scope.principal.0)?;
        segment(&id.0)?;
        let digest = crate::identity::digest_fields(&[
            scope.tenant.0.as_bytes(),
            scope.principal.0.as_bytes(),
            id.0.as_bytes(),
        ]);
        Ok(format!("{}:admission:{}", self.namespace_identity(), crate::identity::hex_of(&digest)))
    }

    /// Retained for source compatibility with old tests and migration tools.
    /// Production admission no longer creates or reads this shard-wide key.
    #[deprecated(note = "the MVP admission layout has no journal root")]
    pub fn admission_root(&self, shard: ShardId) -> String {
        format!("{}:admission-root", self.shard_prefix(shard))
    }
    /// Retained for source compatibility with old migration tooling only.
    #[deprecated(note = "the MVP admission layout has no intent records")]
    pub fn admission_intent(&self, shard: ShardId, sequence: u64) -> String {
        format!("{}:admission-intent:{sequence}", self.shard_prefix(shard))
    }
    pub fn admission_scan(&self, shard: ShardId, kind: &str) -> Result<String, StoreError> {
        segment(kind)?;
        Ok(format!(
            "{}:admission-scan:{kind}",
            self.shard_prefix(shard)
        ))
    }
    /// Legacy spelling retained for source compatibility; it resolves to the
    /// single admission document key and does not create a canonical record.
    #[deprecated(note = "use admission(scope, input_id)")]
    pub fn canonical_admission(
        &self,
        scope: &bcs_dist_model::TaskScope,
        id: &InputId,
    ) -> Result<String, StoreError> {
        self.admission(scope, id)
    }

    fn shard_prefix(&self, shard: ShardId) -> String {
        format!(
            "{PREFIX}:{}:{}:{}",
            self.cluster,
            self.namespace,
            Self::shard_text(shard)
        )
    }
    fn by_session(&self, kind: &str, sid: &Sid) -> Result<String, StoreError> {
        self.by_prefixed_id(kind, &sid.0)
    }
    fn by_prefixed_id(&self, kind: &str, id: &str) -> Result<String, StoreError> {
        segment(id)?;
        let shard = shard_of_prefixed(id)?;
        Ok(format!("{}:{kind}:{id}", self.shard_prefix(shard)))
    }
}

/// Parses the shard from `s<digits>` before the first `.` of a sid, tid or did.
fn shard_of_prefixed(id: &str) -> Result<ShardId, StoreError> {
    bcs_dist_model::shard_of(id).map_err(|error| match error {
        ShardParseError::MissingShardPrefix => invalid("id_without_shard_prefix"),
        ShardParseError::ShardOutOfRange => invalid("shard_out_of_range"),
    })
}

fn instance_segments(instance: &Instance) -> Result<(&str, &str), StoreError> {
    segment(&instance.node)?;
    segment(&instance.incarnation)?;
    if instance.node.contains(':') || instance.incarnation.contains(':') {
        return Err(invalid("instance_contains_colon"));
    }
    Ok((&instance.node, &instance.incarnation))
}

/// A key segment must be non-empty, bounded, and free of braces (they would
/// form a hash tag), whitespace and control characters.
fn segment(value: &str) -> Result<(), StoreError> {
    if value.is_empty() || value.len() > MAX_SEGMENT_BYTES {
        return Err(invalid("key_segment_length"));
    }
    if value
        .chars()
        .any(|c| c == '{' || c == '}' || c.is_whitespace() || c.is_control())
    {
        return Err(invalid("key_segment_character"));
    }
    Ok(())
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid {
        code: code.to_owned(),
    }
}
