//! V1 projection helpers for the session facade.
//!
//! Moved verbatim from `lib.rs` during the bot-avatar plan Task 1
//! file-size split. The helpers stay crate-internal (`pub(super)`),
//! re-imported at the crate root so sibling modules keep their
//! `use super::*` glob access.
//!
//! Bot-avatar plan Task 7: `project_summary` and `project_participant` are
//! pure projections that consume an ALREADY-fetched avatar map (design
//! §5.2/§5.3) — they never perform I/O of their own.

use super::*;

pub(super) fn project_participant(
    participant: &Participant,
    avatars: &BotAvatarMap,
) -> SessionParticipant {
    // Vey7i: pass `actor_kind` and the 4-value domain `ParticipantMode`
    // through verbatim so a Human participant inserted by the legacy
    // invitation-accept path (`actor_kind: Human, mode: Present`) is surfaced
    // as-is, not boot-truncated to `Auto`. Client-input Bot participants still
    // round-trip `Auto`/`Muted` because `build_participant` / `add_participant`
    // map the V1 input mode into the domain mode before persistence.
    SessionParticipant {
        actor_id: participant.bot_uuid.clone(),
        actor_kind: participant.actor_kind,
        name: participant.bot_name.clone(),
        avatar_url: avatar::avatar_for(avatars, &participant.bot_uuid),
        role: participant.role,
        tags: participant.tags.clone(),
        mode: participant.effective_mode(),
        message_view_scope: participant.message_view_scope,
        joined_at: None,
    }
}

pub(super) fn project_status(status: DomainSessionStatus) -> V1SessionStatus {
    match status {
        DomainSessionStatus::Running => V1SessionStatus::Running,
        DomainSessionStatus::Completed => V1SessionStatus::Completed,
    }
}

pub(super) fn map_status_to_domain(status: V1SessionStatus) -> DomainSessionStatus {
    match status {
        V1SessionStatus::Running => DomainSessionStatus::Running,
        V1SessionStatus::Completed => DomainSessionStatus::Completed,
    }
}

pub(super) fn project_summary(session: &Session, avatars: &BotAvatarMap) -> SessionSummary {
    // Design §5.2: the lightweight rows' names are THIS Session's own member
    // snapshots (null allowed, session-only dynamic members included) and are
    // never backfilled from the registry; the avatar column comes from the
    // already-fetched batched map.
    let mut participants = avatar::participant_summaries(session);
    avatar::apply_summary_avatars(&mut participants, avatars);
    SessionSummary {
        session_id: session.id.clone(),
        version: session.group_version.unwrap_or(1),
        group_id: session.group_id.clone(),
        status: project_status(session.status),
        title: session.session_title.clone(),
        participant_count: Some(session.participants.len()),
        caller_principal: session.caller_principal.clone(),
        created_by: session.created_by.clone(),
        created_at: session.created_at,
        updated_at: session.updated_at,
        collected: None,
        participants,
    }
}
