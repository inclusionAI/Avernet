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

use bcs_dist_model::{Did, Instance, InputId, PoolId, ShardId, Sid, Tid};
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
    /// Per-session task index (sorted set: member = task id, score = creation
    /// time in ms), written only by Gateway admission: `…:tasks:<sid>`.
    pub fn tasks(&self, sid: &Sid) -> Result<String, StoreError> {
        self.by_session("tasks", sid)
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
    pub fn heartbeat(&self, copy: HeartbeatCopy, instance: &Instance) -> Result<String, StoreError> {
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
        let digest = crate::identity::digest_fields(&[scope.tenant.0.as_bytes(), scope.principal.0.as_bytes()]);
        Ok(format!("{}:tasks:{}", self.namespace_identity(), crate::identity::hex_of(&digest)))
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
    pub fn effect_completion(&self, effect: &bcs_dist_model::EffectId) -> Result<String, StoreError> {
        Ok(format!("{}:{}:{}", self.by_session("effect-completion", &effect.sid)?, effect.commit.0, effect.index))
    }
    pub fn output_trim(&self, sid: &Sid) -> Result<String, StoreError> {
        self.by_session("output-trim", sid)
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
    let head = id.split('.').next().unwrap_or_default();
    let digits = head
        .strip_prefix('s')
        .filter(|d| !d.is_empty() && d.bytes().all(|b| b.is_ascii_digit()))
        .ok_or_else(|| invalid("id_without_shard_prefix"))?;
    if !id.contains('.') {
        return Err(invalid("id_without_shard_prefix"));
    }
    digits
        .parse::<u32>()
        .map(ShardId)
        .map_err(|_| invalid("shard_out_of_range"))
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
