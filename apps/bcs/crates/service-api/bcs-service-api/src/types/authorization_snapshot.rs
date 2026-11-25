//! Authorization-fact resource snapshots (bot-authority plan §3,
//! spec §8.3 权威资源读取与保护信息传播).
//!
//! These are strict, narrow projections of the persisted Group /
//! Session authorization facts. They carry NO message bodies, NO
//! credentials and NO full configurations: only what a resource
//! authorization decision needs.
//!
//! Reads of these snapshots are strict `ServiceResult` reads against
//! the master data source; stores never fall back to an ordinary
//! display cache (spec §8.3). Field values reuse the existing domain
//! enums — no synonymous snapshot enums are introduced.

use bcs_domain::{
    ActorKind, GroupKind, GroupStatus, GroupStrategy, MessageViewScope,
    ParticipantMode, ParticipantRole, SessionKind, SessionStatus,
};

/// One participant row of a Group/Session authorization snapshot.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParticipantAuthorizationSnapshot {
    /// Actor id (Bot uuid or Human actor id).
    pub actor_id: String,
    pub actor_kind: ActorKind,
    pub role: ParticipantRole,
    /// `None` participant modes project to the existing per-actor-kind
    /// default (`ParticipantMode::default_for`).
    pub effective_mode: ParticipantMode,
    /// Missing legacy values decode with the existing persistence
    /// rules; the snapshot never silently defaults a missing field to
    /// `Full` (spec §8.3).
    pub message_view_scope: MessageViewScope,
}

/// Authorization facts of one Group row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupAuthorizationSnapshot {
    /// Bound environment of the reading store instance.
    pub env: String,
    pub id: String,
    /// The row exists and is not a pending provisioning record.
    /// Deleted Groups project to `Ok(None)` instead.
    pub live: bool,
    pub status: GroupStatus,
    pub record_status: String,
    pub visibility: String,
    pub group_kind: GroupKind,
    pub group_strategy: GroupStrategy,
    pub driver_actor_id: String,
    /// Existing default-driver rule: originator falls back to
    /// `driver_bot` when unset.
    pub originator_actor_id: String,
    pub participants: Vec<ParticipantAuthorizationSnapshot>,
}

impl GroupAuthorizationSnapshot {
    /// The Group's management-role candidates (spec §8.3):
    /// the driver and the effective originator are always
    /// candidates; an extra participant carrying the same-named
    /// `Manager` role counts ONLY under
    /// [`GroupStrategy::ManagerWorker`] — `Chat` / `StateMachine`
    /// never elevate it. Order is stable: driver, originator
    /// (when different), then Manager participants in participant
    /// order; ids are deduplicated.
    pub fn management_actor_ids(&self) -> Vec<&str> {
        fn push_unique<'id>(ids: &mut Vec<&'id str>, id: &'id str) {
            if !ids.iter().any(|existing| *existing == id) {
                ids.push(id);
            }
        }
        let mut ids: Vec<&str> = Vec::new();
        push_unique(&mut ids, &self.driver_actor_id);
        push_unique(&mut ids, &self.originator_actor_id);
        if self.group_strategy == GroupStrategy::ManagerWorker {
            for participant in &self.participants {
                if participant.role == ParticipantRole::Manager {
                    push_unique(&mut ids, &participant.actor_id);
                }
            }
        }
        ids
    }
}

/// Authorization facts of one Session row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionAuthorizationSnapshot {
    /// Bound environment of the reading store instance.
    pub env: String,
    pub id: String,
    /// The row exists in the live resource table
    /// (`bcs_group_sessions` has no tombstone concept, so any
    /// projected row is live; deleted Sessions project to
    /// `Ok(None)`).
    pub live: bool,
    pub group_id: String,
    pub status: SessionStatus,
    /// Chat (default) or ServiceInvocation.
    pub session_kind: SessionKind,
    /// Real creating actor, which may be a non-Human actor
    /// (spec §8.3); this is the Session's own fact, NOT the Bot
    /// legacy `created_by`.
    pub created_by: Option<String>,
    /// `0` is the legacy visibility; `1` guarantees producer
    /// Domain/Audience metadata (existing domain semantics).
    pub message_visibility_version: u8,
    /// The domain actor→join-sequence data, as persisted
    /// (`None` keeps its existing "absent" semantics).
    pub participant_join_seq: Option<serde_json::Value>,
    pub participants: Vec<ParticipantAuthorizationSnapshot>,
}
