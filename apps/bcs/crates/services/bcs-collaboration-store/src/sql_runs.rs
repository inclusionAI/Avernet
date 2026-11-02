use super::*;
#[async_trait]
impl StateMachineRunRepoPort for MySqlCollaborationStore {
    async fn get_node_statuses(&self, run: &str, nodes: &[String]) -> ServiceResult<BTreeMap<String, StateMachineNodeStatus>> {
        self.branch_node_statuses(run, nodes).await
    }
    async fn skip_nodes(&self, run: &str, nodes: &[String], at: u64) -> ServiceResult<bool> {
        self.skip_branch_nodes(run, nodes, at).await
    }
    async fn accept_history_message(&self, command: AcceptStateMachineHistory) -> ServiceResult<Option<StateMachineHistoryCheckpoint>> {
        let checkpoint = history::checkpoint(&self.env, command.payload.clone())?;
        Ok(self.commit_eventful_transition(StateMachineEventfulTransition::AcceptHistory(command)).await?.then_some(checkpoint))
    }
    async fn get_history_message(&self, id: &StateMachineHistoryIdentity) -> ServiceResult<Option<StateMachineHistoryCheckpoint>> { self.history_get(id).await }
    async fn list_history_messages_pending(&self, after: Option<&StateMachineHistoryCursor>, limit: usize) -> ServiceResult<StateMachineHistoryPage> { self.history_page(after, limit).await }
    async fn confirm_history_message(&self, checkpoint: &StateMachineHistoryCheckpoint, at: u64) -> ServiceResult<()> { self.history_confirm(checkpoint, at).await }

    async fn list_unrepaired_history_runs(&self, runs: &[String]) -> ServiceResult<Vec<String>> { self.unrepaired_history_runs(runs).await }
    async fn confirm_terminal_history_repair(&self, run: &str, at: u64) -> ServiceResult<()> { self.confirm_history_repair(run, at).await }
    async fn list_terminal_runs_for_cleanup(&self, after: Option<&str>, limit: usize) -> ServiceResult<Vec<String>> { self.terminal_cleanup_candidates(after, limit).await }
    async fn cleanup_terminal_run_checkpoints(&self, run: &str, limit: usize) -> ServiceResult<usize> { self.cleanup_terminal_work(run, limit).await }
    async fn fail_missing_startup(&self, command: bcs_service_api::FailStateMachineStartup) -> ServiceResult<bool> { self.fail_startup_gap(command).await }
    async fn get_startup_failure(&self, run: &str) -> ServiceResult<Option<bcs_service_api::StateMachineStartupFailure>> { self.startup_failure(run).await }
    async fn fail_missing_dispatch(&self, run: &str, node: &str, attempt: i32, now: u64) -> ServiceResult<bool> { self.fail_dispatch_gap(run, node, attempt, now).await }

    async fn save_terminal_im(&self, payload: bcs_service_api::StateMachineTerminalImPayload) -> ServiceResult<bool> { self.save_terminal_notification(payload).await }
    async fn get_terminal_im(&self, run: &str) -> ServiceResult<Option<bcs_service_api::StateMachineTerminalImCheckpoint>> { self.get_terminal_notification(run).await }
    async fn list_terminal_im_pending(&self, after: Option<&str>, limit: usize) -> ServiceResult<Vec<String>> { self.list_terminal_notifications(after, limit).await }
    async fn claim_terminal_im(&self, run: &str, owner: String, now: u64, until: u64) -> ServiceResult<Option<bcs_service_api::StateMachineTerminalImClaim>> { self.claim_terminal_notification(run, owner, now, until).await }
    async fn update_terminal_im_progress(&self, claim: &bcs_service_api::StateMachineTerminalImClaim, expected: bcs_service_api::StateMachineTerminalImProgress, next: bcs_service_api::StateMachineTerminalImProgress, now: u64) -> ServiceResult<bool> { self.update_terminal_notification(claim, expected, next, now).await }
    async fn release_terminal_im(&self, claim: &bcs_service_api::StateMachineTerminalImClaim) -> ServiceResult<bool> { self.release_terminal_notification(claim).await }
    async fn supersede_terminal_im(&self, run: &str) -> ServiceResult<bool> { self.supersede_terminal_notification(run).await }

    async fn save_chat_result(&self, payload: bcs_service_api::StateMachineChatResultPayload) -> ServiceResult<bool> { self.save_publication(payload).await }
    async fn get_chat_result(&self, run: &str) -> ServiceResult<Option<bcs_service_api::StateMachineChatResultCheckpoint>> { self.get_publication(run).await }
    async fn claim_chat_result(&self, run: &str, owner: String, now: u64, until: u64) -> ServiceResult<Option<bcs_service_api::StateMachineChatResultClaim>> { self.claim_publication(run, owner, now, until).await }
    async fn begin_chat_result_send(&self, claim: &bcs_service_api::StateMachineChatResultClaim, now: u64) -> ServiceResult<bool> { self.begin_publication(claim, now).await }
    async fn finish_chat_result(&self, claim: &bcs_service_api::StateMachineChatResultClaim, outcome: bcs_service_api::StateMachineChatResultOutcome, now: u64) -> ServiceResult<bool> { self.finish_publication(claim, outcome, now).await }
    async fn expire_chat_result(&self, run: &str, now: u64) -> ServiceResult<bool> { self.expire_publication(run, now).await }
    async fn release_chat_result(&self, claim: &bcs_service_api::StateMachineChatResultClaim) -> ServiceResult<bool> { self.release_publication(claim).await }

    async fn save_node_dispatch(&self, payload: StateMachineDispatchPayload) -> ServiceResult<bool> { self.save_dispatch(payload).await }
    async fn get_node_dispatch(&self, run: &str, node: &str, attempt: i32) -> ServiceResult<Option<StateMachineDispatchCheckpoint>> { self.get_dispatch(run, node, attempt).await }
    async fn claim_node_dispatch(&self, run: &str, node: &str, attempt: i32, owner: String, now: u64, until: u64) -> ServiceResult<Option<StateMachineDispatchClaim>> { self.claim_dispatch(run, node, attempt, owner, now, until).await }
    async fn begin_node_dispatch_send(&self, claim: &StateMachineDispatchClaim, now: u64) -> ServiceResult<bool> { self.begin_dispatch_send(claim, now).await }
    async fn finish_node_dispatch(&self, claim: &StateMachineDispatchClaim, result: StateMachineDispatchResult, now: u64) -> ServiceResult<bool> { self.finish_dispatch(claim, result, now).await }
    async fn expire_node_dispatch(&self, command: FailStateMachineNodeAttempt) -> ServiceResult<bool> { self.expire_dispatch(command).await }
    async fn release_node_dispatch(&self, claim: &StateMachineDispatchClaim) -> ServiceResult<bool> { self.release_dispatch(claim).await }
    async fn supersede_inactive_node_dispatches(&self, run: &str) -> ServiceResult<()> { self.supersede_dispatches(run).await }

    async fn save_run_opening(&self, payload: bcs_service_api::StateMachineOpeningPayload) -> ServiceResult<bool> {
        self.save_opening(payload).await
    }
    async fn get_run_opening(&self, run: &str) -> ServiceResult<Option<bcs_service_api::StateMachineOpeningCheckpoint>> {
        self.get_opening(run).await
    }
    async fn mark_run_opening_delivered(&self, run: &str, at: u64) -> ServiceResult<bool> {
        self.deliver_opening(run, at).await
    }

    async fn begin_node_judging(&self, run: &str, node: &str, attempt: i32, artifact: String, responder: Option<String>) -> ServiceResult<bool> {
        self.begin_judging(run, node, attempt, artifact, responder).await
    }
    async fn claim_node_judging(&self, run: &str, node: &str, attempt: i32, owner: String, now: u64, until: u64) -> ServiceResult<Option<bcs_service_api::StateMachineJudgeClaim>> {
        self.claim_judging(run, node, attempt, owner, now, until).await
    }
    async fn release_node_judging(&self, claim: &bcs_service_api::StateMachineJudgeClaim) -> ServiceResult<bool> {
        self.release_judging(claim).await
    }

    async fn commit_eventful_transition(
        &self,
        transition: StateMachineEventfulTransition,
    ) -> ServiceResult<bool> {
        self.sql_commit_eventful_transition(transition).await
    }

    async fn create_run(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<()> {
        self.sql_create_run(run, nodes).await
    }

    async fn create_run_if_session_idle(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<bool> {
        self.sql_create_run_if_session_idle(run, nodes).await
    }

    async fn create_rerun_if_session_idle(
        &self,
        command: CreateStateMachineRerun,
    ) -> ServiceResult<CreateStateMachineRerunOutcome> {
        self.sql_create_rerun_if_session_idle(command).await
    }

    async fn get_direct_rerun(
        &self,
        source_run_id: &str,
    ) -> ServiceResult<Option<StateMachineRun>> {
        self.sql_get_direct_rerun(source_run_id).await
    }

    async fn get_run(&self, run_id: &str) -> ServiceResult<Option<StateMachineRun>> {
        self.sql_get_run(run_id).await
    }

    async fn list_running_runs(&self, after_run_id: Option<&str>, limit: usize) -> ServiceResult<Vec<StateMachineRun>> {
        self.sql_list_running_runs(after_run_id, limit).await
    }

    async fn list_pending_runs(&self, after_run_id: Option<&str>, limit: usize) -> ServiceResult<Vec<StateMachineRun>> {
        self.sql_list_pending_runs(after_run_id, limit).await
    }

    async fn get_run_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Option<StateMachineRun>> {
        self.sql_get_run_by_session_id(session_id).await
    }

    async fn list_runs_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Vec<StateMachineRun>> {
        self.sql_list_runs_by_session_id(session_id).await
    }

    async fn list_node_runs(&self, run_id: &str) -> ServiceResult<Vec<StateMachineNodeRun>> {
        self.sql_list_node_runs(run_id).await
    }

    async fn get_node_run(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> ServiceResult<Option<StateMachineNodeRun>> {
        self.sql_get_node_run(run_id, node_id).await
    }

    async fn mark_node_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<()> {
        self.sql_mark_node_running(run_id, node_id, attempt, delivery_request_id, started_at).await
    }

    async fn mark_node_running_if_run_active(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<bool> {
        self.sql_mark_node_running_if_run_active(run_id, node_id, attempt, delivery_request_id, started_at).await
    }

    async fn complete_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        outcome: String,
        artifact_text: String,
        responded_by: Option<String>,
        completed_at: u64,
    ) -> ServiceResult<bool> {
        self.sql_complete_node_attempt(run_id, node_id, attempt, outcome, artifact_text, responded_by, completed_at).await
    }

    async fn mark_human_node_running_if_run_active(
        &self,
        command: MarkHumanNodeRunningCommand,
    ) -> ServiceResult<bool> {
        self.sql_mark_human_node_running_if_run_active(command).await
    }

    async fn record_node_artifact_if_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
    ) -> ServiceResult<bool> {
        self.sql_record_node_artifact_if_running(run_id, node_id, attempt, artifact_text).await
    }

    async fn record_human_response_if_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
        responded_by: String,
    ) -> ServiceResult<bool> {
        self.sql_record_human_response_if_running(run_id, node_id, attempt, artifact_text, responded_by).await
    }

    async fn fail_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        error: String,
        completed_at: u64,
    ) -> ServiceResult<bool> {
        self.sql_fail_node_attempt(run_id, node_id, attempt, error, completed_at).await
    }

    async fn fail_node_attempt_with_action(&self, command: FailStateMachineNodeAttempt) -> ServiceResult<bool> {
        self.sql_fail_node_attempt_with_action(command).await
    }

    async fn get_node_attempt_failure(&self, run_id: &str, node_id: &str, attempt: i32) -> ServiceResult<Option<StateMachineNodeAttemptFailure>> {
        self.sql_get_node_attempt_failure(run_id, node_id, attempt).await
    }

    async fn schedule_node_retry(
        &self,
        run_id: &str,
        node_id: &str,
        failed_attempt: i32,
        next_attempt: i32,
    ) -> ServiceResult<bool> {
        self.sql_schedule_node_retry(run_id, node_id, failed_attempt, next_attempt).await
    }

    async fn skip_node(&self, run_id: &str, node_id: &str, skipped_at: u64) -> ServiceResult<bool> {
        self.sql_skip_node(run_id, node_id, skipped_at).await
    }

    async fn update_run_status(
        &self,
        run_id: &str,
        status: StateMachineRunStatus,
        output: Option<String>,
        error: Option<String>,
        updated_at: u64,
        completed_at: Option<u64>,
    ) -> ServiceResult<bool> {
        self.sql_update_run_status(run_id, status, output, error, updated_at, completed_at).await
    }

    async fn upsert_delivery_correlation(
        &self,
        correlation: StateMachineDeliveryCorrelation,
    ) -> ServiceResult<()> {
        self.sql_upsert_delivery_correlation(correlation).await
    }

    async fn register_delivery_alias(
        &self,
        delivery_request_id: &str,
        bot_delivery_run_id: String,
    ) -> ServiceResult<()> {
        self.sql_register_delivery_alias(delivery_request_id, bot_delivery_run_id).await
    }

    async fn lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<StateMachineDeliveryCorrelation>> {
        self.sql_lookup_delivery_correlation(run_id).await
    }

    async fn list_expired_running_node_runs(
        &self,
        now_ms: u64,
        timeout_grace_ms: u64,
        limit: usize,
    ) -> ServiceResult<Vec<StateMachineNodeRun>> {
        self.sql_list_expired_running_node_runs(now_ms, timeout_grace_ms, limit).await
    }
}
