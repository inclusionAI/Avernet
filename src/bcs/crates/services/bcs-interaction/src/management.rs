use std::{
    collections::HashSet,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};

use async_trait::async_trait;
use bcs_service_api::{
    CanResolveInteraction, CanResolveInteractionCommand, InteractionFrontendEvent,
    InteractionFrontendPort, InteractionInsertResult, InteractionKey, InteractionProviderCommand,
    InteractionProviderPort, InteractionRecord, InteractionRequestedOutcome,
    InteractionResolveClaim, InteractionResolveCommit, InteractionService, InteractionServiceError,
    InteractionStatus, InteractionStorePort, ProviderInteractionRequestedCommand,
    ProviderInteractionResolvedCommand, ResolveInteractionCommand, ResolveInteractionResult,
    ServiceError, ServiceResult,
};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tracing::{info, warn};

const MAX_INTERACTION_PAYLOAD_BYTES: usize = 256 * 1024;

pub struct InteractionManagement {
    store: Arc<dyn InteractionStorePort>,
    authorization: Arc<dyn CanResolveInteraction>,
    provider: Arc<dyn InteractionProviderPort>,
    frontend: Arc<dyn InteractionFrontendPort>,
    terminal_retention_ms: u64,
}

impl InteractionManagement {
    pub fn new(
        store: Arc<dyn InteractionStorePort>,
        authorization: Arc<dyn CanResolveInteraction>,
        provider: Arc<dyn InteractionProviderPort>,
        frontend: Arc<dyn InteractionFrontendPort>,
        terminal_retention_ms: u64,
    ) -> Self {
        Self {
            store,
            authorization,
            provider,
            frontend,
            terminal_retention_ms,
        }
    }

    async fn cleanup_lazily(&self, now_ms: u64) {
        let terminal_before_ms = now_ms.saturating_sub(self.terminal_retention_ms);
        if let Err(error) = self.store.cleanup_terminal(terminal_before_ms).await {
            warn!(%error, "interaction terminal cleanup failed");
        }
    }

    async fn publish(&self, event: &InteractionFrontendEvent) -> ServiceResult<()> {
        self.frontend.publish_interaction(event.clone()).await
    }

    fn frontend_event(record: &InteractionRecord, payload: Value) -> InteractionFrontendEvent {
        InteractionFrontendEvent {
            bcs_run_id: record.key.bcs_run_id.clone(),
            bcs_session_id: record.bcs_session_id.clone(),
            group_id: record.group_id.clone(),
            bot_id: record.bot_id.clone(),
            payload,
        }
    }
}

#[async_trait]
impl InteractionService for InteractionManagement {
    async fn on_provider_requested(
        &self,
        command: ProviderInteractionRequestedCommand,
    ) -> ServiceResult<InteractionRequestedOutcome> {
        self.cleanup_lazily(command.received_at_ms).await;
        if command.bcs_session_id.trim().is_empty()
            || command.bcs_run_id.trim().is_empty()
            || command.interaction_id.trim().is_empty()
        {
            return Err(ServiceError::InvalidOperation {
                message: "interaction requested requires BCS run, session, and interaction IDs"
                    .to_string(),
                request_id: Some(command.bcs_run_id),
            });
        }
        if !command.provider_target.is_http_provider() {
            return Err(ServiceError::InvalidOperation {
                message: "interaction requested requires an HTTP Provider target".to_string(),
                request_id: Some(command.bcs_run_id),
            });
        }
        let payload_bytes = serde_json::to_vec(&command.payload)
            .map_err(|error| ServiceError::InvalidOperation {
                message: format!("interaction payload cannot be serialized: {error}"),
                request_id: Some(command.bcs_run_id.clone()),
            })?
            .len();
        if payload_bytes > MAX_INTERACTION_PAYLOAD_BYTES {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "interaction payload exceeds {MAX_INTERACTION_PAYLOAD_BYTES} bytes"
                ),
                request_id: Some(command.bcs_run_id.clone()),
            });
        }
        let mut payload = command.payload;
        normalize_requested_payload(
            command.kind,
            &mut payload,
            &command.bcs_run_id,
            &command.interaction_id,
        );
        validate_requested_payload(command.kind, &payload).map_err(|message| {
            ServiceError::InvalidOperation {
                message,
                request_id: Some(command.bcs_run_id.clone()),
            }
        })?;

        let record = InteractionRecord {
            key: InteractionKey {
                bcs_run_id: command.bcs_run_id,
                interaction_id: command.interaction_id,
            },
            provider_run_id: command.provider_run_id,
            kind: command.kind,
            bcs_session_id: command.bcs_session_id,
            group_id: command.group_id,
            bot_id: command.bot_id,
            run_deadline_ms: command.run_deadline_ms,
            provider_target: command.provider_target,
            provider_bypass_headers: command.provider_bypass_headers,
            requested_payload: payload,
            status: InteractionStatus::Pending,
            in_flight: false,
            accepted_idempotency_key: None,
            accepted_resolution_fingerprint: None,
            resolved_by_actor_id: None,
            requested_at_ms: command.received_at_ms,
            accepted_at_ms: None,
            terminal_at_ms: None,
            invalidation_reason: None,
        };
        let inserted = self.store.insert_requested(record.clone()).await?;
        match inserted {
            InteractionInsertResult::Stored => {
                self.publish(&Self::frontend_event(
                    &record,
                    record.requested_payload.clone(),
                ))
                .await?;
                info!(
                    bcs_run_id = %record.key.bcs_run_id,
                    interaction_id = %record.key.interaction_id,
                    bcs_session_id = %record.bcs_session_id,
                    "interaction requested"
                );
                Ok(InteractionRequestedOutcome::Stored)
            }
            InteractionInsertResult::IdenticalDuplicate => {
                Ok(InteractionRequestedOutcome::Duplicate)
            }
            InteractionInsertResult::ConflictingDuplicate => {
                warn!(
                    bcs_run_id = %record.key.bcs_run_id,
                    interaction_id = %record.key.interaction_id,
                    "Provider repeated interaction ID with a conflicting payload; preserving first"
                );
                Ok(InteractionRequestedOutcome::ConflictPreserved)
            }
            InteractionInsertResult::TerminalPreserved => {
                Ok(InteractionRequestedOutcome::TerminalPreserved)
            }
            InteractionInsertResult::CapacityExceeded => {
                warn!(
                    bcs_run_id = %record.key.bcs_run_id,
                    interaction_id = %record.key.interaction_id,
                    bcs_session_id = %record.bcs_session_id,
                    "interaction rejected because active capacity is exhausted"
                );
                Ok(InteractionRequestedOutcome::CapacityRejected)
            }
        }
    }

    async fn on_provider_resolved(
        &self,
        command: ProviderInteractionResolvedCommand,
    ) -> ServiceResult<()> {
        self.cleanup_lazily(command.received_at_ms).await;
        let key = InteractionKey {
            bcs_run_id: command.bcs_run_id,
            interaction_id: command.interaction_id,
        };
        let Some(existing) = self.store.get(&key).await? else {
            warn!(
                bcs_run_id = %key.bcs_run_id,
                interaction_id = %key.interaction_id,
                "resolved interaction has no local requested record"
            );
            return Ok(());
        };
        if existing.provider_run_id != command.provider_run_id || existing.kind != command.kind {
            warn!(
                bcs_run_id = %key.bcs_run_id,
                interaction_id = %key.interaction_id,
                "resolved interaction does not match its requested record"
            );
            return Ok(());
        }
        if !existing.status.is_active() {
            return Ok(());
        }
        validate_resolved_payload(&existing, &command.payload).map_err(|message| {
            ServiceError::InvalidOperation {
                message,
                request_id: Some(existing.key.bcs_run_id.clone()),
            }
        })?;
        let Some(resolved) = self
            .store
            .mark_resolved(&key, command.received_at_ms)
            .await?
        else {
            return Ok(());
        };
        if resolved.status == InteractionStatus::Resolved {
            self.publish(&Self::frontend_event(&resolved, command.payload))
                .await?;
            info!(
                bcs_run_id = %key.bcs_run_id,
                interaction_id = %key.interaction_id,
                "interaction runtime resolved"
            );
        }
        Ok(())
    }

    async fn resolve(
        &self,
        command: ResolveInteractionCommand,
    ) -> Result<ResolveInteractionResult, InteractionServiceError> {
        let now_ms = now_ms();
        self.cleanup_lazily(now_ms).await;
        if command.bcs_run_id.trim().is_empty()
            || command.interaction_id.trim().is_empty()
            || command.idempotency_key.trim().is_empty()
            || command.resolver_actor_id.trim().is_empty()
            || !command.resolution.is_object()
        {
            return Err(InteractionServiceError::InvalidRequest(
                "bcsRunId, interactionId, idempotencyKey, authenticated resolver, and object resolution are required"
                    .to_string(),
            ));
        }
        let key = InteractionKey {
            bcs_run_id: command.bcs_run_id.clone(),
            interaction_id: command.interaction_id.clone(),
        };
        let record = self
            .store
            .get(&key)
            .await
            .map_err(internal_error)?
            .ok_or(InteractionServiceError::NotFound)?;

        if command
            .expected_bcs_session_id
            .as_deref()
            .is_some_and(|expected| expected != record.bcs_session_id)
            || command
                .expected_group_id
                .as_deref()
                .is_some_and(|expected| expected != record.group_id)
        {
            return Err(InteractionServiceError::Unauthorized);
        }

        if now_ms > record.run_deadline_ms && record.status.is_active() {
            self.store
                .invalidate_run(&record.key.bcs_run_id, "run_deadline", now_ms)
                .await
                .map_err(internal_error)?;
            return Err(resolve_failed(
                "The interaction run has expired",
                false,
                InteractionStatus::Invalidated,
            ));
        }

        let allowed = self
            .authorization
            .can_resolve(CanResolveInteractionCommand {
                actor_id: command.resolver_actor_id.clone(),
                bcs_session_id: record.bcs_session_id.clone(),
                group_id: record.group_id.clone(),
            })
            .await
            .map_err(internal_error)?;
        if !allowed {
            return Err(InteractionServiceError::Unauthorized);
        }

        validate_resolution(&record, &command.resolution)
            .map_err(InteractionServiceError::InvalidRequest)?;

        // 前端 resolve 只发 values；BCS 用 requested 存储的原始 question 文本
        // 按 questionId 补进每个 answer，fingerprint 与 Provider 转发均用补齐后的版本。
        let resolution = augment_ask_user_resolution(&record, command.resolution);

        let fingerprint = resolution_fingerprint(record.kind, &resolution);
        match self
            .store
            .claim_resolution(&key, &command.idempotency_key, &fingerprint)
            .await
            .map_err(internal_error)?
        {
            InteractionResolveClaim::NotFound => Err(InteractionServiceError::NotFound),
            InteractionResolveClaim::InFlight(status) => Err(resolve_failed(
                "Another resolution request is currently being delivered",
                true,
                status,
            )),
            InteractionResolveClaim::AlreadyAccepted(existing) => Ok(ResolveInteractionResult {
                accepted: true,
                interaction_id: existing.key.interaction_id,
                status: existing.status,
                idempotency_key: command.idempotency_key,
            }),
            InteractionResolveClaim::AcceptedDifferent(existing)
            | InteractionResolveClaim::Terminal(existing) => Err(resolve_failed(
                "The interaction no longer accepts a different resolution",
                false,
                existing.status,
            )),
            InteractionResolveClaim::Acquired(claimed) => {
                let provider_result = self
                    .provider
                    .resolve_interaction(InteractionProviderCommand {
                        target: claimed.provider_target.clone(),
                        provider_bypass_headers: claimed.provider_bypass_headers.clone(),
                        bcs_run_id: claimed.key.bcs_run_id.clone(),
                        provider_run_id: claimed.provider_run_id.clone(),
                        bcs_session_id: claimed.bcs_session_id.clone(),
                        group_id: claimed.group_id.clone(),
                        bot_id: claimed.bot_id.clone(),
                        interaction_id: claimed.key.interaction_id.clone(),
                        kind: claimed.kind,
                        idempotency_key: command.idempotency_key.clone(),
                        resolution,
                    })
                    .await;

                let ack = match provider_result {
                    Ok(ack) => ack,
                    Err(error) => {
                        let updated = self
                            .store
                            .finish_resolution(&key, InteractionResolveCommit::RetryableFailure)
                            .await
                            .map_err(internal_error)?
                            .ok_or(InteractionServiceError::NotFound)?;
                        warn!(
                            bcs_run_id = %key.bcs_run_id,
                            interaction_id = %key.interaction_id,
                            resolver_actor_id = %command.resolver_actor_id,
                            status = ?updated.status,
                            retryable = updated.status == InteractionStatus::Pending,
                            %error,
                            "interaction resolve Provider transport failed"
                        );
                        return resolution_after_provider_result(
                            updated,
                            &command.idempotency_key,
                            &fingerprint,
                            "Failed to deliver the resolution to Provider",
                            true,
                        );
                    }
                };

                if ack.ok {
                    let updated = self
                        .store
                        .finish_resolution(
                            &key,
                            InteractionResolveCommit::Accepted {
                                idempotency_key: command.idempotency_key.clone(),
                                resolution_fingerprint: fingerprint,
                                resolver_actor_id: command.resolver_actor_id.clone(),
                                accepted_at_ms: now_ms,
                            },
                        )
                        .await
                        .map_err(internal_error)?
                        .ok_or(InteractionServiceError::NotFound)?;
                    if !matches!(
                        updated.status,
                        InteractionStatus::Accepted | InteractionStatus::Resolved
                    ) {
                        return Err(resolve_failed(
                            "The interaction became unavailable while Provider accepted it",
                            updated.status == InteractionStatus::Pending,
                            updated.status,
                        ));
                    }
                    info!(
                        bcs_run_id = %key.bcs_run_id,
                        interaction_id = %key.interaction_id,
                        resolver_actor_id = %command.resolver_actor_id,
                        status = ?updated.status,
                        "interaction resolution accepted by Provider"
                    );
                    return Ok(ResolveInteractionResult {
                        accepted: true,
                        interaction_id: key.interaction_id,
                        status: updated.status,
                        idempotency_key: command.idempotency_key,
                    });
                }

                let retryable = ack.retryable.unwrap_or(true);
                let message = ack
                    .error
                    .unwrap_or_else(|| "Provider rejected the interaction resolution".to_string());
                if retryable {
                    let updated = self
                        .store
                        .finish_resolution(&key, InteractionResolveCommit::RetryableFailure)
                        .await
                        .map_err(internal_error)?
                        .ok_or(InteractionServiceError::NotFound)?;
                    warn!(
                        bcs_run_id = %key.bcs_run_id,
                        interaction_id = %key.interaction_id,
                        resolver_actor_id = %command.resolver_actor_id,
                        status = ?updated.status,
                        retryable = updated.status == InteractionStatus::Pending,
                        "Provider did not accept interaction resolution"
                    );
                    resolution_after_provider_result(
                        updated,
                        &command.idempotency_key,
                        &fingerprint,
                        &message,
                        true,
                    )
                } else {
                    let updated = self
                        .store
                        .finish_resolution(
                            &key,
                            InteractionResolveCommit::Invalidated {
                                resolver_actor_id: command.resolver_actor_id.clone(),
                                reason: "provider_non_retryable".to_string(),
                                invalidated_at_ms: now_ms,
                            },
                        )
                        .await
                        .map_err(internal_error)?
                        .ok_or(InteractionServiceError::NotFound)?;
                    warn!(
                        bcs_run_id = %key.bcs_run_id,
                        interaction_id = %key.interaction_id,
                        resolver_actor_id = %command.resolver_actor_id,
                        status = ?updated.status,
                        retryable = false,
                        "Provider rejected interaction resolution"
                    );
                    resolution_after_provider_result(
                        updated,
                        &command.idempotency_key,
                        &fingerprint,
                        &message,
                        false,
                    )
                }
            }
        }
    }

    async fn list_pending(
        &self,
        bcs_session_id: &str,
    ) -> ServiceResult<Vec<InteractionFrontendEvent>> {
        self.cleanup_lazily(now_ms()).await;
        Ok(self
            .store
            .list_pending(bcs_session_id)
            .await?
            .into_iter()
            .map(|record| {
                let payload = record.requested_payload.clone();
                Self::frontend_event(&record, payload)
            })
            .collect())
    }

    async fn invalidate_run(
        &self,
        bcs_run_id: &str,
        reason: &str,
        invalidated_at_ms: u64,
    ) -> ServiceResult<usize> {
        let invalidated = self
            .store
            .invalidate_run(bcs_run_id, reason, invalidated_at_ms)
            .await?;
        if !invalidated.is_empty() {
            info!(
                bcs_run_id,
                count = invalidated.len(),
                reason,
                "active interactions invalidated with run"
            );
        }
        Ok(invalidated.len())
    }

    async fn cleanup_terminal(&self, terminal_before_ms: u64) -> ServiceResult<usize> {
        self.store.cleanup_terminal(terminal_before_ms).await
    }
}

fn internal_error(error: ServiceError) -> InteractionServiceError {
    InteractionServiceError::Internal(error.to_string())
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn validate_requested_payload(
    kind: bcs_service_api::InteractionKind,
    payload: &Value,
) -> Result<(), String> {
    match kind {
        bcs_service_api::InteractionKind::Exec => {
            require_non_empty_string(payload, "command")?;
            validate_decision_options(payload.get("options"), true)
        }
        bcs_service_api::InteractionKind::ModeSwitch => {
            validate_decision_options(payload.get("options"), true)
        }
        bcs_service_api::InteractionKind::AskUser => validate_questions(payload),
    }
}

fn normalize_requested_payload(
    kind: bcs_service_api::InteractionKind,
    payload: &mut Value,
    bcs_run_id: &str,
    interaction_id: &str,
) {
    if kind != bcs_service_api::InteractionKind::AskUser {
        return;
    }
    let Some(questions) = payload.get_mut("questions").and_then(Value::as_array_mut) else {
        return;
    };
    for (question_index, question) in questions.iter_mut().enumerate() {
        let Some(question) = question.as_object_mut() else {
            continue;
        };
        if question
            .get("allowOther")
            .is_some_and(|allow_other| !allow_other.is_boolean())
        {
            question.remove("allowOther");
            warn!(
                %bcs_run_id,
                %interaction_id,
                question_index,
                "ask_user allowOther is not boolean; treating it as omitted"
            );
        }
    }
}

fn validate_decision_options(options: Option<&Value>, required: bool) -> Result<(), String> {
    let Some(options) = options.and_then(Value::as_array) else {
        return if required {
            Err("interaction options must be a non-empty array".to_string())
        } else {
            Ok(())
        };
    };
    if options.is_empty() {
        return Err("interaction options must be a non-empty array".to_string());
    }
    let mut decisions = HashSet::new();
    for option in options {
        let decision = require_non_empty_string(option, "decision")?;
        require_non_empty_string(option, "label")?;
        if !decisions.insert(decision) {
            return Err("interaction option decisions must be unique".to_string());
        }
    }
    Ok(())
}

fn validate_questions(payload: &Value) -> Result<(), String> {
    if payload.get("options").is_some() {
        return Err("ask_user does not support top-level options".to_string());
    }
    let Some(questions) = payload.get("questions").and_then(Value::as_array) else {
        return Err("ask_user questions must be an array".to_string());
    };
    if questions.is_empty() || questions.len() > 4 {
        return Err("ask_user requires between one and four questions".to_string());
    }
    let mut question_ids = HashSet::new();
    for question in questions {
        let question_id = require_non_empty_string(question, "questionId")?;
        require_non_empty_string(question, "question")?;
        if !question_ids.insert(question_id) {
            return Err("ask_user questionId values must be unique".to_string());
        }
        if question.get("secret").is_some() || question.get("isSecret").is_some() {
            return Err("ask_user secret is not supported in this protocol version".to_string());
        }
        let options = question.get("options");
        if let Some(options) = options {
            let Some(options) = options.as_array() else {
                return Err("ask_user question options must be an array".to_string());
            };
            if options.is_empty() || options.len() > 4 {
                return Err("ask_user question options must contain one to four values".to_string());
            }
            let mut values = HashSet::new();
            for option in options {
                let value = require_non_empty_string(option, "value")?;
                require_non_empty_string(option, "label")?;
                if !values.insert(value) {
                    return Err("ask_user option values must be unique per question".to_string());
                }
            }
        } else if question.get("allowOther").is_some() {
            return Err("allowOther requires question options".to_string());
        }
    }
    Ok(())
}

fn validate_resolution(record: &InteractionRecord, resolution: &Value) -> Result<(), String> {
    match record.kind {
        bcs_service_api::InteractionKind::Exec | bcs_service_api::InteractionKind::ModeSwitch => {
            let decision = require_non_empty_string(resolution, "decision")?;
            let offered = record
                .requested_payload
                .get("options")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|option| option.get("decision").and_then(Value::as_str))
                .any(|offered| offered == decision);
            if !offered {
                return Err("interaction decision was not offered by Provider".to_string());
            }
            Ok(())
        }
        bcs_service_api::InteractionKind::AskUser => {
            validate_ask_user_resolution(record, resolution)
        }
    }
}

fn validate_resolved_payload(record: &InteractionRecord, payload: &Value) -> Result<(), String> {
    match record.kind {
        bcs_service_api::InteractionKind::Exec | bcs_service_api::InteractionKind::ModeSwitch => {
            validate_resolution(record, payload)
        }
        bcs_service_api::InteractionKind::AskUser => {
            if let Some(action) = payload.get("action") {
                match action.as_str() {
                    Some("submit" | "cancel") => {}
                    _ => {
                        return Err(
                            "resolved ask_user action must be submit or cancel when present"
                                .to_string(),
                        );
                    }
                }
            }
            if payload
                .get("answers")
                .is_some_and(|answers| !answers.is_object())
            {
                return Err("resolved ask_user answers must be an object when present".to_string());
            }
            Ok(())
        }
    }
}

fn validate_ask_user_resolution(
    record: &InteractionRecord,
    resolution: &Value,
) -> Result<(), String> {
    let action = require_non_empty_string(resolution, "action")?;
    if action == "cancel" {
        return Ok(());
    }
    if action != "submit" {
        return Err("ask_user action must be submit or cancel".to_string());
    }
    let answers = resolution
        .get("answers")
        .and_then(Value::as_object)
        .ok_or_else(|| "ask_user submit requires answers".to_string())?;
    let questions = record
        .requested_payload
        .get("questions")
        .and_then(Value::as_array)
        .ok_or_else(|| "stored ask_user questions are invalid".to_string())?;
    if answers.len() != questions.len() {
        return Err("ask_user submit must answer every question exactly once".to_string());
    }
    for question in questions {
        let question_id = require_non_empty_string(question, "questionId")?;
        let answer = answers
            .get(question_id)
            .ok_or_else(|| format!("ask_user answer missing for question {question_id}"))?;
        let values = answer
            .get("values")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("ask_user answer {question_id} requires values"))?;
        if values.iter().any(|value| !value.is_string()) {
            return Err(format!(
                "ask_user answer {question_id} values must be strings"
            ));
        }
        let multi_select = question
            .get("multiSelect")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        if !multi_select && values.len() > 1 {
            return Err(format!(
                "ask_user answer {question_id} accepts at most one value"
            ));
        }
    }
    Ok(())
}

/// AskUser submit 时，按 questionId（answers 对象的键）把 requested 阶段存储的
/// 原始 question 和可选 header 补进每个 answer 对象，与 `values` 平级。
/// 前端 resolve 只发 `values`；question/header 始终由 BCS 用权威存储值覆盖，存储
/// header 缺失时保持缺失，不从 questionId 或前端输入合成。canonical resolution
/// 同时用于 fingerprint 与 Provider 转发。cancel / exec / mode_switch 原样返回。
fn augment_ask_user_resolution(record: &InteractionRecord, mut resolution: Value) -> Value {
    if record.kind != bcs_service_api::InteractionKind::AskUser {
        return resolution;
    }
    if resolution.get("action").and_then(Value::as_str) != Some("submit") {
        return resolution;
    }
    let Some(questions) = record
        .requested_payload
        .get("questions")
        .and_then(Value::as_array)
    else {
        return resolution;
    };
    let Some(answers) = resolution
        .get_mut("answers")
        .and_then(Value::as_object_mut)
    else {
        return resolution;
    };
    for question in questions {
        let Some(question_id) = question.get("questionId").and_then(Value::as_str) else {
            continue;
        };
        let Some(question_text) = question.get("question").and_then(Value::as_str) else {
            continue;
        };
        if let Some(answer) = answers.get_mut(question_id).and_then(Value::as_object_mut) {
            answer.remove("question");
            answer.remove("header");
            answer.insert(
                "question".to_string(),
                Value::String(question_text.to_string()),
            );
            if let Some(header) = question
                .get("header")
                .and_then(Value::as_str)
                .filter(|header| !header.trim().is_empty())
            {
                answer.insert("header".to_string(), Value::String(header.to_string()));
            }
        }
    }
    resolution
}

fn require_non_empty_string<'a>(value: &'a Value, field: &str) -> Result<&'a str, String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| format!("interaction field {field} must be a non-empty string"))
}

fn resolve_failed(
    message: &str,
    retryable: bool,
    status: InteractionStatus,
) -> InteractionServiceError {
    InteractionServiceError::ResolveFailed {
        message: message.to_string(),
        retryable,
        status,
    }
}

fn resolution_after_provider_result(
    record: InteractionRecord,
    idempotency_key: &str,
    resolution_fingerprint: &str,
    failure_message: &str,
    pending_retryable: bool,
) -> Result<ResolveInteractionResult, InteractionServiceError> {
    let accepted = record.status == InteractionStatus::Resolved
        || (record.status == InteractionStatus::Accepted
            && record.accepted_idempotency_key.as_deref() == Some(idempotency_key)
            && record.accepted_resolution_fingerprint.as_deref() == Some(resolution_fingerprint));
    if accepted {
        return Ok(ResolveInteractionResult {
            accepted: true,
            interaction_id: record.key.interaction_id,
            status: record.status,
            idempotency_key: idempotency_key.to_string(),
        });
    }
    Err(resolve_failed(
        failure_message,
        pending_retryable && record.status == InteractionStatus::Pending,
        record.status,
    ))
}

fn resolution_fingerprint(kind: bcs_service_api::InteractionKind, resolution: &Value) -> String {
    let canonical = canonical_json(resolution);
    let mut hash = Sha256::new();
    hash.update(format!("{kind:?}:"));
    hash.update(canonical.as_bytes());
    format!("{:x}", hash.finalize())
}

fn canonical_json(value: &Value) -> String {
    match value {
        Value::Object(map) => {
            let mut fields = map.iter().collect::<Vec<_>>();
            fields.sort_by(|(left, _), (right, _)| left.cmp(right));
            let body = fields
                .into_iter()
                .map(|(key, value)| {
                    format!(
                        "{}:{}",
                        serde_json::to_string(key).unwrap_or_default(),
                        canonical_json(value)
                    )
                })
                .collect::<Vec<_>>()
                .join(",");
            format!("{{{body}}}")
        }
        Value::Array(values) => format!(
            "[{}]",
            values
                .iter()
                .map(canonical_json)
                .collect::<Vec<_>>()
                .join(",")
        ),
        _ => serde_json::to_string(value).unwrap_or_default(),
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use async_trait::async_trait;
    use bcs_domain::{BotDeliveryTarget, RedactedToken};
    use bcs_service_api::{
        CanResolveInteraction, CanResolveInteractionCommand, InteractionFrontendEvent,
        InteractionFrontendPort, InteractionKey, InteractionKind, InteractionProviderAck,
        InteractionProviderCommand, InteractionProviderPort, InteractionService,
        InteractionServiceError, InteractionStatus, InteractionStorePort,
        ProviderInteractionRequestedCommand, ProviderInteractionResolvedCommand,
        ResolveInteractionCommand, ServiceError, ServiceResult,
    };
    use serde_json::json;
    use tokio::sync::{Mutex, Notify};

    use super::InteractionManagement;
    use crate::MemoryInteractionStore;

    struct FixedAuthorization(bool);

    #[async_trait]
    impl CanResolveInteraction for FixedAuthorization {
        async fn can_resolve(&self, _command: CanResolveInteractionCommand) -> ServiceResult<bool> {
            Ok(self.0)
        }
    }

    #[derive(Default)]
    struct RecordingProvider {
        calls: Mutex<Vec<InteractionProviderCommand>>,
        response: Mutex<Option<ServiceResult<InteractionProviderAck>>>,
    }

    struct ResolvingBeforeAckProvider {
        store: Arc<MemoryInteractionStore>,
        terminal: InteractionStatus,
        response: Mutex<Option<ServiceResult<InteractionProviderAck>>>,
    }

    #[derive(Default)]
    struct BlockingProvider {
        calls: Mutex<Vec<InteractionProviderCommand>>,
        entered: Notify,
        release: Notify,
    }

    #[async_trait]
    impl InteractionProviderPort for BlockingProvider {
        async fn resolve_interaction(
            &self,
            command: InteractionProviderCommand,
        ) -> ServiceResult<InteractionProviderAck> {
            self.calls.lock().await.push(command);
            self.entered.notify_one();
            self.release.notified().await;
            Ok(InteractionProviderAck {
                ok: true,
                retryable: None,
                error: None,
            })
        }
    }

    #[async_trait]
    impl InteractionProviderPort for ResolvingBeforeAckProvider {
        async fn resolve_interaction(
            &self,
            command: InteractionProviderCommand,
        ) -> ServiceResult<InteractionProviderAck> {
            let key = InteractionKey {
                bcs_run_id: command.bcs_run_id,
                interaction_id: command.interaction_id,
            };
            match self.terminal {
                InteractionStatus::Resolved => {
                    self.store.mark_resolved(&key, super::now_ms()).await?;
                }
                InteractionStatus::Invalidated => {
                    self.store
                        .invalidate_run(&key.bcs_run_id, "run_terminal", super::now_ms())
                        .await?;
                }
                _ => {}
            }
            self.response.lock().await.take().unwrap_or_else(|| {
                Ok(InteractionProviderAck {
                    ok: true,
                    retryable: None,
                    error: None,
                })
            })
        }
    }

    #[async_trait]
    impl InteractionProviderPort for RecordingProvider {
        async fn resolve_interaction(
            &self,
            command: InteractionProviderCommand,
        ) -> ServiceResult<InteractionProviderAck> {
            self.calls.lock().await.push(command);
            self.response.lock().await.take().unwrap_or_else(|| {
                Ok(InteractionProviderAck {
                    ok: true,
                    retryable: None,
                    error: None,
                })
            })
        }
    }

    #[derive(Default)]
    struct RecordingFrontend {
        calls: Mutex<Vec<InteractionFrontendEvent>>,
    }

    #[async_trait]
    impl InteractionFrontendPort for RecordingFrontend {
        async fn publish_interaction(&self, event: InteractionFrontendEvent) -> ServiceResult<()> {
            self.calls.lock().await.push(event);
            Ok(())
        }
    }

    fn target() -> BotDeliveryTarget {
        BotDeliveryTarget::HttpProvider {
            bot_id: "bot-1".to_string(),
            provider_id: "provider-1".to_string(),
            provider_bot_ref: "ref-1".to_string(),
            webhook_url: "https://provider.example/webhook".to_string(),
            bcs_to_provider_token: RedactedToken::new("secret"),
            protocol_version: "2.0".to_string(),
        }
    }

    fn requested(interaction_id: &str) -> ProviderInteractionRequestedCommand {
        ProviderInteractionRequestedCommand {
            bcs_run_id: "bcs-run-1".to_string(),
            provider_run_id: "provider-run-1".to_string(),
            interaction_id: interaction_id.to_string(),
            kind: InteractionKind::Exec,
            bcs_session_id: "session-1".to_string(),
            group_id: "group-1".to_string(),
            bot_id: "bot-1".to_string(),
            run_deadline_ms: u64::MAX,
            provider_target: target(),
            provider_bypass_headers: vec![("x-trace".to_string(), "trace-1".to_string())],
            payload: json!({
                "runId":"provider-run-1",
                "seq":7,
                "phase":"requested",
                "interactionId":interaction_id,
                "kind":"exec",
                "command":"deploy",
                "options":[
                    {"decision":"allow_once","label":"Allow once"},
                    {"decision":"deny","label":"Deny"}
                ]
            }),
            received_at_ms: 100,
        }
    }

    fn resolve(interaction_id: &str, idempotency_key: &str) -> ResolveInteractionCommand {
        ResolveInteractionCommand {
            bcs_run_id: "bcs-run-1".to_string(),
            interaction_id: interaction_id.to_string(),
            idempotency_key: idempotency_key.to_string(),
            resolver_actor_id: "human_1".to_string(),
            expected_bcs_session_id: None,
            expected_group_id: None,
            resolution: json!({"decision":"allow_once"}),
        }
    }

    fn service(
        authorized: bool,
    ) -> (
        InteractionManagement,
        Arc<MemoryInteractionStore>,
        Arc<RecordingProvider>,
        Arc<RecordingFrontend>,
    ) {
        let store = Arc::new(MemoryInteractionStore::new());
        let provider = Arc::new(RecordingProvider::default());
        let frontend = Arc::new(RecordingFrontend::default());
        let service = InteractionManagement::new(
            store.clone(),
            Arc::new(FixedAuthorization(authorized)),
            provider.clone(),
            frontend.clone(),
            120_000,
        );
        (service, store, provider, frontend)
    }

    #[tokio::test]
    async fn requested_is_stored_before_one_frontend_publication_and_replayable() {
        let (service, _store, _provider, frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let calls = frontend.calls.lock().await;
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].bcs_run_id, "bcs-run-1");
        assert_eq!(calls[0].bcs_session_id, "session-1");
        assert_eq!(calls[0].payload["interactionId"], "interaction-1");
        drop(calls);

        let replay = service.list_pending("session-1").await.unwrap();
        assert_eq!(replay.len(), 1);
        assert_eq!(replay[0].payload["interactionId"], "interaction-1");
    }

    #[tokio::test]
    async fn resolve_rechecks_authorization_and_never_calls_provider_when_denied() {
        let (service, _store, provider, _frontend) = service(false);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let error = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .unwrap_err();
        assert_eq!(error, InteractionServiceError::Unauthorized);
        assert!(provider.calls.lock().await.is_empty());
    }

    #[tokio::test]
    async fn resolve_enforces_session_bound_connection_scope_before_provider_call() {
        let (service, _store, provider, _frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();
        let mut command = resolve("interaction-1", "idem-1");
        command.expected_bcs_session_id = Some("session-other".to_string());
        command.expected_group_id = Some("group-1".to_string());

        let error = service.resolve(command).await.unwrap_err();

        assert_eq!(error, InteractionServiceError::Unauthorized);
        assert!(provider.calls.lock().await.is_empty());
    }

    #[tokio::test]
    async fn accepted_retry_is_suppressed_but_different_resolution_is_rejected() {
        let (service, store, provider, _frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let accepted = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .unwrap();
        assert_eq!(accepted.status, InteractionStatus::Accepted);
        let repeated = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .unwrap();
        assert_eq!(repeated.status, InteractionStatus::Accepted);
        assert_eq!(provider.calls.lock().await.len(), 1);

        let mut different = resolve("interaction-1", "idem-2");
        different.resolution = json!({"decision":"deny"});
        let error = service.resolve(different).await.unwrap_err();
        assert!(matches!(
            error,
            InteractionServiceError::ResolveFailed {
                retryable: false,
                status: InteractionStatus::Accepted,
                ..
            }
        ));

        let stored = store
            .get(&InteractionKey {
                bcs_run_id: "bcs-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
            })
            .await
            .unwrap()
            .unwrap();
        assert_eq!(stored.status, InteractionStatus::Accepted);
    }

    #[tokio::test]
    async fn accepted_retry_after_authoritative_resolved_is_still_success() {
        let (service, _store, provider, _frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();
        service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .unwrap();
        service
            .on_provider_resolved(ProviderInteractionResolvedCommand {
                bcs_run_id: "bcs-run-1".to_string(),
                provider_run_id: "provider-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
                kind: InteractionKind::Exec,
                payload: json!({
                    "runId":"provider-run-1",
                    "seq":8,
                    "phase":"resolved",
                    "interactionId":"interaction-1",
                    "kind":"exec",
                    "decision":"allow_once"
                }),
                received_at_ms: super::now_ms(),
            })
            .await
            .unwrap();

        let repeated = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .expect("same acknowledged resolution remains idempotent after SSE resolved");

        assert_eq!(repeated.status, InteractionStatus::Resolved);
        assert_eq!(provider.calls.lock().await.len(), 1);
    }

    #[tokio::test]
    async fn resolved_sse_racing_before_provider_ack_is_reported_as_success() {
        let store = Arc::new(MemoryInteractionStore::new());
        let frontend = Arc::new(RecordingFrontend::default());
        let service = InteractionManagement::new(
            store.clone(),
            Arc::new(FixedAuthorization(true)),
            Arc::new(ResolvingBeforeAckProvider {
                store: store.clone(),
                terminal: InteractionStatus::Resolved,
                response: Mutex::new(None),
            }),
            frontend,
            120_000,
        );
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let result = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .expect("Provider ACK after authoritative SSE resolved is still success");

        assert!(result.accepted);
        assert_eq!(result.status, InteractionStatus::Resolved);

        let repeated = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .expect("ACK metadata survives an SSE-resolved race for later retries");
        assert_eq!(repeated.status, InteractionStatus::Resolved);
    }

    #[tokio::test]
    async fn resolved_sse_racing_before_transport_error_is_reported_as_success() {
        let store = Arc::new(MemoryInteractionStore::new());
        let service = InteractionManagement::new(
            store.clone(),
            Arc::new(FixedAuthorization(true)),
            Arc::new(ResolvingBeforeAckProvider {
                store: store.clone(),
                terminal: InteractionStatus::Resolved,
                response: Mutex::new(Some(Err(ServiceError::InternalError(
                    "ack lost".to_string(),
                )))),
            }),
            Arc::new(RecordingFrontend::default()),
            120_000,
        );
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let result = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .expect("authoritative resolved SSE wins over transport failure");

        assert_eq!(result.status, InteractionStatus::Resolved);
    }

    #[tokio::test]
    async fn run_invalidation_racing_before_retryable_ack_is_non_retryable() {
        let store = Arc::new(MemoryInteractionStore::new());
        let service = InteractionManagement::new(
            store.clone(),
            Arc::new(FixedAuthorization(true)),
            Arc::new(ResolvingBeforeAckProvider {
                store: store.clone(),
                terminal: InteractionStatus::Invalidated,
                response: Mutex::new(Some(Ok(InteractionProviderAck {
                    ok: false,
                    retryable: Some(true),
                    error: Some("temporary".to_string()),
                }))),
            }),
            Arc::new(RecordingFrontend::default()),
            120_000,
        );
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let error = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .unwrap_err();

        assert!(matches!(
            error,
            InteractionServiceError::ResolveFailed {
                retryable: false,
                status: InteractionStatus::Invalidated,
                ..
            }
        ));
    }

    #[tokio::test]
    async fn provider_failures_follow_retryable_default_and_explicit_false() {
        let (service, store, provider, _frontend) = service(true);
        service
            .on_provider_requested(requested("retryable"))
            .await
            .unwrap();
        *provider.response.lock().await = Some(Ok(InteractionProviderAck {
            ok: false,
            retryable: None,
            error: Some("temporary".to_string()),
        }));
        let error = service
            .resolve(resolve("retryable", "idem-1"))
            .await
            .unwrap_err();
        assert!(matches!(
            error,
            InteractionServiceError::ResolveFailed {
                retryable: true,
                status: InteractionStatus::Pending,
                ..
            }
        ));

        service
            .on_provider_requested(requested("terminal"))
            .await
            .unwrap();
        *provider.response.lock().await = Some(Ok(InteractionProviderAck {
            ok: false,
            retryable: Some(false),
            error: Some("engine rejected".to_string()),
        }));
        let error = service
            .resolve(resolve("terminal", "idem-2"))
            .await
            .unwrap_err();
        assert!(matches!(
            error,
            InteractionServiceError::ResolveFailed {
                retryable: false,
                status: InteractionStatus::Invalidated,
                ..
            }
        ));
        let terminal = store
            .get(&InteractionKey {
                bcs_run_id: "bcs-run-1".to_string(),
                interaction_id: "terminal".to_string(),
            })
            .await
            .unwrap()
            .unwrap();
        assert_eq!(terminal.status, InteractionStatus::Invalidated);
    }

    #[tokio::test]
    async fn concurrent_humans_share_one_per_interaction_delivery_guard() {
        let store = Arc::new(MemoryInteractionStore::new());
        let provider = Arc::new(BlockingProvider::default());
        let service = Arc::new(InteractionManagement::new(
            store,
            Arc::new(FixedAuthorization(true)),
            provider.clone(),
            Arc::new(RecordingFrontend::default()),
            120_000,
        ));
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let first_service = service.clone();
        let first = tokio::spawn(async move {
            first_service
                .resolve(resolve("interaction-1", "idem-first"))
                .await
        });
        provider.entered.notified().await;

        let mut second = resolve("interaction-1", "idem-second");
        second.resolution = json!({"decision":"deny"});
        let second_error = service.resolve(second).await.unwrap_err();
        assert!(matches!(
            second_error,
            InteractionServiceError::ResolveFailed {
                retryable: true,
                status: InteractionStatus::Pending,
                ..
            }
        ));
        assert_eq!(provider.calls.lock().await.len(), 1);

        provider.release.notify_one();
        assert!(first.await.unwrap().unwrap().accepted);
    }

    #[tokio::test]
    async fn provider_resolved_is_authoritative_and_publishes_completion() {
        let (service, store, _provider, frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();
        service
            .on_provider_resolved(ProviderInteractionResolvedCommand {
                bcs_run_id: "bcs-run-1".to_string(),
                provider_run_id: "provider-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
                kind: InteractionKind::Exec,
                payload: json!({
                    "runId":"provider-run-1",
                    "seq":8,
                    "phase":"resolved",
                    "interactionId":"interaction-1",
                    "kind":"exec",
                    "decision":"allow_once"
                }),
                received_at_ms: 300,
            })
            .await
            .unwrap();

        let stored = store
            .get(&InteractionKey {
                bcs_run_id: "bcs-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
            })
            .await
            .unwrap()
            .unwrap();
        assert_eq!(stored.status, InteractionStatus::Resolved);
        assert_eq!(frontend.calls.lock().await.len(), 2);
    }

    #[tokio::test]
    async fn provider_resolved_exec_must_echo_an_offered_decision() {
        let (service, store, _provider, frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let error = service
            .on_provider_resolved(ProviderInteractionResolvedCommand {
                bcs_run_id: "bcs-run-1".to_string(),
                provider_run_id: "provider-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
                kind: InteractionKind::Exec,
                payload: json!({
                    "runId":"provider-run-1",
                    "seq":8,
                    "phase":"resolved",
                    "interactionId":"interaction-1",
                    "kind":"exec",
                    "decision":"allow_persistent"
                }),
                received_at_ms: 300,
            })
            .await
            .unwrap_err();

        assert!(error.to_string().contains("was not offered"));
        let stored = store
            .get(&InteractionKey {
                bcs_run_id: "bcs-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
            })
            .await
            .unwrap()
            .unwrap();
        assert_eq!(stored.status, InteractionStatus::Pending);
        assert_eq!(frontend.calls.lock().await.len(), 1);
    }

    #[tokio::test]
    async fn transport_error_stays_pending_and_is_retryable() {
        let (service, store, provider, _frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();
        *provider.response.lock().await =
            Some(Err(ServiceError::InternalError("timeout".to_string())));

        let error = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .unwrap_err();
        assert!(matches!(
            error,
            InteractionServiceError::ResolveFailed {
                retryable: true,
                status: InteractionStatus::Pending,
                ..
            }
        ));
        let stored = store
            .get(&InteractionKey {
                bcs_run_id: "bcs-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
            })
            .await
            .unwrap()
            .unwrap();
        assert!(!stored.in_flight);
    }

    #[tokio::test]
    async fn rejects_invalid_requested_shape_and_unoffered_exec_decision() {
        let (service, _store, provider, _frontend) = service(true);
        let mut invalid = requested("invalid");
        invalid.payload = json!({
            "phase":"requested",
            "interactionId":"invalid",
            "kind":"exec",
            "command":"deploy",
            "options":[]
        });
        assert!(service.on_provider_requested(invalid).await.is_err());

        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();
        let mut unoffered = resolve("interaction-1", "idem-1");
        unoffered.resolution = json!({"decision":"always_allow"});
        assert!(matches!(
            service.resolve(unoffered).await,
            Err(InteractionServiceError::InvalidRequest(_))
        ));
        assert!(provider.calls.lock().await.is_empty());
    }

    #[tokio::test]
    async fn ask_user_submit_requires_answers_for_every_question() {
        let (service, _store, provider, _frontend) = service(true);
        let mut ask = requested("ask-1");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-1",
            "kind":"ask_user",
            "questions":[
                {"questionId":"target","question":"Where?","options":[
                    {"value":"staging","label":"Staging"},
                    {"value":"prod","label":"Production"}
                ]},
                {"questionId":"confirm","question":"Continue?","options":[
                    {"value":"yes","label":"Yes"},
                    {"value":"no","label":"No"}
                ]}
            ]
        });
        service.on_provider_requested(ask).await.unwrap();
        let mut incomplete = resolve("ask-1", "idem-ask");
        incomplete.resolution = json!({
            "action":"submit",
            "answers":{"target":{"values":["staging"]}}
        });
        assert!(matches!(
            service.resolve(incomplete).await,
            Err(InteractionServiceError::InvalidRequest(_))
        ));
        assert!(provider.calls.lock().await.is_empty());
    }

    #[tokio::test]
    async fn ask_user_accepts_custom_values_regardless_of_allow_other() {
        let (service, _store, provider, _frontend) = service(true);
        let mut ask = requested("ask-custom");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-custom",
            "kind":"ask_user",
            "questions":[
                {
                    "questionId":"missing_hint",
                    "question":"Choose or customize",
                    "options":[{"value":"offered","label":"Offered"}]
                },
                {
                    "questionId":"false_hint",
                    "question":"Choose or customize anyway",
                    "allowOther":false,
                    "options":[{"value":"offered","label":"Offered"}]
                }
            ]
        });
        service.on_provider_requested(ask).await.unwrap();

        let mut command = resolve("ask-custom", "idem-custom");
        command.resolution = json!({
            "action":"submit",
            "answers":{
                "missing_hint":{"values":["custom without hint"]},
                "false_hint":{"values":["custom despite false"]}
            }
        });

        let result = service.resolve(command).await.unwrap();

        assert!(result.accepted);
        let calls = provider.calls.lock().await;
        assert_eq!(calls.len(), 1);
        assert_eq!(
            calls[0].resolution["answers"]["missing_hint"]["values"],
            json!(["custom without hint"])
        );
        assert_eq!(
            calls[0].resolution["answers"]["false_hint"]["values"],
            json!(["custom despite false"])
        );
    }

    #[tokio::test]
    async fn ask_user_accepts_skipped_values_and_forwards_them_unchanged() {
        let (service, _store, provider, _frontend) = service(true);
        let mut ask = requested("ask-skip");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-skip",
            "kind":"ask_user",
            "questions":[
                {
                    "questionId":"empty_array",
                    "question":"Skip with an empty array?",
                    "options":[{"value":"offered","label":"Offered"}]
                },
                {
                    "questionId":"empty_string",
                    "question":"Skip with an empty string?",
                    "options":[{"value":"offered","label":"Offered"}]
                },
                {
                    "questionId":"whitespace",
                    "question":"Skip with whitespace?"
                }
            ]
        });
        service.on_provider_requested(ask).await.unwrap();

        let mut command = resolve("ask-skip", "idem-skip");
        command.resolution = json!({
            "action":"submit",
            "answers":{
                "empty_array":{"values":[]},
                "empty_string":{"values":[""]},
                "whitespace":{"values":["   "]}
            }
        });

        let result = service.resolve(command).await.unwrap();

        assert!(result.accepted);
        let calls = provider.calls.lock().await;
        assert_eq!(calls.len(), 1);
        let answers = &calls[0].resolution["answers"];
        assert_eq!(answers["empty_array"]["values"], json!([]));
        assert_eq!(answers["empty_string"]["values"], json!([""]));
        assert_eq!(answers["whitespace"]["values"], json!(["   "]));
    }

    #[tokio::test]
    async fn ask_user_still_rejects_non_string_values() {
        let (service, _store, provider, _frontend) = service(true);
        let mut ask = requested("ask-non-string");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-non-string",
            "kind":"ask_user",
            "questions":[{
                "questionId":"target",
                "question":"Choose or skip",
                "options":[{"value":"offered","label":"Offered"}]
            }]
        });
        service.on_provider_requested(ask).await.unwrap();

        let mut command = resolve("ask-non-string", "idem-non-string");
        command.resolution = json!({
            "action":"submit",
            "answers":{"target":{"values":[null]}}
        });

        assert!(matches!(
            service.resolve(command).await,
            Err(InteractionServiceError::InvalidRequest(_))
        ));
        assert!(provider.calls.lock().await.is_empty());
    }

    #[tokio::test]
    async fn ask_user_treats_non_boolean_allow_other_as_omitted() {
        let (service, store, _provider, frontend) = service(true);
        let mut ask = requested("ask-invalid-hint");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-invalid-hint",
            "kind":"ask_user",
            "questions":[{
                "questionId":"target",
                "question":"Choose or customize",
                "allowOther":"yes",
                "options":[{"value":"offered","label":"Offered"}]
            }]
        });

        service.on_provider_requested(ask).await.unwrap();

        let stored = store
            .get(&InteractionKey {
                bcs_run_id: "bcs-run-1".to_string(),
                interaction_id: "ask-invalid-hint".to_string(),
            })
            .await
            .unwrap()
            .unwrap();
        assert!(stored.requested_payload["questions"][0]
            .get("allowOther")
            .is_none());
        let calls = frontend.calls.lock().await;
        assert!(calls[0].payload["questions"][0].get("allowOther").is_none());
    }

    #[tokio::test]
    async fn ask_user_submit_augments_answers_with_origin_question() {
        let (service, _store, provider, _frontend) = service(true);
        let mut ask = requested("ask-1");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-1",
            "kind":"ask_user",
            "questions":[
                {"questionId":"target","header":"Deployment environment","question":"Where should this be deployed?","options":[
                    {"value":"staging","label":"Staging"},
                    {"value":"prod","label":"Production"}
                ]},
                {"questionId":"components","header":"Components","question":"Which components?","multiSelect":true,"options":[
                    {"value":"web","label":"Web"},
                    {"value":"worker","label":"Worker"}
                ]}
            ]
        });
        service.on_provider_requested(ask).await.unwrap();

        let mut command = resolve("ask-1", "idem-ask");
        command.resolution = json!({
            "action":"submit",
            "answers":{
                "target":{
                    "values":["staging"],
                    "question":"frontend question",
                    "header":"frontend header"
                },
                "components":{"values":["web","worker"]}
            }
        });
        let result = service.resolve(command).await.unwrap();
        assert!(result.accepted);

        let calls = provider.calls.lock().await;
        let resolution = &calls[0].resolution;
        assert_eq!(resolution["action"], "submit");
        assert_eq!(
            resolution["answers"]["target"]["values"],
            json!(["staging"])
        );
        assert_eq!(
            resolution["answers"]["target"]["question"],
            "Where should this be deployed?"
        );
        assert_eq!(
            resolution["answers"]["target"]["header"],
            "Deployment environment"
        );
        assert_eq!(
            resolution["answers"]["components"]["values"],
            json!(["web", "worker"])
        );
        assert_eq!(
            resolution["answers"]["components"]["question"],
            "Which components?"
        );
        assert_eq!(resolution["answers"]["components"]["header"], "Components");
    }

    #[tokio::test]
    async fn ask_user_submit_omits_header_when_requested_header_is_absent() {
        let (service, _store, provider, _frontend) = service(true);
        let mut ask = requested("ask-1");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-1",
            "kind":"ask_user",
            "questions":[{
                "questionId":"target",
                "question":"Where?",
                "options":[{"value":"staging","label":"Staging"}]
            }]
        });
        service.on_provider_requested(ask).await.unwrap();

        let mut command = resolve("ask-1", "idem-ask");
        command.resolution = json!({
            "action":"submit",
            "answers":{
                "target":{
                    "values":["staging"],
                    "question":"frontend question",
                    "header":"untrusted"
                }
            }
        });
        let result = service.resolve(command).await.unwrap();
        assert!(result.accepted);

        let calls = provider.calls.lock().await;
        let answer = &calls[0].resolution["answers"]["target"];
        assert_eq!(answer["question"], "Where?");
        assert!(answer.get("header").is_none());
    }

    #[tokio::test]
    async fn ask_user_rejects_all_secret_field_aliases() {
        for secret_field in ["secret", "isSecret"] {
            let (service, _store, _provider, _frontend) = service(true);
            let mut ask = requested("ask-secret");
            ask.kind = InteractionKind::AskUser;
            ask.payload = json!({
                "runId":"provider-run-1",
                "phase":"requested",
                "interactionId":"ask-secret",
                "kind":"ask_user",
                "questions":[{
                    "questionId":"credential",
                    "question":"Enter credential",
                    (secret_field): true
                }]
            });

            assert!(service.on_provider_requested(ask).await.is_err());
        }
    }

    #[tokio::test]
    async fn rejects_oversized_requested_payload_before_storing_or_publishing() {
        let (service, store, _provider, frontend) = service(true);
        let mut oversized = requested("oversized");
        oversized.payload["command"] = json!("x".repeat(super::MAX_INTERACTION_PAYLOAD_BYTES));

        assert!(service.on_provider_requested(oversized).await.is_err());
        assert!(store.list_pending("session-1").await.unwrap().is_empty());
        assert!(frontend.calls.lock().await.is_empty());
    }
}
