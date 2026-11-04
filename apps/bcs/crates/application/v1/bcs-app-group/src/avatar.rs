//! V1 participant-avatar projection helpers (bot-avatar plan Task 6).
//!
//! Design §5.2/§5.3 (`docs/specs/2026-11-02-bot-avatar-collaboration-repair/design.md`):
//! list summaries carry a lightweight `participants` array whose `name` is the
//! Group-membership snapshot — extracted BEFORE the driver/peer name backfill
//! mutates the member copies — and whose `avatar_url` comes from ONE batched
//! display-profile read per result page. Detail projections keep the full
//! `Participant` control fields and gain the same avatar column from one
//! batched read for that Group.
//!
//! The helpers in this module are pure projections over an already-fetched
//! avatar map plus the extraction of the snapshot rows; the service
//! orchestration owns the single registry call. Avatar read failures propagate
//! as application errors — never a page of nulls pretending success — and the
//! fetched batch is keyed by exactly the ids visible in the projected rows, so
//! no id filtered out by the existing visibility rules is ever queried.

use super::*;

/// Extract the lightweight identity/snapshot rows of a Group's stored members.
///
/// MUST run before `backfill_bot_names` so `name` stays the membership
/// snapshot (possibly null) even though the driver/peer display fields are
/// backfilled from the registry afterwards (design §5.2).
pub(crate) fn participant_summaries(group: &DomainGroup) -> Vec<ParticipantSummary> {
    group
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
/// unknown identities, foreign environments) resolve null by the batch read's
/// contract, and a present `None` is a record without an avatar.
fn avatar_for(avatars: &BotAvatarMap, actor_id: &str) -> Option<String> {
    avatars.get(actor_id).cloned().flatten()
}

/// Fill one batched avatar map onto a whole list page (pure projection,
/// design §5.3): every summary row plus the DM `peer_actor` view.
pub(crate) fn apply_group_avatars(groups: &mut [GroupSummary], avatars: &BotAvatarMap) {
    for group in groups {
        match group {
            GroupSummary::Normal(summary) => {
                for row in &mut summary.participants {
                    row.avatar_url = avatar_for(avatars, &row.actor_id);
                }
            }
            GroupSummary::DirectMessage(summary) => {
                for row in &mut summary.participants {
                    row.avatar_url = avatar_for(avatars, &row.actor_id);
                }
                if let Some(peer) = summary.peer_actor.as_mut() {
                    peer.avatar_url = avatar_for(avatars, &peer.actor_id);
                }
            }
        }
    }
}

/// Fill one batched avatar map onto a detail's participants (pure projection).
pub(crate) fn apply_participant_avatars(
    participants: &mut [V1Participant],
    avatars: &BotAvatarMap,
) {
    for participant in participants {
        participant.avatar_url = avatar_for(avatars, &participant.actor_id);
    }
}

/// The deduplicated Bot participant ids of a projected summary page, in
/// first-seen order. Human participants never join a Bot display read, and the
/// ids come from the visible projected rows only, so the batch is bounded by
/// what the caller is allowed to see.
pub(crate) fn summary_page_bot_ids(groups: &[GroupSummary]) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut ids = Vec::new();
    for group in groups {
        let rows = match group {
            GroupSummary::Normal(summary) => &summary.participants,
            GroupSummary::DirectMessage(summary) => &summary.participants,
        };
        for row in rows {
            if row.actor_kind != ActorKind::Bot || row.actor_id.is_empty() {
                continue;
            }
            if seen.insert(row.actor_id.clone()) {
                ids.push(row.actor_id.clone());
            }
        }
    }
    ids
}

/// The deduplicated Bot participant ids of a detail's projected participant
/// rows, in first-seen order.
pub(crate) fn detail_bot_ids(participants: &[V1Participant]) -> Vec<String> {
    let mut ids = Vec::new();
    for participant in participants {
        if participant.actor_kind != ActorKind::Bot || participant.actor_id.is_empty() {
            continue;
        }
        if !ids.contains(&participant.actor_id) {
            ids.push(participant.actor_id.clone());
        }
    }
    ids
}

impl GroupServiceImpl {
    /// ONE batched display-profile read (design §5.3) for a submitted set of
    /// Bot participant ids — a whole list page, one detail Group, or one
    /// returned participant. An empty id set reads nothing; read failures map
    /// to service errors instead of mutating the response into nulls.
    pub(crate) async fn avatar_map_for_ids(
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

    /// Fill a returned shared-model participant with its avatar through a
    /// single display-profile read for that actor (create/update/participant
    /// responses reuse the shared model, design §5.1). Human participants
    /// resolve null by contract and read nothing.
    pub(crate) async fn fill_participant_avatar(
        &self,
        participant: &mut V1Participant,
    ) -> Result<(), ApplicationError> {
        if participant.actor_kind != ActorKind::Bot {
            return Ok(());
        }
        let avatars = self
            .avatar_map_for_ids(vec![participant.actor_id.clone()])
            .await?;
        participant.avatar_url = avatar_for(&avatars, &participant.actor_id);
        Ok(())
    }
}
