//! Protected sends bypass ordinary queued `before_send`. Register only their
//! trusted, non-secret correlation facts here, before any Provider I/O, and use
//! the same event-source fencing and SSE reader as ordinary Provider sends.
use std::sync::Arc;

use bcs_domain::BotDeliveryTarget;
use bcs_service_api::{
    ActiveBotRunContext, BotRunContext, BotRunContextPort, BotRunScope,
    BotRunTransportOwner, ProviderEventIngestService, ProviderRunTransport,
};
use bcs_service_api::port::{CallerSendError, SendCallerExecutionCommand};

use crate::{HttpProviderTransport, SseInteractionContext, stream_and_drive};

pub(super) struct CallerReception {
    context: Option<Arc<dyn BotRunContextPort>>,
    ingest: Option<Arc<dyn ProviderEventIngestService>>,
    run_id: String,
}

impl CallerReception {
    pub(super) async fn prepare(
        transport: &HttpProviderTransport,
        target: &BotDeliveryTarget,
        command: &SendCallerExecutionCommand,
    ) -> Result<Self, CallerSendError> {
        let reception = Self {
            context: transport.bot_run_context.read().expect("bot_run_context lock poisoned").clone(),
            ingest: transport.event_ingest.read().expect("event_ingest lock poisoned").clone(),
            run_id: command.association.run_id.clone(),
        };
        // Preserve standalone JSON-ACK compatibility. An SSE response without
        // these dependencies is never acknowledged as accepted (see below).
        let Some(context) = &reception.context else { return Ok(reception); };
        let expected = BotRunContext {
            run_id: reception.run_id.clone(),
            bot_id: command.association.target_bot_id.clone(),
            group_id: command.group_id.clone().unwrap_or_default(),
            bcs_session_id: Some(command.association.session_id.clone()),
            deadline_ms: command.not_after_ms.max(0) as u64,
            terminal: false,
        };
        let existing = context.get_context(&reception.run_id).await;
        if existing.as_ref().is_some_and(|ctx| !same_live_context(ctx, &expected)) {
            return Err(CallerSendError::NotSent);
        }
        // Insert-only ownership prevents duplicate readers/sends. A retained
        // registration can represent an earlier uncertain send, not rejection.
        if !context.begin_provider_transport(&reception.run_id, expected.deadline_ms).await {
            return Err(CallerSendError::OutcomeUnknown);
        }
        let registered = async {
            if existing.is_none() {
                context.put_context(expected.clone()).await;
                // put_context has no Result; never ship after a lost write.
                if !context.get_context(&reception.run_id).await
                    .is_some_and(|ctx| same_live_context(&ctx, &expected)) {
                    return Err(CallerSendError::NotSent);
                }
            }
            let active = ActiveBotRunContext {
                canonical_run_id: reception.run_id.clone(),
                downstream_run_id: reception.run_id.clone(),
                downstream_session_key: Some(command.association.session_id.clone()),
                scope: BotRunScope {
                    group_id: expected.group_id,
                    session_id: command.association.session_id.clone(),
                    bot_id: expected.bot_id,
                },
                transport_owner: BotRunTransportOwner::from_target(target),
                provider_bypass_headers: Vec::new(),
                deadline_ms: expected.deadline_ms,
            };
            match context.find_active_run(&reception.run_id).await.map_err(|_| CallerSendError::NotSent)? {
                Some(current) if current != active => Err(CallerSendError::NotSent),
                Some(_) => Ok(()),
                None => context.register_active_run(active).await.map_err(|_| CallerSendError::NotSent),
            }
        }.await;
        if registered.is_err() { reception.clear().await; }
        registered?;
        Ok(reception)
    }

    pub(super) async fn clear(&self) {
        if let Some(context) = &self.context {
            context.clear_provider_transport(&self.run_id).await;
        }
    }

    pub(super) async fn bind(&self, source: ProviderRunTransport) -> Result<(), CallerSendError> {
        if let Some(context) = &self.context {
            // Read back: legacy cache implementations may lose a binding write
            // despite returning true. Never start a reader without its fence.
            if !context.bind_provider_transport(&self.run_id, source).await
                || context.get_provider_transport(&self.run_id).await != Some(source) {
                return Err(CallerSendError::OutcomeUnknown);
            }
        }
        Ok(())
    }

    pub(super) async fn accept_sse(
        &self,
        transport: &HttpProviderTransport,
        target: &BotDeliveryTarget,
        response: reqwest::Response,
        bot_id: &str,
    ) -> Result<(), CallerSendError> {
        let (Some(context), Some(ingest)) = (&self.context, &self.ingest) else {
            tracing::warn!(run_id = %self.run_id, "protected caller send: SSE ingest deps not wired");
            return Err(CallerSendError::OutcomeUnknown);
        };
        self.bind(ProviderRunTransport::Sse).await?;
        let interaction_context = transport.interactions.read().expect("interactions lock poisoned")
            .clone().and_then(|service| service.upgrade()).map(|service| SseInteractionContext {
                service, provider_target: target.clone(), provider_bypass_headers: Vec::new(),
            });
        let (run_id, bot_id, ingest, context) = (self.run_id.clone(), bot_id.to_owned(), ingest.clone(), context.clone());
        tracing::info!(%run_id, "protected caller send: SSE stream accepted; spawning reader");
        tokio::spawn(async move {
            stream_and_drive(response, run_id, bot_id, ingest, context, interaction_context).await;
        });
        Ok(())
    }
}

fn same_live_context(actual: &BotRunContext, expected: &BotRunContext) -> bool {
    !actual.terminal && actual.run_id == expected.run_id && actual.bot_id == expected.bot_id
        && actual.group_id == expected.group_id && actual.bcs_session_id == expected.bcs_session_id
        && actual.deadline_ms == expected.deadline_ms
}
