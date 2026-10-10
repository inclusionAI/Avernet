//! V1 Session facade: read/query use cases (plan Task 11).
//!
//! Lists and detail reads select their view through the same live authority
//! facts as the mutations (managed-bot parity, spec §8.1): the View Actor and
//! the detail-access checks resolve via `BotAuthorityHook`, and read queries
//! carry no audit parameters (the audit contract is required WRITES only).

use std::collections::HashSet;

use async_trait::async_trait;
use bcs_domain::state_machine_history::merge_state_machine_history;
use bcs_service_api::application::v1::session::{
    GetSession, ListSessions, SessionDetail, SessionSummary,
};
use bcs_service_api::application::v1::Page;
use bcs_service_api::application::v1::ApplicationError;
use crate::{map_service_error, map_session_error, SessionServiceImpl};

impl SessionServiceImpl {
    /// Read use case: ListSessions (the trait impl lives with the other
    /// `SessionService` methods in `mutations.rs`; a trait impl must stay in
    /// one impl block).
    pub(crate) async fn list_sessions(
        &self,
        command: ListSessions,
    ) -> Result<Page<SessionSummary>, ApplicationError> {
        let view_actor_id = self
            .resolve_view_actor(&command.caller, command.view_bot_id.as_deref())
            .await?;
        if command.limit == 0 || command.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        self.load_group(&command.group_id).await?;
        let participant_id = Some(view_actor_id.as_str());
        let status = command.status.map(crate::map_status_to_domain);
        let mut sessions = self
            .sessions
            .list_by_group(
                &command.group_id,
                status,
                command.offset,
                command.limit,
                None,
                participant_id,
            )
            .await
            .map_err(map_session_error)?;
        // Repo ORDER BY already guarantees created_at DESC, session_id ASC
        // (VSN7M); keep this sort as a no-op safety net.
        sessions.sort_by(|a, b| {
            b.created_at
                .cmp(&a.created_at)
                .then_with(|| a.id.cmp(&b.id))
        });
        let total = self
            .session_repo
            .count_by_group(&command.group_id, status, None, participant_id)
            .await
            .map_err(map_service_error)?;
        // Surface the collected state only when the request explicitly names
        // that actor (mirrors the legacy `list_sessions_for_group` gating).
        let collected_set: HashSet<String> = if command.view_bot_id.is_some() {
            let ids: Vec<&str> = sessions.iter().map(|s| s.id.as_str()).collect();
            self.sessions
                .collected_at_map(&ids, &view_actor_id)
                .await
                .map_err(map_session_error)?
                .into_iter()
                .map(|(sid, _collected_at)| sid)
                .collect()
        } else {
            HashSet::new()
        };
        let items = sessions
            .iter()
            .map(|s| {
                let mut summary = crate::project_summary(s);
                if command.view_bot_id.is_some() {
                    summary.collected = Some(collected_set.contains(&s.id));
                }
                summary
            })
            .collect::<Vec<_>>();
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }

}

impl SessionServiceImpl {
    /// Read use case: GetSession (see `list_sessions` for the impl-split
    /// note).
    pub(crate) async fn get_session(
        &self,
        query: GetSession,
    ) -> Result<SessionDetail, ApplicationError> {
        // The detail read admits a Human caller acting through an owned or
        // managed participating Bot (spec §8.2 详情读取同 owner); the
        // synchronous claim selection formerly in the DTO lives here now
        // behind the async authority resolution.
        let (principal, session) = self
            .load_session_for_authorized_detail(&query.caller, &query.session_id)
            .await?;
        let _ = principal;
        self.project_detail(&session, None).await
    }
}

#[async_trait]
impl bcs_service_api::application::v1::message::SessionMessageService for SessionServiceImpl {
    async fn list(
        &self,
        query: bcs_service_api::application::v1::message::ListSessionMessages,
    ) -> Result<Vec<bcs_service_api::GroupMessage>, ApplicationError> {
        self.list_with_options(query, bcs_service_api::MessageHistoryOptions::default())
            .await
    }

    async fn list_with_options(
        &self,
        query: bcs_service_api::application::v1::message::ListSessionMessages,
        options: bcs_service_api::MessageHistoryOptions,
    ) -> Result<Vec<bcs_service_api::GroupMessage>, ApplicationError> {
        if query.limit == 0 || query.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        let view_actor_id = self
            .resolve_view_actor(&query.caller, query.view_bot_id.as_deref())
            .await?;
        let session = self.load_session(&query.session_id).await?;
        if !session
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == view_actor_id)
        {
            return Err(ApplicationError::forbidden(
                "The selected View Actor is not a Session Participant",
            ));
        }
        let group = self.load_group(&session.group_id).await?;
        let view_participant = session
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == view_actor_id)
            .expect("membership checked above");
        if group.group_strategy == bcs_service_api::GroupStrategy::StateMachine {
            let history = if view_participant.actor_kind == bcs_service_api::ActorKind::Human {
                self.collaboration_runtime
                    .get_state_machine_session_history_for_view(
                        &query.session_id,
                        query.limit,
                        query.before,
                        bcs_service_api::types::HumanMessageView {
                            actor_id: view_actor_id,
                            scope: view_participant.message_view_scope,
                            allow_legacy_unclassified_chat: false,
                        },
                    )
                    .await
            } else {
                self.collaboration_runtime
                    .get_state_machine_session_history(&query.session_id, query.limit, query.before)
                    .await
            };
            return history
                .map(|result| result.map_or_else(Vec::new, |result| result.messages))
                .map_err(crate::map_runtime_error);
        }

        let user = bcs_service_api::application::v1::authorization::require_authenticated_user(&query.caller)?;
        let session_id = query.session_id.clone();
        let human_view = (view_participant.actor_kind == bcs_service_api::ActorKind::Human
            && view_participant.message_view_scope
                == bcs_service_api::types::MessageViewScope::Participant)
            .then(|| bcs_service_api::types::HumanMessageView {
                actor_id: view_actor_id.clone(),
                scope: view_participant.message_view_scope,
                allow_legacy_unclassified_chat: false,
            });
        let result = self
            .history
            .get_session_history_with_options(
                bcs_service_api::SessionHistoryCommand {
                    caller: bcs_service_api::CallerContext::Human(
                        bcs_service_api::HumanActor {
                            actor_id: format!("human_{}", user.id),
                            staff_no: user.id.clone(),
                        },
                    ),
                    group_id: session.group_id.clone(),
                    session_id: query.session_id,
                    session_participants: session.participants,
                    view_bot_id: Some(view_actor_id),
                    limit: query.limit,
                    before: query.before,
                },
                options,
            )
            .await
            .map_err(crate::map_group_use_case_error)?;
        let Some(human_view) = human_view else {
            return Ok(result.messages);
        };

        // One-shot StateMachine runs can live inside Chat or ManagerWorker
        // Sessions; merge the participant-safe projection for backwards
        // compatibility while persisted history stays authoritative for
        // stable IDs.
        let snapshot = self
            .collaboration_runtime
            .get_state_machine_session_history_for_view(
                &session_id,
                query.limit,
                query.before,
                human_view,
            )
            .await
            .map_err(crate::map_runtime_error)?;
        Ok(merge_state_machine_history(
            result.messages,
            snapshot.map(|history| history.messages).unwrap_or_default(),
            query.limit,
        ))
    }
}
