use std::{
    collections::HashSet,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};

use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
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
        match &command.provider_target {
            BotDeliveryTarget::WebSocket { bot_id } => {
                if bot_id.trim().is_empty() {
                    return Err(ServiceError::InvalidOperation {
                        message: "interaction requested requires a non-empty WebSocket bot id"
                            .to_string(),
                        request_id: Some(command.bcs_run_id),
                    });
                }
            }
            BotDeliveryTarget::HttpProvider { .. } | BotDeliveryTarget::A2a { .. } => {}
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
        validate_requested_payload(command.kind, &command.payload).map_err(|message| {
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
            requested_payload: command.payload,
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

        if let Err(message) = validate_resolution(&record, &command.resolution) {
            warn!(
                bcs_run_id = %record.key.bcs_run_id,
                provider_run_id = %record.provider_run_id,
                bcs_session_id = %record.bcs_session_id,
                group_id = %record.group_id,
                bot_id = %record.bot_id,
                interaction_id = %record.key.interaction_id,
                interaction_kind = ?record.kind,
                resolver_actor_id = %command.resolver_actor_id,
                validation_error = %message,
                "interaction resolution rejected"
            );
            return Err(InteractionServiceError::InvalidRequest(message));
        }

        // 前端 resolve 只发 values；BCS 对照 requested options 把值分类为
        // Provider 所需的 values/customValues，并补入权威 question/header。
        // fingerprint 与 Provider 转发均使用规范化后的版本。
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
            if payload.get("command").is_some() {
                require_non_empty_string(payload, "command")?;
            }
            validate_decision_options(payload.get("options"), true)
        }
        bcs_service_api::InteractionKind::ModeSwitch => {
            validate_decision_options(payload.get("options"), true)
        }
        bcs_service_api::InteractionKind::AskUser => validate_questions(payload),
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
            if question
                .get("allowOther")
                .is_some_and(|allow_other| !allow_other.is_boolean())
            {
                return Err("ask_user allowOther must be a boolean when present".to_string());
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
        if values
            .iter()
            .any(|value| value.as_str().is_none_or(|value| value.trim().is_empty()))
        {
            return Err(format!(
                "ask_user answer {question_id} values must be non-empty strings"
            ));
        }
        if answer.get("customValues").is_some() {
            return Err(format!(
                "ask_user answer {question_id} must put submitted selections in values; customValues is generated by BCS"
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
        if let Some(options) = question.get("options").and_then(Value::as_array) {
            let offered = options
                .iter()
                .filter_map(|option| option.get("value").and_then(Value::as_str))
                .collect::<HashSet<_>>();
            let has_custom_value = values
                .iter()
                .filter_map(Value::as_str)
                .any(|value| !offered.contains(value));
            let allow_other = question
                .get("allowOther")
                .and_then(Value::as_bool)
                // Provider 2.0 permits omission; BaaS commonly omits the field
                // when Engine does not send it. Only explicit false opts out.
                .unwrap_or(true);
            if has_custom_value && !allow_other {
                return Err(format!(
                    "ask_user question {question_id} does not allow custom answers; select one of the provided options"
                ));
            }
        } else if values.len() > 1 {
            return Err(format!(
                "free-text answer {question_id} accepts at most one value"
            ));
        }
    }
    Ok(())
}

/// AskUser submit 时，按 questionId（answers 对象的键）把 requested 阶段存储的
/// 原始 question 和可选 header 补进每个 answer 对象，与 `values/customValues` 平级。
/// 前端 resolve 只发 `values`。对于选择题，BCS 用 requested options 的 value
/// 精确匹配并拆成声明过的 `values` 与自定义的 `customValues`；自由文本题不拆分。
/// question/header 始终由 BCS 用权威存储值覆盖，header 缺失时保持缺失，不从
/// questionId 或前端输入合成。canonical resolution 同时用于 fingerprint 与
/// Provider 转发。cancel / exec / mode_switch 原样返回。
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
    let Some(answers) = resolution.get_mut("answers").and_then(Value::as_object_mut) else {
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
            answer.remove("customValues");
            if let (Some(options), Some(values)) = (
                question.get("options").and_then(Value::as_array),
                answer.get("values").and_then(Value::as_array),
            ) {
                let offered = options
                    .iter()
                    .filter_map(|option| option.get("value").and_then(Value::as_str))
                    .collect::<HashSet<_>>();
                let (declared_values, custom_values): (Vec<_>, Vec<_>) = values
                    .iter()
                    .cloned()
                    .partition(|value| value.as_str().is_some_and(|value| offered.contains(value)));
                answer.insert("values".to_string(), Value::Array(declared_values));
                if !custom_values.is_empty() {
                    answer.insert("customValues".to_string(), Value::Array(custom_values));
                }
            }
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
#[path = "management_tests.rs"]
mod tests;
