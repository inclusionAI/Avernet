//! V1 orchestration and safe errors for A2A registration and Card management.
use async_trait::async_trait;
use bcs_service_api::{
    ServiceError,
    application::{
        a2a_bot::{A2aBotManagementService, RegisterAgentCommand, RegisterNamedA2aBotCommand},
        v1::{ApplicationError, a2a::*},
    },
    types::a2a_bot::{
        A2aCardSource,
        a2a_management::{ManagementCode as Code, validate_card_url},
    },
};
use std::sync::Arc;

pub struct A2aApiService {
    management: Arc<dyn A2aBotManagementService>,
}
impl A2aApiService {
    pub fn new(management: Arc<dyn A2aBotManagementService>) -> Self {
        Self { management }
    }
}
fn source(input: A2aCardInput) -> Result<Option<A2aCardSource>, ApplicationError> {
    match (input.agent_card_url, input.agent_card) {
        (Some(url), None) => {
            validate_card_url(&url).map_err(|error| failure(ServiceError::A2aManagement(error)))?;
            Ok(Some(A2aCardSource::Url(url)))
        }
        (None, Some(card)) => Ok(Some(A2aCardSource::Inline(card))),
        (None, None) => Ok(None),
        _ => Err(ApplicationError::unprocessable(
            "CARD_SOURCE_INVALID",
            "Provide exactly one Card source",
        )),
    }
}
fn required_source(input: A2aCardInput) -> Result<A2aCardSource, ApplicationError> {
    source(input)?.ok_or_else(|| {
        ApplicationError::unprocessable("CARD_SOURCE_INVALID", "Card source required")
    })
}
fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_millis().min(i64::MAX as u128) as i64)
        .unwrap_or(0)
}
pub(crate) fn failure(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::A2aManagement(error) => {
            let code = serde_json::to_value(error.code)
                .ok()
                .and_then(|v| v.as_str().map(str::to_owned))
                .unwrap_or_else(|| "INTERNAL_ERROR".into());
            let message = "A2A management request failed";
            match error.code {
                Code::Unauthenticated => ApplicationError::Unauthenticated,
                Code::PermissionDenied => {
                    ApplicationError::forbidden_code(code, "Provider access denied")
                }
                Code::BotNotFound => ApplicationError::not_found(code, "Agent not found"),
                Code::InvalidRequest => ApplicationError::invalid(code, "Invalid A2A request"),
                Code::ProviderBotRefConflict
                | Code::BotNameConflict
                | Code::SourceAgentConflict
                | Code::SourceAgentChangeNotAllowed
                | Code::TargetChanged
                | Code::CardUrlRequired
                | Code::RefreshSuperseded => ApplicationError::conflict(code, message),
                Code::RequestTooLarge | Code::CardTooLarge => ApplicationError::PayloadTooLarge {
                    code,
                    message: message.into(),
                },
                Code::RateLimited => ApplicationError::QuotaExceeded {
                    code,
                    message: message.into(),
                },
                Code::CardFetchFailed | Code::CardFetchTimeout => {
                    ApplicationError::bad_gateway(code, "AgentCard fetch failed")
                }
                Code::ServiceUnavailable => {
                    ApplicationError::unavailable(code, "A2A service unavailable")
                }
                Code::InternalError => ApplicationError::internal("A2A management failed"),
                _ => ApplicationError::unprocessable(code, message),
            }
        }
        ServiceError::Unauthorized(_) => ApplicationError::Unauthenticated,
        ServiceError::Forbidden(_) | ServiceError::BotHidden(_) | ServiceError::NotFriends(_) => {
            ApplicationError::forbidden("A2A access denied")
        }
        ServiceError::BotNotFound(_)
        | ServiceError::BotNotRegistered(_)
        | ServiceError::ProviderNotFound(_)
        | ServiceError::BotNotConnected(_) => {
            ApplicationError::not_found("a2a_not_found", "Agent not found")
        }
        ServiceError::Conflict(_) => {
            ApplicationError::conflict("a2a_conflict", "A2A state changed")
        }
        ServiceError::InvalidOperation { .. } => {
            ApplicationError::invalid("invalid_request", "Invalid A2A request")
        }
        _ => ApplicationError::internal("A2A operation failed"),
    }
}

#[async_trait]
impl A2aService for A2aApiService {
    async fn register(
        &self,
        command: RegisterA2aAgent,
    ) -> Result<ManagedA2aCard, ApplicationError> {
        self.management
            .register_agent(RegisterAgentCommand {
                provider_admin_token: command.token,
                provider_bot_ref: command.provider_bot_ref,
                owners: command.owners,
                summary: command.summary,
                visibility: command.visibility,
                source: required_source(command.source)?,
                now_ms: now_ms(),
            })
            .await
            .map_err(failure)
    }
    async fn find(
        &self,
        token: &str,
        provider_bot_ref: &str,
    ) -> Result<Vec<ManagedA2aCard>, ApplicationError> {
        self.management
            .find_agents_by_provider_ref(token, provider_bot_ref)
            .await
            .map_err(failure)
    }
    async fn get(&self, token: &str, bot_id: &str) -> Result<ManagedA2aCard, ApplicationError> {
        self.management
            .get_agent_by_id(token, bot_id)
            .await
            .map_err(failure)
    }
    async fn update(&self, command: UpdateA2aAgent) -> Result<ManagedA2aCard, ApplicationError> {
        self.management
            .update_agent_by_id_with_visibility(
                &command.token,
                &command.bot_id,
                source(command.source)?,
                command.visibility,
            )
            .await
            .map_err(failure)
    }
    async fn refresh(&self, token: &str, bot_id: &str) -> Result<ManagedA2aCard, ApplicationError> {
        self.management
            .refresh_agent_by_id(token, bot_id)
            .await
            .map_err(failure)
    }
    async fn register_named(
        &self,
        command: RegisterNamedA2aAgent,
    ) -> Result<NamedA2aRegistration, ApplicationError> {
        self.management
            .register_named(RegisterNamedA2aBotCommand {
                provider_admin_token: command.token,
                provider_name: command.provider_name,
                provider_bot_ref: command.provider_bot_ref,
                owners: command.owners,
                summary: command.summary,
                source: required_source(command.source)?,
                now_ms: now_ms(),
            })
            .await
            .map_err(failure)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::types::a2a_bot::a2a_management::ManagementError;
    use serde_json::json;

    #[test]
    fn management_error_mapping_preserves_machine_codes_without_upstream_details() {
        let codes = [
            Code::Unauthenticated,
            Code::PermissionDenied,
            Code::BotNotFound,
            Code::InvalidRequest,
            Code::ProviderBotRefConflict,
            Code::BotNameConflict,
            Code::SourceAgentConflict,
            Code::SourceAgentChangeNotAllowed,
            Code::TargetChanged,
            Code::CardUrlRequired,
            Code::RefreshSuperseded,
            Code::RequestTooLarge,
            Code::CardTooLarge,
            Code::RateLimited,
            Code::CardFetchFailed,
            Code::CardFetchTimeout,
            Code::ServiceUnavailable,
            Code::InternalError,
            Code::InvalidCard,
        ];
        for code in codes {
            let result = failure(ServiceError::A2aManagement(ManagementError::new(
                code,
                "private-field",
                "PRIVATE_REASON",
            )));
            assert!(!format!("{result:?}").contains("PRIVATE_REASON"));
            assert!(!format!("{result:?}").contains("private-field"));
            if code != Code::Unauthenticated && code != Code::InternalError {
                assert_eq!(
                    result.code(),
                    serde_json::to_value(code).unwrap().as_str().unwrap()
                );
            }
        }
    }

    #[test]
    fn legacy_errors_are_classified_without_exposing_identifiers() {
        let errors = [
            ServiceError::Unauthorized("private-credential".into()),
            ServiceError::Forbidden("private-id".into()),
            ServiceError::BotHidden("private-id".into()),
            ServiceError::NotFriends(vec!["private-id".into()]),
            ServiceError::BotNotFound("private-id".into()),
            ServiceError::BotNotRegistered("private-id".into()),
            ServiceError::ProviderNotFound("private-id".into()),
            ServiceError::BotNotConnected("private-id".into()),
            ServiceError::Conflict("private-id".into()),
            ServiceError::InvalidOperation {
                message: "private-id".into(),
                request_id: None,
            },
            ServiceError::InternalError("private-id".into()),
        ];
        for error in errors {
            assert!(!format!("{:?}", failure(error)).contains("private-"));
        }
    }

    #[test]
    fn source_validation_rejects_credential_urls_and_preserves_inline_payloads() {
        assert!(required_source(A2aCardInput::default()).is_err());
        assert!(
            source(A2aCardInput {
                agent_card_url: Some("https://user:password@example.test/card".into()),
                agent_card: None
            })
            .is_err()
        );
        assert!(matches!(
            source(A2aCardInput {
                agent_card_url: Some("https://example.test/card".into()),
                agent_card: None
            })
            .unwrap(),
            Some(A2aCardSource::Url(_))
        ));
        let value = json!({"unknown":{"keep":42}});
        assert!(
            matches!(source(A2aCardInput {agent_card_url:None,agent_card:Some(value.clone())}).unwrap(),Some(A2aCardSource::Inline(card)) if card==value)
        );
    }
}
