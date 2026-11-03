//! Extracted verbatim from `server.rs`: WS dispatch bridges and lifecycle instrumentation hooks.
use super::*;

struct AgentCredentialBackfill {
    registry: Arc<dyn BotRegistryCoreService>,
}

#[async_trait::async_trait]
impl bcs_ws::bot::AgentCredentialBackfillPort for AgentCredentialBackfill {
    async fn backfill(
        &self,
        bot_uuid: &str,
        agent_token: Option<String>,
        agent_code_header: Option<String>,
    ) {
        let agent_token_str = match &agent_token {
            Some(t) if !t.is_empty() => t.clone(),
            _ => return,
        };

        // agent_token: always write to memory only (not DB) for security
        self.registry
            .add_bot_info(bot_uuid, "agent_token", agent_token_str.clone())
            .await;

        let agent_code = agent_code_header.filter(|s| !s.is_empty());

        let Some(agent_code) = agent_code else {
            warn!(
                bot_uuid = %bot_uuid,
                "no agent_code resolved, skipping backfill"
            );
            return;
        };

        // agent_code: persist to DB
        if let Some(mut caps) = self.registry.load_from_storage(bot_uuid).await {
            if caps.agent_code.as_deref() == Some(&agent_code) {
                debug!(
                    bot_uuid = %bot_uuid,
                    "agent_code unchanged, skipping write"
                );
                return;
            }
            caps.agent_code = Some(agent_code.clone());
            if let Err(e) = self.registry.save_to_storage(bot_uuid, &caps).await {
                warn!(
                    bot_uuid = %bot_uuid,
                    error = %e,
                    "failed to backfill agent_code"
                );
            } else {
                let _ = self.registry.register(bot_uuid.to_string(), caps).await;
                info!(
                    bot_uuid = %bot_uuid,
                    agent_code = %agent_code,
                    "agent_code backfilled"
                );
            }
        } else {
            warn!(
                bot_uuid = %bot_uuid,
                "bot not yet onboarded, skipping agent credential backfill"
            );
        }
    }
}

/// `GroupDispatchContextPort` backed by the core `GroupCoreService`. Lives in
/// the composition root, so it may depend on the core trait the WS adapter is
/// not allowed to name.
struct CoreGroupDispatchContext {
    group: Arc<dyn GroupCoreService>,
}

#[async_trait::async_trait]
impl bcs_service_api::GroupDispatchContextPort for CoreGroupDispatchContext {
    async fn participants(&self, group_id: &str) -> Option<Vec<bcs_service_api::Participant>> {
        self.group
            .get(group_id)
            .await
            .map(|group| group.participants)
    }
}

pub(super) fn bot_ws_dispatch_state(state: &Arc<BcsServerState>) -> Arc<bcs_ws::bot::BotDispatchState> {
    Arc::new(bcs_ws::bot::BotDispatchState {
        expected_actor_id: None,
        bot_runtime: state.services.bot_runtime.clone(),
        message_flow: state.services.message_flow.clone(),
        collaboration_runtime: state.services.collaboration_runtime.clone(),
        bot_run_context: state.services.bot_run_context.clone(),
        bot_connections: state.bot_connections.clone(),
        run_channels: state.run_channels.clone(),
        task_callback: None,
        session_management: state.services.session_management.clone(),
        group_dispatch: Arc::new(CoreGroupDispatchContext {
            group: state.services.group.clone(),
        }),
        system_message: Some(state.services.system_message.clone()),
        coordination_processed: state.coordination_processed.clone(),
        agent_credential_backfill: Some(Arc::new(AgentCredentialBackfill {
            registry: state.services.registry.clone(),
        })),
        interactions: state.services.interactions.clone(),
    })
}

pub(super) fn web_ws_dispatch_state(
    state: &Arc<BcsServerState>,
    group_session_connections: Option<Arc<dyn GroupSessionConnectionService>>,
) -> Arc<bcs_ws::web::WebDispatchState> {
    Arc::new(bcs_ws::web::WebDispatchState {
        message_flow: state.services.message_flow.clone(),
        collaboration_runtime: state.services.collaboration_runtime.clone(),
        workbench_sessions: state.services.workbench_sessions.clone(),
        interactions: state.services.interactions.clone(),
        group_session_connections,
        frontend_connections: state.frontend_connections.clone(),
        run_channels: state.frontend_run_channels.clone(),
        delivery_admission: state.delivery_admission.clone(),
        // Speaking-lease store (spec §6.3, P3.3): the SAME store instance
        // server_infrastructure mounts feeds the flow's admission fence AND
        // this WS lease command entrance — one store, one epoch. A None here
        // (store-less compositions) keeps lease commands answering an
        // explicit error, never a fake lease.
        speaking_leases: state.speaking_leases.clone(),
        // §15.99 server-wide idle window ([speaking_lease]
        // idle_timeout_ms): granted at every acquire/preempt and required
        // again at message submission (the message-flow fence reads the
        // same config value). A store-less composition keeps the value
        // inert — lease commands answer an explicit error anyway.
        lease_idle_timeout_ms: state.config.speaking_lease.idle_timeout_ms,
    })
}

struct NoopWsLifecycleInstrumentationHook;

#[async_trait::async_trait]
impl WsLifecycleInstrumentationHook for NoopWsLifecycleInstrumentationHook {
    async fn accepted(&self, _peer: WsPeer, _endpoint: &'static str) {}

    async fn registered(&self, _peer: WsPeer, _endpoint: &'static str) {}

    async fn error(&self, _peer: WsPeer, _endpoint: &'static str, _kind: WsErrorKind) {}

    async fn closed(
        &self,
        _peer: WsPeer,
        _endpoint: &'static str,
        _close_reason: WsCloseReason,
        _duration: std::time::Duration,
    ) {
    }
}

struct NoopDirectChatRunLifecycleHook;

#[async_trait::async_trait]
impl DirectChatRunLifecycleHook for NoopDirectChatRunLifecycleHook {
    async fn event(
        &self,
        _event: DirectChatRunEvent,
        _result: MetricsResult,
        _client_kind: DirectChatClientKind,
        _reason: DirectChatRunReason,
    ) {
    }
}

pub(super) fn ws_lifecycle_hook(_state: &Arc<BcsServerState>) -> Arc<dyn WsLifecycleInstrumentationHook> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if let Some(metrics) = &_state.metrics {
            return metrics.clone();
        }
    }

    Arc::new(NoopWsLifecycleInstrumentationHook)
}

pub(super) fn state_machine_loop_instrumentation(
    _metrics: Option<&Arc<crate::metrics::MetricsRuntime>>,
) -> Option<Arc<dyn bcs_service_api::StateMachineLoopInstrumentationHook>> {
    #[cfg(feature = "prometheus-metrics")]
    if let Some(metrics) = _metrics {
        return Some(Arc::new(crate::metrics::MetricsStateMachineLoopHook::new(metrics.env.clone())));
    }
    None
}

pub(super) fn direct_chat_run_lifecycle_hook(
    _metrics: Option<&Arc<crate::metrics::MetricsRuntime>>,
) -> Arc<dyn DirectChatRunLifecycleHook> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if let Some(metrics) = _metrics {
            return Arc::new(crate::metrics::MetricsDirectChatRunLifecycleHook::new(
                metrics.env.clone(),
            ));
        }
    }

    Arc::new(NoopDirectChatRunLifecycleHook)
}
