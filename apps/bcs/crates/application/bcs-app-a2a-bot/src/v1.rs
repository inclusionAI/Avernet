//! V1 orchestration and safe application errors for the A2A control plane.
use async_trait::async_trait;
use bcs_service_api::{
    ServiceError,
    application::{
        a2a_bot::{A2aBotManagementService, RegisterAgentCommand, RegisterNamedA2aBotCommand},
        a2a_chat::{
            A2aChatRunService, A2aRunStatus, AsyncA2aChatCommand, ChatRunCancelCommand,
            ChatRunQueryCommand,
        },
        principal::{BotActor, CallerContext},
        v1::{ApplicationError, BotSelfService, a2a::*},
    },
    types::a2a_bot::{
        A2aCardSource,
        a2a_management::{ManagementCode as Code, validate_card_url},
    },
};
use serde_json::{Value, json};
use std::sync::Arc;

pub struct A2aApiService {
    management: Arc<dyn A2aBotManagementService>,
    identity: Arc<dyn BotSelfService>,
    runs: Arc<dyn A2aChatRunService>,
    run_timeout_ms: u64,
    poll_wait_max_ms: u64,
}
impl A2aApiService {
    pub fn new(
        management: Arc<dyn A2aBotManagementService>,
        identity: Arc<dyn BotSelfService>,
        runs: Arc<dyn A2aChatRunService>,
        run_timeout_ms: u64,
        poll_wait_max_ms: u64,
    ) -> Self {
        Self {
            management,
            identity,
            runs,
            run_timeout_ms,
            poll_wait_max_ms,
        }
    }

    async fn caller(
        &self,
        token: &str,
        actor: Option<&str>,
    ) -> Result<CallerContext, ApplicationError> {
        let view = self.identity.get_me_with_actor(token, actor).await?;
        let bot = view
            .bot
            .ok_or_else(|| ApplicationError::forbidden("Registered Bot required"))?;
        Ok(CallerContext::Bot(BotActor {
            bot_uuid: bot.bot_id,
        }))
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
fn normalized(value: Option<String>) -> Option<String> {
    value
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
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
            ApplicationError::not_found("a2a_not_found", "Agent or run not found")
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
    async fn chat(&self, command: StartNamedA2aChat) -> Result<Value, ApplicationError> {
        // Authenticate before resolving any target. Resolution never grants chat permission.
        let caller = self
            .caller(&command.token, command.actor.as_deref())
            .await?;
        let target = self
            .management
            .resolve_provider_ref_target(&command.provider_name, &command.provider_bot_ref)
            .await
            .map_err(failure)?;
        let run_id = uuid::Uuid::new_v4().to_string();
        let bot_id = match &caller {
            CallerContext::Bot(bot) => bot.bot_uuid.as_str(),
            _ => unreachable!(),
        };
        let session_key = session_key(
            command.session_id.as_deref(),
            &run_id,
            command.client.as_deref(),
            bot_id,
        )?;
        let accepted = self
            .runs
            .start_async_chat(AsyncA2aChatCommand {
                caller,
                target_bot_id: target,
                message: command.message,
                run_channel_from: command.from.clone(),
                from_actor_id: Some(command.from.unwrap_or_else(|| "user".into())),
                authenticated_staff_id: None,
                run_id,
                session_key,
                timeout_ms: command
                    .timeout_ms
                    .unwrap_or(self.run_timeout_ms)
                    .min(86_400_000),
                client: command.client,
                tags: command
                    .tags
                    .into_iter()
                    .map(|v| v.trim().to_string())
                    .filter(|v| !v.is_empty())
                    .collect(),
                response_mode: command.response_mode,
                caller_wait_mode: normalized(command.caller_wait_mode),
                organization_code: normalized(command.organization_code),
                provider_bypass_headers: command.provider_bypass_headers,
            })
            .await
            .map_err(|error| match error {
                ServiceError::Unauthorized(_) => {
                    ApplicationError::forbidden("Chat admission failed")
                }
                other => failure(other),
            })?;
        Ok(
            json!({"provider_name":command.provider_name,"provider_bot_ref":command.provider_bot_ref,
            "run_id":accepted.run_id,"session_id":accepted.session_id,"status":accepted.status,"expires_at_ms":accepted.expires_at_ms}),
        )
    }
    async fn get_run(&self, command: QueryA2aRun) -> Result<Value, ApplicationError> {
        let caller = self
            .caller(&command.token, command.actor.as_deref())
            .await?;
        let status = self
            .runs
            .get_run(ChatRunQueryCommand {
                caller,
                run_id: command.run_id,
                wait_ms: command.wait_ms.min(self.poll_wait_max_ms),
                since_version: command.since_version,
            })
            .await
            .map_err(failure)?;
        Ok(run_view(status, false))
    }
    async fn cancel_run(
        &self,
        token: &str,
        actor: Option<&str>,
        run_id: &str,
    ) -> Result<Value, ApplicationError> {
        let caller = self.caller(token, actor).await?;
        let status = self
            .runs
            .cancel_run(ChatRunCancelCommand {
                caller,
                run_id: run_id.into(),
            })
            .await
            .map_err(failure)?;
        Ok(run_view(status, true))
    }
}
fn session_key(
    session: Option<&str>,
    run: &str,
    client: Option<&str>,
    bot: &str,
) -> Result<String, ApplicationError> {
    if let Some(session) = session {
        let session = session.trim();
        if session.is_empty()
            || session.len() > 128
            || !session
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | ':' | '.'))
        {
            return Err(ApplicationError::invalid(
                "invalid_session_id",
                "Invalid session_id",
            ));
        }
        return Ok(session.into());
    }
    Ok(
        if client.is_some_and(|client| client.starts_with("bcs-cli")) {
            format!("bcs-cli:{bot}:{}", &run[..8])
        } else {
            format!("chat:{}", &run[..8])
        },
    )
}
fn run_view(status: A2aRunStatus, cancel: bool) -> Value {
    let details = status.response.unwrap_or_else(|| json!({}));
    let state = details
        .get("state")
        .and_then(Value::as_str)
        .unwrap_or(&status.status);
    let mut view = json!({"run_id":status.run_id,"state":state,
        "response":{"content":details.get("content").and_then(Value::as_str).unwrap_or("")},
        "error_message":details.get("error_message").filter(|v|!v.is_null()).map(|_|"Run failed; provide run_id to the platform operator"),
        "version":details.get("version"),"content_truncated":details.get("content_truncated").and_then(Value::as_bool).unwrap_or(false)});
    if cancel {
        view["cancelled"] = json!(
            details
                .get("cancelled")
                .and_then(Value::as_bool)
                .unwrap_or(state == "cancelled")
        );
    } else {
        for key in [
            "session_id",
            "created_at_ms",
            "updated_at_ms",
            "completed_at_ms",
            "expires_at_ms",
        ] {
            view[key] = details.get(key).cloned().unwrap_or(Value::Null);
        }
        view["is_terminal"] = json!(
            details
                .get("is_terminal")
                .and_then(Value::as_bool)
                .unwrap_or(matches!(state, "completed" | "failed" | "cancelled"))
        );
    }
    view
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::types::a2a_bot::a2a_management::ManagementError;

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

    #[test]
    fn run_projection_handles_absent_snapshots_and_preserves_cancel_state() {
        let view = run_view(
            A2aRunStatus {
                run_id: "run".into(),
                status: "running".into(),
                response: None,
            },
            false,
        );
        assert_eq!(view["state"], "running");
        assert_eq!(view["is_terminal"], false);
        assert_eq!(view["response"]["content"], "");
        assert_eq!(view["error_message"], Value::Null);
        let view = run_view(
            A2aRunStatus {
                run_id: "run".into(),
                status: "cancelled".into(),
                response: Some(json!({"cancelled":true})),
            },
            true,
        );
        assert_eq!(view["cancelled"], true);
        assert!(
            session_key(None, "123456789", None, "caller")
                .unwrap()
                .starts_with("chat:")
        );
    }
}
