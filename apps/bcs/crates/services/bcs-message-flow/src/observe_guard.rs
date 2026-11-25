//! Shared qualification for input/reply observe sync. Roster reads are batched
//! once per retry round; each individual send rechecks its target and term.
use std::sync::{Arc, atomic::Ordering};
use bcs_domain::{ActorKind, RedactedToken};
use bcs_service_api::port::{ObserveInjectCommand, ObserveInjectError, ObserveInjectPort};
use crate::caller_dispatch_pipeline::ObserveSyncAssembly;

impl ObserveSyncAssembly {
    pub(crate) fn current_term(&self) -> Option<u64> {
        self.delivery_term.as_ref().map(|term| term.load(Ordering::SeqCst))
    }

    /// Missing/error membership evidence pauses sync; it never authorizes it.
    /// The session roster, when populated, is the routing membership authority.
    pub(crate) async fn qualified_port(
        &self, port: Arc<dyn ObserveInjectPort>, group_id: &str,
        bot_id: &str, session_id: &str, at_term: Option<u64>,
    ) -> Result<Option<Arc<dyn ObserveInjectPort>>, ObserveInjectError> {
        if self.current_term() != at_term { return Ok(None); }
        let mut group = match self.groups.try_get(group_id).await {
            Ok(Some(group)) => group,
            Ok(None) => return Ok(None),
            Err(_) => return Err(ObserveInjectError::Unavailable { retryable: true }),
        };
        if let Some(sessions) = &self.sessions {
            match sessions.get(session_id).await {
                Ok(Some(session)) if session.group_id == group_id => {
                    if !session.participants.is_empty() { group.participants = session.participants; }
                }
                Ok(_) => return Ok(None),
                Err(_) => return Err(ObserveInjectError::Unavailable { retryable: true }),
            }
        }
        if self.current_term() != at_term { return Ok(None); }
        if !group.participants.iter().any(|p| p.actor_kind == ActorKind::Bot && p.bot_uuid == bot_id) {
            self.index.invalidate_bot_in_session(bot_id, session_id);
            return Ok(None);
        }
        let subjects = group.participants.iter()
            .filter(|p| p.actor_kind == ActorKind::Human)
            .filter_map(|p| p.bot_uuid.strip_prefix("human_"))
            .filter(|subject| !subject.is_empty()).map(str::to_owned).collect::<Vec<_>>();
        self.index.pause_departed_subjects(bot_id, session_id, &subjects);
        Ok(Some(Arc::new(QualifiedObservePort { assembly: self.clone(), port, at_term })))
    }
}

struct QualifiedObservePort {
    assembly: ObserveSyncAssembly,
    port: Arc<dyn ObserveInjectPort>,
    at_term: Option<u64>,
}

#[async_trait::async_trait]
impl ObserveInjectPort for QualifiedObservePort {
    async fn inject_shared_context(&self, credential: &RedactedToken, mut command: ObserveInjectCommand)
        -> Result<(), ObserveInjectError> {
        if self.assembly.current_term() != self.at_term { return Ok(()); }
        let now = chrono::Utc::now().timestamp_millis();
        let Some(current) = self.assembly.index.current(command.caller_subject.expose(),
            &command.target_bot_id, &command.session_id, now) else { return Ok(()); };
        // A cloned fan-out list cannot revive an invalidated or replaced target.
        if current.caller_ref.as_ref().map(|r| r.expose()) != command.runtime_target_ref.as_ref().map(|r| r.expose())
            || current.target_epoch.as_ref().map(|r| r.expose()) != command.runtime_target_epoch.as_ref().map(|r| r.expose()) {
            return Ok(());
        }
        if self.assembly.current_term() != self.at_term { return Ok(()); }
        command.now_ms = now;
        self.port.inject_shared_context(credential, command).await
    }
}
