use std::sync::{Arc, RwLock, Weak};
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_service_api::{BotTerminalEvent, BotTerminalObserverPort, InteractionService};
use tracing::warn;

/// Deferred observer used to break the bootstrap ordering between message flow
/// and interaction service construction. The weak reference also avoids a
/// message-flow -> observer -> interaction -> Provider -> message-flow cycle.
#[derive(Default)]
pub struct InteractionTerminalObserver {
    service: RwLock<Option<Weak<dyn InteractionService>>>,
}

impl InteractionTerminalObserver {
    pub fn set_service(&self, service: Arc<dyn InteractionService>) {
        *self
            .service
            .write()
            .unwrap_or_else(|error| error.into_inner()) = Some(Arc::downgrade(&service));
    }
}

#[async_trait]
impl BotTerminalObserverPort for InteractionTerminalObserver {
    async fn observe(&self, event: BotTerminalEvent) {
        let service = self
            .service
            .read()
            .unwrap_or_else(|error| error.into_inner())
            .as_ref()
            .and_then(Weak::upgrade);
        let Some(service) = service else {
            return;
        };
        if let Err(error) = service
            .invalidate_run(&event.run_id, "bot_terminal", now_ms())
            .await
        {
            warn!(run_id = %event.run_id, %error, "failed to invalidate terminal run interactions");
        }
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use tokio::sync::Mutex;

    use bcs_service_api::{
        InteractionFrontendEvent, InteractionRequestedOutcome, InteractionServiceError,
        ProviderInteractionRequestedCommand, ProviderInteractionResolvedCommand,
        ResolveInteractionCommand, ResolveInteractionResult, ServiceResult,
    };

    use super::*;

    #[derive(Default)]
    struct RecordingService {
        invalidations: Mutex<Vec<(String, String)>>,
    }

    #[async_trait]
    impl InteractionService for RecordingService {
        async fn on_provider_requested(
            &self,
            _command: ProviderInteractionRequestedCommand,
        ) -> ServiceResult<InteractionRequestedOutcome> {
            unreachable!()
        }

        async fn on_provider_resolved(
            &self,
            _command: ProviderInteractionResolvedCommand,
        ) -> ServiceResult<()> {
            unreachable!()
        }

        async fn resolve(
            &self,
            _command: ResolveInteractionCommand,
        ) -> Result<ResolveInteractionResult, InteractionServiceError> {
            unreachable!()
        }

        async fn list_pending(
            &self,
            _bcs_session_id: &str,
        ) -> ServiceResult<Vec<InteractionFrontendEvent>> {
            unreachable!()
        }

        async fn invalidate_run(
            &self,
            bcs_run_id: &str,
            reason: &str,
            _invalidated_at_ms: u64,
        ) -> ServiceResult<usize> {
            self.invalidations
                .lock()
                .await
                .push((bcs_run_id.to_string(), reason.to_string()));
            Ok(1)
        }

        async fn cleanup_terminal(&self, _terminal_before_ms: u64) -> ServiceResult<usize> {
            unreachable!()
        }
    }

    #[tokio::test]
    async fn terminal_event_invalidates_the_correlated_run_without_strong_cycle() {
        let observer = InteractionTerminalObserver::default();
        let service = Arc::new(RecordingService::default());
        let service_port: Arc<dyn InteractionService> = service.clone();
        observer.set_service(service_port.clone());
        assert_eq!(Arc::strong_count(&service_port), 2);

        observer
            .observe(BotTerminalEvent {
                run_id: "run-1".to_string(),
                bot_uuid: "bot-1".to_string(),
                state: bcs_service_api::BotTerminalState::Final,
                text: String::new(),
            })
            .await;

        assert_eq!(
            *service.invalidations.lock().await,
            vec![("run-1".to_string(), "bot_terminal".to_string())]
        );
    }
}
