//! V1 session participant-avatar projection helpers (bot-avatar plan Task 7).
//!
//! Design §5.2/§5.3 (`docs/specs/2026-11-02-bot-avatar-collaboration-repair/design.md`):
//! Session list summaries carry a lightweight `participants` array whose `name`
//! is THIS Session's own member snapshot — session members, including
//! session-only dynamic members and a Group member that has left the Session
//! is never included — and whose `avatar_url` comes from ONE batched
//! display-profile read per result page. Detail projections and the shared
//! create/update/participant response models keep their existing name
//! backfill and gain the same avatar column from one batched read.
//!
//! The helpers in this module are pure projections over an already-fetched
//! avatar map plus the extraction of the snapshot rows; the service
//! orchestration owns the single registry call. Avatar read failures
//! propagate as application errors — never a page of nulls pretending
//! success — and the fetched batch is keyed by exactly the ids visible in the
//! rows being projected, so no id outside the Session's own roster is queried.

use super::*;

/// Extract the lightweight identity/snapshot rows of a Session's own current
/// members (design §5.2). The `name` is the Session-membership snapshot
/// verbatim (null allowed); it is never a Group member snapshot and the list
/// never backfills it from the registry. `avatar_url` stays null here — the
/// caller fills it from one batched read.
pub(crate) fn participant_summaries(session: &Session) -> Vec<ParticipantSummary> {
    session
        .participants
        .iter()
        .map(|participant| ParticipantSummary {
            actor_id: participant.bot_uuid.clone(),
            actor_kind: participant.actor_kind,
            name: participant.bot_name.clone(),
            avatar_url: None,
        })
        .collect()
}

/// The stored avatar of one actor, or null: absent map keys (Human actors,
/// unknown identities, foreign environments) resolve null by the batch
/// read's contract, and a present `None` is a record without an avatar.
pub(super) fn avatar_for(avatars: &BotAvatarMap, actor_id: &str) -> Option<String> {
    avatars.get(actor_id).cloned().flatten()
}

/// Fill one batched avatar map onto the extracted summary rows (pure
/// projection, design §5.3).
pub(crate) fn apply_summary_avatars(
    rows: &mut [ParticipantSummary],
    avatars: &BotAvatarMap,
) {
    for row in rows {
        row.avatar_url = avatar_for(avatars, &row.actor_id);
    }
}

/// The deduplicated Bot member ids of a result page's Sessions, in first-seen
/// order. Human members never join a Bot display read, and the ids come from
/// exactly the Session rosters that will be projected on the page.
pub(crate) fn session_page_bot_ids(sessions: &[Session]) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut ids = Vec::new();
    for session in sessions {
        for participant in &session.participants {
            if participant.actor_kind != ActorKind::Bot || participant.bot_uuid.is_empty() {
                continue;
            }
            if seen.insert(participant.bot_uuid.clone()) {
                ids.push(participant.bot_uuid.clone());
            }
        }
    }
    ids
}

/// The deduplicated Bot ids of one Session's member rows, in first-seen order.
pub(crate) fn detail_bot_ids(participants: &[Participant]) -> Vec<String> {
    let mut ids = Vec::new();
    for participant in participants {
        if participant.actor_kind != ActorKind::Bot || participant.bot_uuid.is_empty() {
            continue;
        }
        if !ids.contains(&participant.bot_uuid) {
            ids.push(participant.bot_uuid.clone());
        }
    }
    ids
}

/// The ids of one returned shared-model participant, bounded to a Bot member
/// (a Human resolves null by contract and reads nothing).
pub(crate) fn single_bot_id(participant: &Participant) -> Vec<String> {
    if participant.actor_kind == ActorKind::Bot && !participant.bot_uuid.is_empty() {
        vec![participant.bot_uuid.clone()]
    } else {
        Vec::new()
    }
}

impl SessionServiceImpl {
    /// ONE batched display-profile read (design §5.3) for a submitted set of
    /// Bot member ids — a whole list page, one detail Session, or one returned
    /// participant. An empty id set reads nothing; read failures map to
    /// service errors instead of mutating the response into nulls.
    pub(super) async fn avatar_map_for_ids(
        &self,
        bot_ids: Vec<String>,
    ) -> Result<BotAvatarMap, ApplicationError> {
        if bot_ids.is_empty() {
            return Ok(BotAvatarMap::new());
        }
        self.registry
            .avatar_urls(&bot_ids)
            .await
            .map_err(map_service_error)
    }
}
