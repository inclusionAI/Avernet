//! IM commands that control managed message delivery without forwarding the
//! command text to Bots.

use bcs_domain::ChannelBinding;
use bcs_service_api::application::channel::{ChannelUseCaseError, InboundMessage};
use bcs_service_api::{
    CancelLatestQueuedMessageCommand, ChatAbortCommand, HumanActor,
};
use tracing::warn;

use crate::{BcsChannelService, ResolvedInboundContext};

pub(crate) const ABORT_DONE_TEXT: &str = "已终止当前会话中正在处理的消息。";
pub(crate) const ABORT_PARTIAL_TEXT: &str = "已终止部分处理中消息，仍有部分消息未能终止。";
pub(crate) const ABORT_FAILED_TEXT: &str = "终止失败，请稍后重试。";
pub(crate) const NOTHING_TO_ABORT_TEXT: &str = "当前没有可终止的处理中消息。";
pub(crate) const CANCEL_DONE_TEXT: &str = "已取消当前排队消息。";
pub(crate) const NOTHING_TO_CANCEL_TEXT: &str = "当前没有可取消的排队消息。";

impl BcsChannelService {
    async fn current_running_session(
        &self,
        ctx: &ResolvedInboundContext,
        msg: &InboundMessage,
    ) -> Result<Option<bcs_domain::Session>, ChannelUseCaseError> {
        let Some(mapping) = self
            .conversations
            .get(
                &ctx.binding_id,
                &msg.im_conversation_id,
                ctx.session_scope,
                ctx.im_user_id.as_deref(),
            )
            .await?
        else {
            return Ok(None);
        };
        Ok(self.sessions.try_get(&mapping.bcs_session_id).await?.filter(|session| {
            session.status == crate::SessionStatus::Running && session.group_id == ctx.group_id
        }))
    }

    pub(crate) async fn execute_abort(
        &self,
        ctx: &ResolvedInboundContext,
        binding: &ChannelBinding,
        msg: &InboundMessage,
        actor_id: &str,
    ) -> Result<(), ChannelUseCaseError> {
        let Some(session) = self.current_running_session(ctx, msg).await? else {
            return self
                .send_command_reply(
                    binding,
                    &msg.im_conversation_id,
                    &msg.conversation_type,
                    ctx.im_user_id.as_deref(),
                    "",
                    "abort",
                    NOTHING_TO_ABORT_TEXT,
                    Some(&msg.msg_id),
                )
                .await;
        };
        let caller = bcs_service_api::CallerContext::Human(HumanActor {
            actor_id: actor_id.to_string(),
            staff_no: msg.im_user_id.trim().to_string(),
        });
        let mut joins = tokio::task::JoinSet::new();
        for bot_id in session
            .participants
            .iter()
            .filter(|participant| participant.is_bot())
            .map(|participant| participant.bot_uuid.clone())
        {
            let flow = self.message_flow.clone();
            let command = ChatAbortCommand {
                caller: caller.clone(),
                group_id: session.group_id.clone(),
                session_id: session.id.clone(),
                bot_id,
                run_id: None,
            };
            joins.spawn(async move { flow.handle_chat_abort(command).await });
        }
        let mut aborted_run_ids = Vec::new();
        let mut failures = 0usize;
        while let Some(result) = joins.join_next().await {
            match result {
                Ok(Ok(outcome)) => {
                    aborted_run_ids.extend(outcome.aborted_run_ids);
                    failures += outcome.failures.len();
                    failures += outcome
                        .bot_deliveries
                        .iter()
                        .filter(|delivery| !delivery.delivered)
                        .count();
                }
                Ok(Err(_)) | Err(_) => failures += 1,
            }
        }
        aborted_run_ids.sort();
        aborted_run_ids.dedup();
        for run_id in &aborted_run_ids {
            for reset in self
                .session_reset_tracker
                .observe_run_terminal(run_id)
                .await
            {
                self.execute_deferred_reset(reset).await;
            }
        }
        let text = match (aborted_run_ids.is_empty(), failures == 0) {
            (false, true) => ABORT_DONE_TEXT,
            (false, false) => ABORT_PARTIAL_TEXT,
            (true, false) => ABORT_FAILED_TEXT,
            (true, true) => NOTHING_TO_ABORT_TEXT,
        };
        let reply = self.send_command_reply(
            binding,
            &msg.im_conversation_id,
            &msg.conversation_type,
            ctx.im_user_id.as_deref(),
            &session.id,
            "abort",
            text,
            Some(&msg.msg_id),
        )
        .await;
        if !aborted_run_ids.is_empty() {
            if let Err(error) = reply {
                warn!(error = %error, "channel command: abort confirmation delivery failed");
            }
            Ok(())
        } else {
            reply
        }
    }

    pub(crate) async fn execute_cancel_queued(
        &self,
        ctx: &ResolvedInboundContext,
        binding: &ChannelBinding,
        msg: &InboundMessage,
        actor_id: &str,
    ) -> Result<(), ChannelUseCaseError> {
        let Some(session) = self.current_running_session(ctx, msg).await? else {
            return self
                .send_command_reply(
                    binding,
                    &msg.im_conversation_id,
                    &msg.conversation_type,
                    ctx.im_user_id.as_deref(),
                    "",
                    "cacel",
                    NOTHING_TO_CANCEL_TEXT,
                    Some(&msg.msg_id),
                )
                .await;
        };
        let outcome = self
            .message_flow
            .cancel_latest_queued_message(CancelLatestQueuedMessageCommand {
                caller: bcs_service_api::CallerContext::Human(HumanActor {
                    actor_id: actor_id.to_string(),
                    staff_no: msg.im_user_id.trim().to_string(),
                }),
                group_id: session.group_id.clone(),
                session_id: session.id.clone(),
            })
            .await
            .map_err(ChannelUseCaseError::Internal)?;
        let changed = !outcome.cancelled.is_empty();
        let text = if !changed {
            NOTHING_TO_CANCEL_TEXT
        } else {
            CANCEL_DONE_TEXT
        };
        let reply = self.send_command_reply(
            binding,
            &msg.im_conversation_id,
            &msg.conversation_type,
            ctx.im_user_id.as_deref(),
            &session.id,
            "cacel",
            text,
            Some(&msg.msg_id),
        )
        .await;
        if changed {
            if let Err(error) = reply {
                warn!(error = %error, "channel command: cancellation confirmation delivery failed");
            }
            Ok(())
        } else {
            reply
        }
    }
}
