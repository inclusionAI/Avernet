use super::*;
impl CollaborationRuntime {

    /// Terminal release (audit P1 2026-10-25): a run converging to a
    /// terminal state releases its caller instance association so the
    /// bounded runtime-only lineage ledger tracks in-flight work, not
    /// history. Fire-and-forget: a release failure never breaks terminal
    /// convergence (the instance-budget age-out is the safety net). Call
    /// at EVERY terminal point — complete, fail (including failed
    /// creation) and abort — and keep it idempotent.
    pub(super) async fn release_instance_association(&self, run_id: &str) {
        let Some(registrar) = self.caller_instance_registrar.clone() else {
            return;
        };
        if run_id.trim().is_empty() {
            return;
        }
        match registrar.release_instance(run_id).await {
            Ok(true) => info!(target: RUNTIME_LOG_TARGET,
                run_id = %run_id,
                "state_machine: caller instance association released on run terminal"
            ),
            Ok(false) => {}
            Err(error) => warn!(target: RUNTIME_LOG_TARGET,
                run_id = %run_id,
                error = %error,
                "state_machine: caller instance association release refused; budget age-out remains the bound"
            ),
        }
    }

    /// §15.83 S2 rearm: refresh the run's delegation-only instance
    /// association after a HumanInput response is consumed. Same-subject
    /// submissions refresh the window (idempotent per workflow_run_id);
    /// a cross-subject submission is refused by the lineage index's
    /// structural comparison — the original binding never flips. No
    /// registrar in this composition skips opportunistically: the human
    /// response's primary purpose is node data, never a protected
    /// submission (unlike the one-shot route whose primary intent IS the
    /// caller-aware run).
    pub(super) async fn rearm_instance_association(
        &self,
        run: &StateMachineRun,
        ingress: bcs_service_api::application::caller_identity::AuthenticatedHumanCallerIngress,
    ) {
        let Some(registrar) = self.caller_instance_registrar.clone() else {
            tracing::debug!(target: RUNTIME_LOG_TARGET,
                run_id = %run.run_id,
                "state_machine: no caller registrar wired; human-input association rearm skipped"
            );
            return;
        };
        let now_ms = bcs_protocol::now_ms() as i64;
        let association = bcs_service_api::port::OrchestrationInstanceAssociation {
            // Same stamping as start_state_machine_run (§15.90): the
            // mega-turn identity of the instance — the index's structural
            // match expects this exact format for the same run.
            //
            // Environment convergence note: the entry-side (§15.90)
            // `environment` comes from the HTTP adapter's `manifest_env`
            // (bootstrap-resolved), while the rearm resolves
            // `bcs_config::resolve_env_str()` here. They converge in
            // practice — the §15.90 registration requires
            // `ingress.env() == environment`, and runtime startup
            // prefixes both from the SERVER_ENV family — but they are
            // resolved in different places (adapter-supplied vs
            // runtime-resolved), which is why they are not the same
            // literal call site.
            workflow_run_id: run.run_id.clone(),
            environment: bcs_config::resolve_env_str(),
            group_id: run.group_id.clone(),
            session_id: run.session_id.clone(),
            message_turn_id: format!("{}:000-panel", run.run_id),
            history_cutoff_seq: 0,
            // A run-budget fact, not an identity window: the association
            // is bounded by the instance budget so the shared lineage
            // ledger can age out strays; terminal release reclaims first.
            expires_at_ms: now_ms + CALLER_INSTANCE_ASSOCIATION_BUDGET_MS,
            ingress,
            now_ms,
        };
        match registrar.register_instance(association).await {
            Ok(true) => tracing::info!(target: RUNTIME_LOG_TARGET,
                run_id = %run.run_id,
                "state_machine: human input re-armed the caller instance association"
            ),
            Ok(false) => tracing::warn!(target: RUNTIME_LOG_TARGET,
                run_id = %run.run_id,
                "state_machine: human input association rearm refused (cross-subject or capacity); nodes keep the existing lineage"
            ),
            Err(error) => tracing::warn!(target: RUNTIME_LOG_TARGET,
                run_id = %run.run_id, %error,
                "state_machine: human input association rearm failed; nodes stay caller-closed"
            ),
        }
    }
}
