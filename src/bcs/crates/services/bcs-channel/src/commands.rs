//! Channel(IM)slash 命令拦截：`/new` 会话重置。
//!
//! 当前仅支持 `/new`：归档当前会话（Running → Completed，历史保留），
//! 后续消息经既有 lazy rollover 自动创建全新会话。解析器为 match 结构，
//! 后续命令（/help 等）在这里扩展。

use std::collections::{HashMap, HashSet, VecDeque};

use tokio::sync::Mutex;
use tracing::{info, warn};

use bcs_domain::{ChannelBinding, SessionScope};
use bcs_service_api::application::channel::{
    ChannelUseCaseError, InboundMessage, OutboundMessage,
};
use bcs_service_api::StateMachineTerminalEvent;

use crate::{BcsChannelService, ResolvedInboundContext};

/// 排队重置的兜底过期：bot 假死导致终态事件永远不到达时，
/// 由下一条入站消息顺带执行超过该阈值的排队重置。
pub(crate) const PENDING_RESET_STALE_MS: u64 = 1_800_000;

pub(crate) const RESET_DONE_TEXT: &str = "已开启全新会话，此前的聊天记录已归档。";
pub(crate) const RESET_QUEUED_TEXT: &str = "当前任务仍在运行中，任务结束后将自动开启新会话。";
pub(crate) const NOTHING_TO_RESET_TEXT: &str = "当前没有进行中的会话，直接发送消息即可开始新会话。";
pub(crate) const STATE_MACHINE_STARTING_TEXT: &str = "流程正在启动，请稍后再试。";

/// 识别出的 channel 命令。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ChannelCommand {
    NewSession,
}

/// 仅精确匹配（去首尾空白）；`/new foo`、`//new` 视为普通消息。
pub(crate) fn parse_channel_command(text: &str) -> Option<ChannelCommand> {
    match text.trim() {
        "/new" => Some(ChannelCommand::NewSession),
        _ => None,
    }
}

/// 排队中的会话重置请求（等待在途 run 结束）。
#[derive(Debug, Clone)]
pub(crate) struct PendingSessionReset {
    pub(crate) old_session_id: String,
    pub(crate) binding_id: String,
    pub(crate) im_conversation_id: String,
    pub(crate) im_conversation_type: String,
    pub(crate) session_scope: SessionScope,
    pub(crate) im_user_id: Option<String>,
    pub(crate) source_im_message_id: String,
    /// `/new` 时刻快照的在途 run 集合；之后新起的 run 不延长等待。
    pub(crate) waiting_on: HashSet<String>,
    pub(crate) requested_at_ms: u64,
}

/// channel 层 chat run 在途跟踪 + 排队重置记录。
///
/// chat run 只在 dispatch 时（`handle_web_send` 返回 active_run_ids）和
/// 终态事件经过 `try_outbound`（ChatFinal / error / aborted）时可见，
/// 因此在 channel 层自行维护快照，不跨 crate 新增查询 API。
pub(crate) struct SessionResetTracker {
    state: Mutex<SessionResetState>,
    active_run_limit: usize,
}

#[derive(Default)]
struct SessionResetState {
    /// session_id -> 在途 chat run 集合。
    active_runs: HashMap<String, HashSet<String>>,
    /// run_id -> session_id 反向索引。
    run_sessions: HashMap<String, String>,
    /// run 播种顺序（FIFO 驱逐用）。
    run_order: VecDeque<String>,
    /// conversation_key -> 排队中的重置。
    pending: HashMap<String, PendingSessionReset>,
}

impl SessionResetTracker {
    pub(crate) fn new(active_run_limit: usize) -> Self {
        Self {
            state: Mutex::new(SessionResetState::default()),
            active_run_limit,
        }
    }

    /// dispatch 成功后播种该 session 新起的 run。
    pub(crate) async fn seed_runs(&self, session_id: &str, run_ids: &[String]) {
        let mut state = self.state.lock().await;
        for run_id in run_ids {
            if run_id.is_empty() {
                continue;
            }
            state
                .active_runs
                .entry(session_id.to_string())
                .or_default()
                .insert(run_id.clone());
            state
                .run_sessions
                .insert(run_id.clone(), session_id.to_string());
            state.run_order.push_back(run_id.clone());
        }
        while state.run_order.len() > self.active_run_limit {
            let Some(oldest) = state.run_order.pop_front() else {
                break;
            };
            if let Some(session_id) = state.run_sessions.remove(&oldest)
                && let Some(runs) = state.active_runs.get_mut(&session_id)
            {
                runs.remove(&oldest);
                if runs.is_empty() {
                    state.active_runs.remove(&session_id);
                }
            }
        }
    }

    /// `/new` 时刻该 session 的在途 run 快照。
    pub(crate) async fn active_run_ids(&self, session_id: &str) -> HashSet<String> {
        let state = self.state.lock().await;
        state.active_runs.get(session_id).cloned().unwrap_or_default()
    }

    pub(crate) async fn has_pending(&self, conversation_key: &str) -> bool {
        let state = self.state.lock().await;
        state.pending.contains_key(conversation_key)
    }

    /// 登记排队重置；同一 conversation 已有排队时拒绝（幂等）。
    pub(crate) async fn begin_pending(
        &self,
        conversation_key: String,
        reset: PendingSessionReset,
    ) -> bool {
        let mut state = self.state.lock().await;
        if state.pending.contains_key(&conversation_key) {
            return false;
        }
        state.pending.insert(conversation_key, reset);
        true
    }

    /// bot 假死兜底：超过 stale_ms 的排队重置被取走执行。
    pub(crate) async fn take_pending_if_stale(
        &self,
        conversation_key: &str,
        now_ms: u64,
        stale_ms: u64,
    ) -> Option<PendingSessionReset> {
        let mut state = self.state.lock().await;
        let stale = state
            .pending
            .get(conversation_key)
            .is_some_and(|reset| now_ms.saturating_sub(reset.requested_at_ms) > stale_ms);
        if stale {
            state.pending.remove(conversation_key)
        } else {
            None
        }
    }

    /// run 终态观测：从索引中移除该 run，并扣减所有排队的 waiting_on；
    /// 返回因此清空了等待集合、可以执行的排队重置。
    pub(crate) async fn observe_run_terminal(&self, run_id: &str) -> Vec<PendingSessionReset> {
        let mut state = self.state.lock().await;
        if let Some(session_id) = state.run_sessions.remove(run_id)
            && let Some(runs) = state.active_runs.get_mut(&session_id)
        {
            runs.remove(run_id);
            if runs.is_empty() {
                state.active_runs.remove(&session_id);
            }
        }
        let mut drained = Vec::new();
        state.pending.retain(|_, reset| {
            reset.waiting_on.remove(run_id);
            if reset.waiting_on.is_empty() {
                drained.push(reset.clone());
                false
            } else {
                true
            }
        });
        drained
    }

    /// 无条件取走排队重置（竞态闭合：登记后发现在途 run 已结束时立即执行）。
    pub(crate) async fn take_pending(&self, conversation_key: &str) -> Option<PendingSessionReset> {
        let mut state = self.state.lock().await;
        state.pending.remove(conversation_key)
    }
}

/// 排队重置的 conversation 键（SessionScope 无 Hash，用字符串标签）。
pub(crate) fn reset_conversation_key(
    binding_id: &str,
    im_conversation_id: &str,
    scope: SessionScope,
    im_user_id: Option<&str>,
) -> String {
    format!(
        "{}|{}|{}|{}",
        binding_id,
        im_conversation_id,
        crate::session_scope_label(scope),
        im_user_id.unwrap_or("")
    )
}

impl BcsChannelService {
    /// 入站命令拦截：是命令则完整处理并返回 Ok(Some(()))。
    ///
    /// 位于 `try_consume_human_input` 之前：等待回复的 HumanInput 卡片
    /// 不得把 `/new` 吞作卡片回复。
    pub(crate) async fn try_execute_channel_command(
        &self,
        binding: &ChannelBinding,
        msg: &InboundMessage,
        actor_id: &str,
    ) -> Result<Option<()>, ChannelUseCaseError> {
        let Some(command) = parse_channel_command(&msg.text) else {
            return Ok(None);
        };
        info!(
            channel_type = %msg.channel_type,
            account_ref = %msg.account_ref,
            binding_id = %binding.id,
            msg_id = %msg.msg_id,
            im_conversation_id = %msg.im_conversation_id,
            command = ?command,
            "channel command: received"
        );
        match command {
            ChannelCommand::NewSession => {
                let ctx = self.resolve_inbound_context(binding, msg, actor_id).await?;
                self.execute_new_session(&ctx, binding, msg).await?;
            }
        }
        Ok(Some(()))
    }

    async fn execute_new_session(
        &self,
        ctx: &ResolvedInboundContext,
        binding: &ChannelBinding,
        msg: &InboundMessage,
    ) -> Result<(), ChannelUseCaseError> {
        let conversation_key = reset_conversation_key(
            &ctx.binding_id,
            &msg.im_conversation_id,
            ctx.session_scope,
            ctx.im_user_id.as_deref(),
        );
        // 重复 /new 幂等：已有排队重置时仅重答排队文案。
        if self.session_reset_tracker.has_pending(&conversation_key).await {
            self.send_command_reply(
                binding,
                &msg.im_conversation_id,
                &msg.conversation_type,
                ctx.im_user_id.as_deref(),
                "",
                RESET_QUEUED_TEXT,
                Some(&msg.msg_id),
            )
            .await?;
            return Ok(());
        }
        let mapping = self
            .conversations
            .get(
                &ctx.binding_id,
                &msg.im_conversation_id,
                ctx.session_scope,
                ctx.im_user_id.as_deref(),
            )
            .await?;
        let active = match &mapping {
            Some(map) => match self.sessions.get(&map.bcs_session_id).await {
                // 与 resolve_or_create_chat_session 的复用判定保持一致：
                // 仅 Running 且同 group 的会话才算"进行中的会话"。
                Some(session)
                    if session.status == crate::SessionStatus::Running
                        && session.group_id == ctx.group_id =>
                {
                    Some((map.clone(), session))
                }
                _ => None,
            },
            None => None,
        };
        let Some((mapping, session)) = active else {
            self.send_command_reply(
                binding,
                &msg.im_conversation_id,
                &msg.conversation_type,
                ctx.im_user_id.as_deref(),
                "",
                NOTHING_TO_RESET_TEXT,
                Some(&msg.msg_id),
            )
            .await?;
            return Ok(());
        };

        // state-machine 启动窗口：映射新鲜但 run 尚未落库，与
        // start_state_machine_from_inbound 的判定保持一致。
        if ctx.state_machine_trigger {
            let run_view = self
                .collaboration_runtime
                .get_state_machine_run_by_session_id(&session.id)
                .await
                .map_err(|error| {
                    ChannelUseCaseError::Internal(crate::ServiceError::InternalError(
                        error.to_string(),
                    ))
                })?;
            if run_view.is_none()
                && (self.now_ms)().saturating_sub(mapping.last_active_at)
                    < crate::CHANNEL_START_STALE_MS
            {
                self.send_command_reply(
                    binding,
                    &msg.im_conversation_id,
                    &msg.conversation_type,
                    ctx.im_user_id.as_deref(),
                    &session.id,
                    STATE_MACHINE_STARTING_TEXT,
                    Some(&msg.msg_id),
                )
                .await?;
                return Ok(());
            }
        }

        let waiting_on = self.collect_waiting_runs(ctx, &session.id).await?;
        let reset = PendingSessionReset {
            old_session_id: session.id.clone(),
            binding_id: ctx.binding_id.clone(),
            im_conversation_id: msg.im_conversation_id.clone(),
            im_conversation_type: msg.conversation_type.clone(),
            session_scope: ctx.session_scope,
            im_user_id: ctx.im_user_id.clone(),
            source_im_message_id: msg.msg_id.clone(),
            waiting_on,
            requested_at_ms: (self.now_ms)(),
        };
        if reset.waiting_on.is_empty() {
            self.execute_session_reset(reset).await?;
            return Ok(());
        }
        let waiting_count = reset.waiting_on.len();
        let old_session_id = reset.old_session_id.clone();
        if self
            .session_reset_tracker
            .begin_pending(conversation_key.clone(), reset)
            .await
        {
            info!(
                binding_id = %ctx.binding_id,
                bcs_session_id = %old_session_id,
                waiting_run_count = waiting_count,
                "channel command: session reset queued"
            );
            // 竞态闭合：登记后重新收集，若在途 run 恰好已全部结束则立即执行。
            if self
                .collect_waiting_runs(ctx, &old_session_id)
                .await?
                .is_empty()
            {
                if let Some(reset) = self.session_reset_tracker.take_pending(&conversation_key).await
                {
                    self.execute_session_reset(reset).await?;
                }
                return Ok(());
            }
        }
        self.send_command_reply(
            binding,
            &msg.im_conversation_id,
            &msg.conversation_type,
            ctx.im_user_id.as_deref(),
            &old_session_id,
            RESET_QUEUED_TEXT,
            Some(&msg.msg_id),
        )
        .await?;
        Ok(())
    }

    /// `/new` 时刻的在途 run 集合：chat run 取 tracker 快照；
    /// state-machine group 额外查 DB 中的 run 视图。
    async fn collect_waiting_runs(
        &self,
        ctx: &ResolvedInboundContext,
        session_id: &str,
    ) -> Result<HashSet<String>, ChannelUseCaseError> {
        let mut waiting = self.session_reset_tracker.active_run_ids(session_id).await;
        if ctx.state_machine_trigger
            && let Some(view) = self
                .collaboration_runtime
                .get_state_machine_run_by_session_id(session_id)
                .await
                .map_err(|error| {
                    ChannelUseCaseError::Internal(crate::ServiceError::InternalError(
                        error.to_string(),
                    ))
                })?
            && view.run.status == bcs_domain::StateMachineRunStatus::Running
            && view.run.group_id == ctx.group_id
        {
            waiting.insert(view.run.run_id);
        }
        Ok(waiting)
    }

    /// 锁保护的重置执行：复查映射仍指向旧会话 → CAS 归档 → 确认回复。
    /// 幂等：并发 /new 或重复终态事件下安全。
    async fn execute_session_reset(&self, reset: PendingSessionReset) -> Result<(), ChannelUseCaseError> {
        {
            let _guard = self.chat_session_resolution_lock.lock().await;
            let current = self
                .conversations
                .get(
                    &reset.binding_id,
                    &reset.im_conversation_id,
                    reset.session_scope,
                    reset.im_user_id.as_deref(),
                )
                .await?;
            if current
                .as_ref()
                .is_none_or(|map| map.bcs_session_id != reset.old_session_id)
            {
                info!(
                    binding_id = %reset.binding_id,
                    bcs_session_id = %reset.old_session_id,
                    "channel command: session reset skipped (mapping moved)"
                );
                return Ok(());
            }
            self.sessions
                .complete_if_running(
                    &reset.old_session_id,
                    Some(serde_json::json!({"reason": "channel_command_new"})),
                    None,
                )
                .await?;
        }
        info!(
            binding_id = %reset.binding_id,
            bcs_session_id = %reset.old_session_id,
            im_conversation_id = %reset.im_conversation_id,
            "channel command: session reset executed"
        );
        // 确认回复失败不阻碍归档（归档已持久化）；deferred 路径下
        // binding 可能已下线，此时跳过回复。
        let binding = self.bindings.get(&reset.binding_id).await?;
        match binding {
            Some(binding) if binding.status == bcs_domain::BindingStatus::Active => {
                if let Err(error) = self
                    .send_command_reply(
                        &binding,
                        &reset.im_conversation_id,
                        &reset.im_conversation_type,
                        reset.im_user_id.as_deref(),
                        &reset.old_session_id,
                        RESET_DONE_TEXT,
                        Some(&reset.source_im_message_id),
                    )
                    .await
                {
                    warn!(
                        binding_id = %reset.binding_id,
                        bcs_session_id = %reset.old_session_id,
                        error = %error,
                        "channel command: reset confirmation delivery failed"
                    );
                }
            }
            _ => {
                info!(
                    binding_id = %reset.binding_id,
                    "channel command: reset confirmation skipped (binding inactive)"
                );
            }
        }
        Ok(())
    }

    /// `try_outbound` 顶部 hook：观测 chat run 终态并执行因此排干的重置。
    /// 必须在所有提前 return 之前调用，保证可见性过滤或投递失败不饿死重置。
    pub(crate) async fn observe_outbound_terminal(&self, msg: &OutboundMessage) {
        let terminal = match msg.kind {
            crate::ChannelOutboundEventKind::ChatFinal => true,
            crate::ChannelOutboundEventKind::System => {
                msg.purpose == crate::ChannelOutboundPurpose::Conversation
                    && matches!(
                        msg.raw_payload.get("state").and_then(serde_json::Value::as_str),
                        Some("error" | "aborted")
                    )
            }
            _ => false,
        };
        if !terminal || msg.run_id.is_empty() {
            return;
        }
        let drained = self.session_reset_tracker.observe_run_terminal(&msg.run_id).await;
        for reset in drained {
            info!(
                run_id = %msg.run_id,
                bcs_session_id = %reset.old_session_id,
                "channel command: run terminal observed, executing queued reset"
            );
            self.execute_deferred_reset(reset).await;
        }
    }

    /// `publish_state_machine_terminal` 顶部 hook：state-machine run 终态观测。
    /// 该方法在 run 终态时必被调用（即使无 IM 通知），是可靠的观测点。
    pub(crate) async fn observe_state_machine_terminal(&self, event: &StateMachineTerminalEvent) {
        let drained = self
            .session_reset_tracker
            .observe_run_terminal(&event.run_id)
            .await;
        for reset in drained {
            info!(
                run_id = %event.run_id,
                bcs_session_id = %reset.old_session_id,
                "channel command: state-machine terminal observed, executing queued reset"
            );
            self.execute_deferred_reset(reset).await;
        }
    }

    /// 入站顺带执行超时的排队重置（bot 假死导致终态事件永远不到达的兜底）。
    pub(crate) async fn maybe_execute_stale_reset(
        &self,
        ctx: &ResolvedInboundContext,
        msg: &InboundMessage,
    ) {
        let conversation_key = reset_conversation_key(
            &ctx.binding_id,
            &msg.im_conversation_id,
            ctx.session_scope,
            ctx.im_user_id.as_deref(),
        );
        let Some(reset) = self
            .session_reset_tracker
            .take_pending_if_stale(&conversation_key, (self.now_ms)(), PENDING_RESET_STALE_MS)
            .await
        else {
            return;
        };
        info!(
            binding_id = %ctx.binding_id,
            bcs_session_id = %reset.old_session_id,
            "channel command: executing stale queued reset"
        );
        self.execute_deferred_reset(reset).await;
    }

    async fn execute_deferred_reset(&self, reset: PendingSessionReset) {
        if let Err(error) = self.execute_session_reset(reset).await {
            warn!(error = %error, "channel command: deferred session reset failed");
        }
    }

    /// 命令回复走 provider 直接投递（仿 deliver_human_input_event），
    /// 不依赖 try_outbound —— 无会话场景（/new 为首条消息）没有可供
    /// 路由的 session。bcs_session_id 在无会话时传空串。
    #[allow(clippy::too_many_arguments)]
    async fn send_command_reply(
        &self,
        binding: &ChannelBinding,
        im_conversation_id: &str,
        im_conversation_type: &str,
        im_user_id: Option<&str>,
        bcs_session_id: &str,
        text: &str,
        source_im_message_id: Option<&str>,
    ) -> Result<(), ChannelUseCaseError> {
        let provider = self.provider_for(&binding.channel_type)?;
        let binding_ref = crate::ChannelBindingRef {
            channel_type: binding.channel_type.clone(),
            account_ref: binding.account_ref.clone(),
        };
        if !provider.delivery().is_available(&binding_ref).await {
            return Err(ChannelUseCaseError::InvalidParams(
                "channel command reply delivery is unavailable".to_string(),
            ));
        }
        let result = provider
            .delivery()
            .deliver_event(crate::ChannelOutboundEvent {
                binding_ref,
                im_conversation_id: im_conversation_id.to_string(),
                im_conversation_type: im_conversation_type.to_string(),
                im_user_id: im_user_id.map(str::to_string),
                im_user_display_name: None,
                bcs_session_id: bcs_session_id.to_string(),
                run_id: String::new(),
                sender_actor_id: "bcs_channel".to_string(),
                sender_label: "BCS".to_string(),
                render_sender_label: false,
                sender_role: bcs_domain::ParticipantRole::Driver,
                kind: crate::ChannelOutboundEventKind::System,
                purpose: crate::ChannelOutboundPurpose::HumanInputAck,
                text: Some(text.to_string()),
                raw_payload: serde_json::json!({
                    "type": "channel.command",
                    "command": "new",
                }),
                render_hint: crate::ChannelRenderHint::Render,
                source_im_message_id: source_im_message_id.map(str::to_string),
            })
            .await?;
        if !result.delivered {
            return Err(ChannelUseCaseError::Internal(
                result.error.unwrap_or_else(|| {
                    crate::ServiceError::InternalError(
                        "channel command reply was not confirmed".to_string(),
                    )
                }),
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pending_reset(run_ids: &[&str]) -> PendingSessionReset {
        PendingSessionReset {
            old_session_id: "group_1:00000001".to_string(),
            binding_id: "binding_1".to_string(),
            im_conversation_id: "conv_1".to_string(),
            im_conversation_type: "1".to_string(),
            session_scope: SessionScope::Conversation,
            im_user_id: Some("u1".to_string()),
            source_im_message_id: "msg_new".to_string(),
            waiting_on: run_ids.iter().map(|id| id.to_string()).collect(),
            requested_at_ms: 100,
        }
    }

    #[test]
    fn parse_accepts_exact_new_command() {
        assert_eq!(parse_channel_command("/new"), Some(ChannelCommand::NewSession));
    }

    #[test]
    fn parse_tolerates_surrounding_whitespace() {
        assert_eq!(
            parse_channel_command("  /new \n"),
            Some(ChannelCommand::NewSession)
        );
    }

    #[test]
    fn parse_rejects_non_command_texts() {
        for text in ["", "new", "/new x", "//new", "/NEW", "/new/"] {
            assert_eq!(parse_channel_command(text), None, "text: {text:?}");
        }
    }

    #[tokio::test]
    async fn seeded_runs_are_reported_as_active() {
        let tracker = SessionResetTracker::new(16);
        tracker
            .seed_runs("session_1", &["run_1".to_string(), "run_2".to_string()])
            .await;

        let active = tracker.active_run_ids("session_1").await;
        assert_eq!(
            active,
            ["run_1".to_string(), "run_2".to_string()]
                .into_iter()
                .collect::<HashSet<_>>()
        );
        assert!(tracker.active_run_ids("session_2").await.is_empty());
    }

    #[tokio::test]
    async fn terminal_drains_run_from_active_set() {
        let tracker = SessionResetTracker::new(16);
        tracker
            .seed_runs("session_1", &["run_1".to_string(), "run_2".to_string()])
            .await;

        tracker.observe_run_terminal("run_1").await;

        assert_eq!(
            tracker.active_run_ids("session_1").await,
            ["run_2".to_string()].into_iter().collect::<HashSet<_>>()
        );
    }

    #[tokio::test]
    async fn terminal_for_unknown_run_is_noop() {
        let tracker = SessionResetTracker::new(16);
        tracker.seed_runs("session_1", &["run_1".to_string()]).await;

        let drained = tracker.observe_run_terminal("run_unknown").await;

        assert!(drained.is_empty());
        assert_eq!(tracker.active_run_ids("session_1").await.len(), 1);
    }

    #[tokio::test]
    async fn duplicate_terminal_is_idempotent() {
        let tracker = SessionResetTracker::new(16);
        tracker.seed_runs("session_1", &["run_1".to_string()]).await;
        tracker
            .begin_pending("key_1".to_string(), pending_reset(&["run_1"]))
            .await;

        let first = tracker.observe_run_terminal("run_1").await;
        let second = tracker.observe_run_terminal("run_1").await;

        assert_eq!(first.len(), 1);
        assert!(second.is_empty());
        assert!(tracker.active_run_ids("session_1").await.is_empty());
    }

    #[tokio::test]
    async fn pending_waits_for_all_snapshotted_runs() {
        let tracker = SessionResetTracker::new(16);
        tracker
            .seed_runs("session_1", &["run_1".to_string(), "run_2".to_string()])
            .await;
        tracker
            .begin_pending("key_1".to_string(), pending_reset(&["run_1", "run_2"]))
            .await;

        assert!(tracker.observe_run_terminal("run_1").await.is_empty());
        let drained = tracker.observe_run_terminal("run_2").await;

        assert_eq!(drained.len(), 1);
        assert_eq!(drained[0].old_session_id, "group_1:00000001");
    }

    #[tokio::test]
    async fn begin_pending_rejects_duplicate_conversation() {
        let tracker = SessionResetTracker::new(16);
        assert!(
            tracker
                .begin_pending("key_1".to_string(), pending_reset(&["run_1"]))
                .await
        );
        assert!(
            !tracker
                .begin_pending("key_1".to_string(), pending_reset(&["run_2"]))
                .await
        );
        assert!(tracker.has_pending("key_1").await);
        assert!(!tracker.has_pending("key_2").await);
    }

    #[tokio::test]
    async fn stale_pending_is_taken_only_past_threshold() {
        let tracker = SessionResetTracker::new(16);
        tracker
            .begin_pending("key_1".to_string(), pending_reset(&["run_1"]))
            .await;

        assert!(
            tracker
                .take_pending_if_stale("key_1", 100 + 1_000, 1_800_000)
                .await
                .is_none()
        );
        let taken = tracker
            .take_pending_if_stale("key_1", 100 + 1_800_001, 1_800_000)
            .await;
        assert!(taken.is_some());
        assert!(!tracker.has_pending("key_1").await);
    }

    #[tokio::test]
    async fn fifo_eviction_bounds_active_run_tracking() {
        let tracker = SessionResetTracker::new(2);
        tracker
            .seed_runs("session_1", &["run_1".to_string(), "run_2".to_string()])
            .await;
        tracker.seed_runs("session_1", &["run_3".to_string()]).await;

        let active = tracker.active_run_ids("session_1").await;
        assert_eq!(
            active,
            ["run_2".to_string(), "run_3".to_string()]
                .into_iter()
                .collect::<HashSet<_>>()
        );
        // 被驱逐的 run 终态不再有任何效果。
        assert!(tracker.observe_run_terminal("run_1").await.is_empty());
    }

    #[tokio::test]
    async fn take_pending_removes_registration() {
        let tracker = SessionResetTracker::new(16);
        tracker
            .begin_pending("key_1".to_string(), pending_reset(&["run_1"]))
            .await;

        let taken = tracker.take_pending("key_1").await;

        assert!(taken.is_some());
        assert!(!tracker.has_pending("key_1").await);
        assert!(tracker.take_pending("key_1").await.is_none());
    }
}
