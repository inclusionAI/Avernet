//! Event Subscription application service.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_config_api::{EventingConfig, PrivateEndpointAllowlistEntryConfig};
use bcs_service_api::application::v1::{
    ApplicationError, CreateEventSubscription, CursorPage, DeleteEventSubscription,
    EventDeliveryAttemptResult, EventDeliveryAttemptSummary, EventDeliveryDetail,
    EventDeliverySummary, EventSinkInput, EventSinkView, EventSubscription,
    EventSubscriptionDesiredStatus, EventSubscriptionService, EventSubscriptionTestResult,
    EventWebhookEndpointView, GetEventDelivery, GetEventSubscription,
    GroupEventSubscriptionProvisioner, IdentityPolicy, InlineGroupEventSubscriptionRequest,
    ListEventDeliveries, ListEventSubscriptions, PatchEventSinkInput, PatchEventSubscription,
    PendingGroupEventSubscriptions, PreparedGroupEventSubscriptions, Principal, ReplayEventDelivery,
    ReplayEventDeliveryResult, SkipEventDelivery, SkipEventDeliveryResult, TestEventSubscription,
    select_principal,
};
use bcs_service_api::port::repo::{
    AppendEventRecord, CancelPendingEventSubscriptions, CreateEventReplayTarget,
    CreateEventSubscriptionRecord, EventDeliveryAttemptRecord, EventDeliveryAttemptRecordResult,
    EventDeliveryRecord, EventRepoError, EventRepoPort, EventSubscriptionRecord,
    EventSubscriptionRevisionRecord, FinalizeGroupProvisioning, ListEventDeliveryRecords,
    ListEventSubscriptionRecords, ReplaceEventSubscriptionRevision, SkipDeadLetteredEventDelivery,
};
use bcs_service_api::port::{
    EventDeliveryDisposition, EventDeliveryPort, EventDeliveryRequest, NewEvent,
};
use bcs_service_api::types::{
    EVENT_SCHEMA_VERSION_V1, EVENT_SOURCE, EVENT_SPEC_VERSION, EventActor, EventActorType,
    EventDeliveryStatus, EventEnvelope, EventOrdering, EventOrderingMode, EventPayload,
    EventPayloadMode, EventScope, EventStream, EventSubject, EventSubscriptionScope,
    EventSubscriptionScopeType, EventSubscriptionStatus, Group, GroupKind, GroupStatus,
    GroupStrategy, Session, SessionKind, SessionStatus,
};
use chrono::{SecondsFormat, TimeZone, Utc};
use serde_json::json;
use sha2::{Digest, Sha256};
use url::Url;
use uuid::Uuid;

use crate::EventCatalog;
use crate::authorization::{
    AuthorizedEventSubscriptionScope, EventSubscriptionAuthorizationAction,
    EventSubscriptionAuthorizer,
};
use crate::matcher::{validate_event_filter, validate_subscription_scope};

const MAX_NAME_CHARACTERS: usize = 128;
const MAX_WEBHOOK_URL_BYTES: usize = 2_048;
const MIN_REQUEST_TIMEOUT_MS: u64 = 1_000;
const MILLIS_PER_DAY: u64 = 86_400_000;

#[derive(Debug, Clone)]
pub struct EventSubscriptionPolicy {
    pub enabled: bool,
    pub default_request_timeout_ms: u64,
    pub max_request_timeout_ms: u64,
    pub max_event_body_bytes: usize,
    pub max_group_subscriptions: u32,
    pub max_filters_per_subscription: u32,
    pub allow_http_loopback: bool,
    pub allow_non_standard_ports: bool,
    pub private_endpoint_allowlist: Vec<PrivateEndpointAllowlistEntryConfig>,
}

impl From<&EventingConfig> for EventSubscriptionPolicy {
    fn from(config: &EventingConfig) -> Self {
        Self {
            enabled: config.enabled,
            default_request_timeout_ms: config.webhook.request_timeout_ms,
            max_request_timeout_ms: config.webhook.max_request_timeout_ms,
            max_event_body_bytes: config.webhook.max_event_body_bytes,
            max_group_subscriptions: config.limits.max_group_subscriptions,
            max_filters_per_subscription: config.limits.max_filters_per_subscription,
            allow_http_loopback: config.webhook.allow_http_loopback,
            allow_non_standard_ports: config.webhook.allow_non_standard_ports,
            private_endpoint_allowlist: config.webhook.private_endpoint_allowlist.clone(),
        }
    }
}

impl Default for EventSubscriptionPolicy {
    fn default() -> Self {
        Self::from(&EventingConfig::default())
    }
}

pub struct EventSubscriptionApplicationService {
    repo: Arc<dyn EventRepoPort>,
    delivery: Arc<dyn EventDeliveryPort>,
    authorizer: Arc<dyn EventSubscriptionAuthorizer>,
    catalog: Arc<EventCatalog>,
    policy: EventSubscriptionPolicy,
    env: String,
    group_provisioning: Option<GroupProvisioningRuntime>,
    now_ms: Arc<dyn Fn() -> u64 + Send + Sync>,
    new_id: Arc<dyn Fn(&str) -> String + Send + Sync>,
}

#[derive(Clone)]
struct GroupProvisioningRuntime {
    groups: Arc<dyn bcs_service_api::GroupCoreService>,
    event_retention_days: u32,
}

impl EventSubscriptionApplicationService {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        repo: Arc<dyn EventRepoPort>,
        delivery: Arc<dyn EventDeliveryPort>,
        authorizer: Arc<dyn EventSubscriptionAuthorizer>,
        catalog: Arc<EventCatalog>,
        policy: EventSubscriptionPolicy,
        env: impl Into<String>,
    ) -> Self {
        Self {
            repo,
            delivery,
            authorizer,
            catalog,
            policy,
            env: env.into(),
            group_provisioning: None,
            now_ms: Arc::new(system_now_ms_for_workers),
            new_id: Arc::new(|prefix| format!("{prefix}_{}", Uuid::new_v4())),
        }
    }

    pub fn with_group_provisioning(
        mut self,
        groups: Arc<dyn bcs_service_api::GroupCoreService>,
        event_retention_days: u32,
    ) -> Self {
        self.group_provisioning = Some(GroupProvisioningRuntime {
            groups,
            event_retention_days,
        });
        self
    }

    pub fn with_runtime(
        mut self,
        now_ms: Arc<dyn Fn() -> u64 + Send + Sync>,
        new_id: Arc<dyn Fn(&str) -> String + Send + Sync>,
    ) -> Self {
        self.now_ms = now_ms;
        self.new_id = new_id;
        self
    }

    fn ensure_enabled(&self) -> Result<(), ApplicationError> {
        if self.policy.enabled {
            Ok(())
        } else {
            Err(ApplicationError::conflict(
                "eventing_disabled",
                "Event subscriptions are disabled",
            ))
        }
    }

    async fn authorize(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        scope: &EventSubscriptionScope,
        action: EventSubscriptionAuthorizationAction,
    ) -> Result<AuthorizedEventSubscriptionScope, ApplicationError> {
        self.authorizer.authorize(caller, scope, action).await
    }

    async fn authorize_subscription(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        subscription: &EventSubscriptionRecord,
        action: EventSubscriptionAuthorizationAction,
    ) -> Result<AuthorizedEventSubscriptionScope, ApplicationError> {
        self.authorize(caller, &subscription.scope, action).await
    }

    async fn load_subscription(
        &self,
        subscription_id: &str,
    ) -> Result<(EventSubscriptionRecord, EventSubscriptionRevisionRecord), ApplicationError> {
        self.repo
            .get_subscription(subscription_id, &self.env)
            .await
            .map_err(map_repo_error)?
            .ok_or_else(|| {
                ApplicationError::event_subscription_not_found("Event Subscription not found")
            })
    }

    async fn to_view(
        &self,
        record: &EventSubscriptionRecord,
        revision: &EventSubscriptionRevisionRecord,
    ) -> Result<EventSubscription, ApplicationError> {
        let endpoint = redact_endpoint(&revision.endpoint_url)?;
        Ok(EventSubscription {
            subscription_id: record.subscription_id.clone(),
            name: record.name.clone(),
            scope: record.scope.clone(),
            include_descendants: true,
            event_filters: revision.event_filters.clone(),
            payload: EventPayload {
                mode: revision.payload_mode,
            },
            ordering: EventOrdering {
                mode: EventOrderingMode::StrictPerStream,
            },
            sink: EventSinkView::Webhook {
                endpoint,
                request_timeout_ms: revision.request_timeout_ms,
            },
            status: record.status,
            revision: record.current_revision,
            created_at: timestamp(record.created_at_ms)?,
            updated_at: timestamp(record.updated_at_ms)?,
        })
    }

    fn validate_name(&self, name: &str) -> Result<(), ApplicationError> {
        if name.trim().is_empty() || name.chars().count() > MAX_NAME_CHARACTERS {
            return Err(ApplicationError::invalid(
                "invalid_event_subscription_name",
                "Subscription name must contain 1 to 128 characters",
            ));
        }
        Ok(())
    }

    fn validate_filters(&self, filters: &[String]) -> Result<(), ApplicationError> {
        if filters.is_empty() || filters.len() > self.policy.max_filters_per_subscription as usize {
            return Err(ApplicationError::invalid_event_filter(
                "Event filter count is outside the configured limit",
            ));
        }
        let mut unique = std::collections::HashSet::new();
        for filter in filters {
            validate_event_filter(&self.catalog, filter)
                .map_err(|error| ApplicationError::invalid_event_filter(error.to_string()))?;
            if !unique.insert(filter) {
                return Err(ApplicationError::invalid_event_filter(
                    "Duplicate Event filters are not allowed",
                ));
            }
        }
        Ok(())
    }

    fn validate_group_scope(&self, scope: &EventSubscriptionScope) -> Result<(), ApplicationError> {
        if scope.scope_type != EventSubscriptionScopeType::Group {
            return Err(ApplicationError::invalid_event_scope(
                "Only group Event Subscription scope is supported",
            ));
        }
        validate_subscription_scope(scope)
            .map_err(|error| ApplicationError::invalid_event_scope(error.to_string()))
    }

    fn validate_payload_authorization(
        &self,
        payload_mode: EventPayloadMode,
        grant: &AuthorizedEventSubscriptionScope,
    ) -> Result<(), ApplicationError> {
        if payload_mode == EventPayloadMode::Full && !grant.full_payload_allowed {
            return Err(ApplicationError::event_subscription_forbidden(
                "Full Event payload access requires a separate grant",
            ));
        }
        Ok(())
    }

    fn validate_create_request(
        &self,
        request: &bcs_service_api::application::v1::CreateEventSubscriptionRequest,
    ) -> Result<(), ApplicationError> {
        self.validate_name(&request.name)?;
        self.validate_group_scope(&request.scope)?;
        self.validate_filters(&request.event_filters)?;
        Ok(())
    }

    async fn persist_create(
        &self,
        request: bcs_service_api::application::v1::CreateEventSubscriptionRequest,
        grant: AuthorizedEventSubscriptionScope,
        status: EventSubscriptionStatus,
    ) -> Result<EventSubscription, ApplicationError> {
        self.validate_payload_authorization(request.payload.mode, &grant)?;
        let EventSinkInput::Webhook {
            url,
            request_timeout_ms,
        } = request.sink;
        validate_webhook_url(&url, &self.policy)?;
        let request_timeout_ms =
            request_timeout_ms.unwrap_or(self.policy.default_request_timeout_ms);
        validate_request_timeout(request_timeout_ms, self.policy.max_request_timeout_ms)?;

        let subscription_id = (self.new_id)("sub");
        let revision_number = 1;
        let now_ms = (self.now_ms)();
        let record = CreateEventSubscriptionRecord {
            subscription: EventSubscriptionRecord {
                subscription_id: subscription_id.clone(),
                name: request.name,
                scope: request.scope,
                status,
                current_revision: revision_number,
                created_by: grant.actor,
                created_at_ms: now_ms,
                updated_at_ms: now_ms,
                deleted_at_ms: None,
                env: self.env.clone(),
            },
            revision: EventSubscriptionRevisionRecord {
                subscription_id: subscription_id.clone(),
                revision: revision_number,
                event_filters: request.event_filters,
                payload_mode: request.payload.mode,
                endpoint_url: url,
                request_timeout_ms,
                activated_at_ms: now_ms,
                retired_at_ms: None,
            },
            scope_limit: self.policy.max_group_subscriptions,
        };
        self.repo
            .create_subscription(record)
            .await
            .map_err(map_repo_error)?;
        let (record, revision) = self.load_subscription(&subscription_id).await?;
        self.to_view(&record, &revision).await
    }
}

#[async_trait]
impl EventSubscriptionService for EventSubscriptionApplicationService {
    async fn create(
        &self,
        command: CreateEventSubscription,
    ) -> Result<EventSubscription, ApplicationError> {
        self.ensure_enabled()?;
        let request = command.request;
        self.validate_create_request(&request)?;
        let grant = self
            .authorize(
                &command.caller,
                &request.scope,
                EventSubscriptionAuthorizationAction::Create,
            )
            .await?;
        self.persist_create(request, grant, EventSubscriptionStatus::Active)
            .await
    }

    async fn list(
        &self,
        query: ListEventSubscriptions,
    ) -> Result<CursorPage<EventSubscription>, ApplicationError> {
        self.ensure_enabled()?;
        if query.limit == 0 || query.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_page_limit",
                "List limit must be between 1 and 100",
            ));
        }
        if let Some(scope) = &query.scope {
            self.validate_group_scope(scope)?;
            self.authorize(
                &query.caller,
                scope,
                EventSubscriptionAuthorizationAction::Read,
            )
            .await?;
        }
        let fetch_limit = query.limit.saturating_add(1).min(100);
        let records = self
            .repo
            .list_subscriptions(ListEventSubscriptionRecords {
                scope: query.scope,
                status: query.status,
                after_subscription_id: query.cursor,
                limit: fetch_limit,
                env: self.env.clone(),
            })
            .await
            .map_err(map_repo_error)?;
        let has_more =
            records.len() > query.limit as usize || (query.limit == 100 && records.len() == 100);
        let mut items = Vec::new();
        for record in records.iter().take(query.limit as usize) {
            match self
                .authorize_subscription(
                    &query.caller,
                    record,
                    EventSubscriptionAuthorizationAction::Read,
                )
                .await
            {
                Ok(_) => {}
                Err(error) if is_visibility_denial(&error) => continue,
                Err(error) => return Err(error),
            }
            let (_, revision) = self.load_subscription(&record.subscription_id).await?;
            items.push(self.to_view(record, &revision).await?);
        }
        let next_cursor = has_more
            .then(|| records.get(query.limit.saturating_sub(1) as usize))
            .flatten()
            .map(|record| record.subscription_id.clone());
        Ok(CursorPage { items, next_cursor })
    }

    async fn get(
        &self,
        query: GetEventSubscription,
    ) -> Result<EventSubscription, ApplicationError> {
        self.ensure_enabled()?;
        let (record, revision) = self.load_subscription(&query.subscription_id).await?;
        self.authorize_subscription(
            &query.caller,
            &record,
            EventSubscriptionAuthorizationAction::Read,
        )
        .await
        .map_err(hide_visibility_error)?;
        self.to_view(&record, &revision).await
    }

    async fn patch(
        &self,
        command: PatchEventSubscription,
    ) -> Result<EventSubscription, ApplicationError> {
        self.ensure_enabled()?;
        let (record, revision) = self.load_subscription(&command.subscription_id).await?;
        let grant = self
            .authorize_subscription(
                &command.caller,
                &record,
                EventSubscriptionAuthorizationAction::Manage,
            )
            .await?;
        if record.current_revision != command.expected_revision {
            return Err(ApplicationError::event_subscription_revision_conflict(
                "Event Subscription revision does not match",
            ));
        }
        if record.status == EventSubscriptionStatus::Deleted {
            return Err(ApplicationError::event_subscription_not_found(
                "Event Subscription not found",
            ));
        }
        if patch_is_empty(&command.patch) {
            return self.to_view(&record, &revision).await;
        }
        let next_revision = record.current_revision + 1;
        let mut name = record.name.clone();
        if let Some(next_name) = command.patch.name {
            self.validate_name(&next_name)?;
            name = next_name;
        }
        let event_filters = command
            .patch
            .event_filters
            .unwrap_or_else(|| revision.event_filters.clone());
        self.validate_filters(&event_filters)?;
        let payload_mode = command
            .patch
            .payload
            .as_ref()
            .map_or(revision.payload_mode, |payload| payload.mode);
        self.validate_payload_authorization(payload_mode, &grant)?;
        let mut endpoint_url = revision.endpoint_url.clone();
        let mut timeout_ms = revision.request_timeout_ms;
        let mut endpoint_changed = false;
        if let Some(PatchEventSinkInput::Webhook {
            url,
            request_timeout_ms,
        }) = command.patch.sink
        {
            if let Some(url) = url {
                validate_webhook_url(&url, &self.policy)?;
                endpoint_url = url;
                endpoint_changed = true;
            }
            if let Some(request_timeout_ms) = request_timeout_ms {
                validate_request_timeout(request_timeout_ms, self.policy.max_request_timeout_ms)?;
                timeout_ms = request_timeout_ms;
            }
        }
        let status = match command.patch.status {
            Some(EventSubscriptionDesiredStatus::Active) => EventSubscriptionStatus::Active,
            Some(EventSubscriptionDesiredStatus::Disabled) => EventSubscriptionStatus::Disabled,
            None => record.status,
        };
        if status != record.status && !record.status.can_transition_to(status) {
            return Err(ApplicationError::conflict(
                "event_subscription_status_transition_invalid",
                "Requested Subscription status transition is not allowed",
            ));
        }
        let payload_tightened = revision.payload_mode == EventPayloadMode::Full
            && payload_mode == EventPayloadMode::MetadataOnly;
        let cancel_retired_pending_deliveries = endpoint_changed
            || payload_tightened
            || matches!(
                status,
                EventSubscriptionStatus::Disabled | EventSubscriptionStatus::Deleted
            );
        let now_ms = (self.now_ms)();
        self.repo
            .replace_subscription_revision(ReplaceEventSubscriptionRevision {
                subscription_id: record.subscription_id.clone(),
                expected_revision: record.current_revision,
                name,
                status,
                revision: EventSubscriptionRevisionRecord {
                    subscription_id: record.subscription_id.clone(),
                    revision: next_revision,
                    event_filters,
                    payload_mode,
                    endpoint_url,
                    request_timeout_ms: timeout_ms,
                    activated_at_ms: now_ms,
                    retired_at_ms: None,
                },
                cancel_retired_pending_deliveries,
                actor: grant.actor,
                reason: Some("application_patch".to_string()),
                updated_at_ms: now_ms,
                env: self.env.clone(),
            })
            .await
            .map_err(map_revision_repo_error)?;
        let (updated, updated_revision) = self.load_subscription(&record.subscription_id).await?;
        self.to_view(&updated, &updated_revision).await
    }

    async fn delete(
        &self,
        command: DeleteEventSubscription,
    ) -> Result<EventSubscription, ApplicationError> {
        self.ensure_enabled()?;
        let (record, revision) = self.load_subscription(&command.subscription_id).await?;
        let grant = self
            .authorize_subscription(
                &command.caller,
                &record,
                EventSubscriptionAuthorizationAction::Manage,
            )
            .await?;
        if record.current_revision != command.expected_revision {
            return Err(ApplicationError::event_subscription_revision_conflict(
                "Event Subscription revision does not match",
            ));
        }
        if record.status == EventSubscriptionStatus::Deleted {
            return self.to_view(&record, &revision).await;
        }
        let next_revision = record.current_revision + 1;
        let now_ms = (self.now_ms)();
        self.repo
            .replace_subscription_revision(ReplaceEventSubscriptionRevision {
                subscription_id: record.subscription_id.clone(),
                expected_revision: record.current_revision,
                name: record.name.clone(),
                status: EventSubscriptionStatus::Deleted,
                revision: EventSubscriptionRevisionRecord {
                    subscription_id: record.subscription_id.clone(),
                    revision: next_revision,
                    event_filters: revision.event_filters,
                    payload_mode: revision.payload_mode,
                    endpoint_url: revision.endpoint_url,
                    request_timeout_ms: revision.request_timeout_ms,
                    activated_at_ms: now_ms,
                    retired_at_ms: None,
                },
                cancel_retired_pending_deliveries: true,
                actor: grant.actor,
                reason: Some("application_delete".to_string()),
                updated_at_ms: now_ms,
                env: self.env.clone(),
            })
            .await
            .map_err(map_revision_repo_error)?;
        let (deleted, deleted_revision) = self.load_subscription(&record.subscription_id).await?;
        self.to_view(&deleted, &deleted_revision).await
    }

    async fn test(
        &self,
        command: TestEventSubscription,
    ) -> Result<EventSubscriptionTestResult, ApplicationError> {
        self.ensure_enabled()?;
        let (record, revision) = self.load_subscription(&command.subscription_id).await?;
        self.authorize(
            &command.caller,
            &record.scope,
            EventSubscriptionAuthorizationAction::Test,
        )
        .await?;
        let now_ms = (self.now_ms)();
        let event_id = (self.new_id)("test_evt");
        let request_id = (self.new_id)("test_req");
        let envelope = test_envelope(&record, &event_id, now_ms)?;
        let body = serde_json::to_vec(&envelope)
            .map_err(|_| ApplicationError::internal("serialize test Event"))?;
        if body.len() > self.policy.max_event_body_bytes {
            return Err(ApplicationError::payload_too_large(
                "event_payload_too_large",
                "Test Event exceeds the configured body limit",
            ));
        }
        let response = self
            .delivery
            .deliver(EventDeliveryRequest {
                endpoint_url: revision.endpoint_url,
                body,
                request_timeout_ms: revision.request_timeout_ms,
            })
            .await
            .map_err(|_| {
                ApplicationError::bad_gateway(
                    "event_subscription_test_failed",
                    "Webhook test delivery could not be executed",
                )
            })?;
        Ok(EventSubscriptionTestResult {
            request_id,
            delivered: response.disposition == EventDeliveryDisposition::Succeeded,
            http_status: response.http_status,
            error_category: response.error_category,
            completed_at: timestamp(now_ms)?,
        })
    }

    async fn list_deliveries(
        &self,
        query: ListEventDeliveries,
    ) -> Result<CursorPage<EventDeliverySummary>, ApplicationError> {
        self.ensure_enabled()?;
        if query.limit == 0 || query.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_page_limit",
                "List limit must be between 1 and 100",
            ));
        }
        let (subscription, _) = self.load_subscription(&query.subscription_id).await?;
        self.authorize_subscription(
            &query.caller,
            &subscription,
            EventSubscriptionAuthorizationAction::Read,
        )
        .await
        .map_err(hide_visibility_error)?;
        let fetch_limit = query.limit.saturating_add(1).min(100);
        let deliveries = self
            .repo
            .list_deliveries(ListEventDeliveryRecords {
                subscription_id: Some(query.subscription_id),
                event_id: None,
                status: query.status,
                after_delivery_id: query.cursor,
                limit: fetch_limit,
                env: self.env.clone(),
            })
            .await
            .map_err(map_repo_error)?;
        let has_more = deliveries.len() > query.limit as usize
            || (query.limit == 100 && deliveries.len() == 100);
        let items = deliveries
            .iter()
            .take(query.limit as usize)
            .map(delivery_summary)
            .collect::<Result<Vec<_>, _>>()?;
        let next_cursor = has_more
            .then(|| deliveries.get(query.limit.saturating_sub(1) as usize))
            .flatten()
            .map(|delivery| delivery.delivery_id.clone());
        Ok(CursorPage { items, next_cursor })
    }

    async fn get_delivery(
        &self,
        query: GetEventDelivery,
    ) -> Result<EventDeliveryDetail, ApplicationError> {
        self.ensure_enabled()?;
        let (delivery, attempts) = self
            .repo
            .get_delivery(&query.delivery_id, &self.env)
            .await
            .map_err(map_repo_error)?
            .ok_or_else(|| {
                ApplicationError::event_delivery_not_found("Event Delivery not found")
            })?;
        let (subscription, _) = self.load_subscription(&delivery.subscription_id).await?;
        self.authorize_subscription(
            &query.caller,
            &subscription,
            EventSubscriptionAuthorizationAction::Read,
        )
        .await
        .map_err(|_| ApplicationError::event_delivery_not_found("Event Delivery not found"))?;
        Ok(EventDeliveryDetail {
            delivery: delivery_summary(&delivery)?,
            attempts: attempts
                .iter()
                .map(attempt_summary)
                .collect::<Result<Vec<_>, _>>()?,
            replay_of_delivery_id: delivery.replay_of_delivery_id,
            resolved_by_delivery_id: delivery.resolved_by_delivery_id,
        })
    }

    async fn replay_delivery(
        &self,
        command: ReplayEventDelivery,
    ) -> Result<ReplayEventDeliveryResult, ApplicationError> {
        self.ensure_enabled()?;
        let (original, _) = self
            .repo
            .get_delivery(&command.delivery_id, &self.env)
            .await
            .map_err(map_repo_error)?
            .ok_or_else(|| {
                ApplicationError::event_delivery_not_found("Event Delivery not found")
            })?;
        let (subscription, _) = self.load_subscription(&original.subscription_id).await?;
        let grant = self
            .authorize_subscription(
                &command.caller,
                &subscription,
                EventSubscriptionAuthorizationAction::Replay,
            )
            .await?;
        if subscription.current_revision != command.expected_subscription_revision {
            return Err(ApplicationError::event_subscription_revision_conflict(
                "Event Subscription revision does not match",
            ));
        }
        if subscription.status != EventSubscriptionStatus::Active {
            return Err(ApplicationError::event_delivery_not_replayable(
                "Subscription is not active",
            ));
        }
        let replacement_id = (self.new_id)("del");
        let now_ms = (self.now_ms)();
        let target = self
            .repo
            .create_replay_target(CreateEventReplayTarget {
                original_delivery_id: original.delivery_id.clone(),
                subscription_id: original.subscription_id.clone(),
                subscription_revision: subscription.current_revision,
                replay_request_id: command.replay_request_id,
                target_id: replacement_id.clone(),
                actor: grant.actor,
                reason: Some("application_replay".to_string()),
                created_at_ms: now_ms,
                env: self.env.clone(),
            })
            .await
            .map_err(map_replay_repo_error)?;
        // Manual replay reserves the future Delivery ID as its target ID. The
        // fanout materializer must preserve this ID so a 202 response remains
        // queryable once asynchronous projection completes.
        let replacement_created_at_ms = target.created_at_ms;
        let replacement_id = target.target_id;
        Ok(ReplayEventDeliveryResult {
            original_delivery_id: original.delivery_id.clone(),
            replacement: EventDeliverySummary {
                delivery_id: replacement_id,
                event_id: original.event_id,
                event_type: original.event_type,
                subscription_id: original.subscription_id,
                subscription_revision: subscription.current_revision,
                stream_key_hash: hash_text(&original.stream_key),
                sequence: original.sequence,
                status: EventDeliveryStatus::Pending,
                attempt_count: 0,
                last_http_status: None,
                last_error_category: None,
                created_at: timestamp(replacement_created_at_ms)?,
            },
        })
    }

    async fn skip_delivery(
        &self,
        command: SkipEventDelivery,
    ) -> Result<SkipEventDeliveryResult, ApplicationError> {
        self.ensure_enabled()?;
        if command.reason.trim().is_empty() || command.reason.len() > 1_024 {
            return Err(ApplicationError::invalid(
                "invalid_skip_reason",
                "Skip reason must contain 1 to 1024 bytes",
            ));
        }
        let (delivery, _) = self
            .repo
            .get_delivery(&command.delivery_id, &self.env)
            .await
            .map_err(map_repo_error)?
            .ok_or_else(|| {
                ApplicationError::event_delivery_not_found("Event Delivery not found")
            })?;
        let (subscription, _) = self.load_subscription(&delivery.subscription_id).await?;
        let grant = self
            .authorize_subscription(
                &command.caller,
                &subscription,
                EventSubscriptionAuthorizationAction::Skip,
            )
            .await?;
        let now_ms = (self.now_ms)();
        let skipped = self
            .repo
            .skip_dead_lettered_delivery(SkipDeadLetteredEventDelivery {
                delivery_id: command.delivery_id,
                actor: grant.actor,
                reason: command.reason,
                skipped_at_ms: now_ms,
                env: self.env.clone(),
            })
            .await
            .map_err(map_replay_repo_error)?;
        Ok(SkipEventDeliveryResult {
            delivery_id: skipped.delivery_id,
            status: skipped.status,
            skipped_at: timestamp(now_ms)?,
        })
    }
}

#[async_trait]
impl GroupEventSubscriptionProvisioner for EventSubscriptionApplicationService {
    async fn prepare(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        group_id: &str,
        requests: Vec<InlineGroupEventSubscriptionRequest>,
    ) -> Result<PreparedGroupEventSubscriptions, ApplicationError> {
        self.ensure_enabled()?;
        let principal = select_principal(caller, IdentityPolicy::HumanOrOwnedBot)?;
        let actor = match principal {
            Principal::Human(human) => EventActor {
                actor_type: EventActorType::Human,
                id: format!("human_{}", human.subject.id),
                display_name: human.subject.display_name.or(human.subject.full_name),
            },
            Principal::Bot(bot) => EventActor {
                actor_type: EventActorType::Bot,
                id: bot.bot_uuid,
                display_name: None,
            },
        };
        let grant = AuthorizedEventSubscriptionScope {
            actor: actor.clone(),
            full_payload_allowed: true,
        };
        let mut subscription_ids = Vec::with_capacity(requests.len());
        for request in requests {
            let request = request.into_scoped(group_id.to_string());
            let result = match self.validate_create_request(&request) {
                Ok(()) => {
                    self.persist_create(request, grant.clone(), EventSubscriptionStatus::Pending)
                        .await
                }
                Err(error) => Err(error),
            };
            match result {
                Ok(subscription) => subscription_ids.push(subscription.subscription_id),
                Err(error) => {
                    if !subscription_ids.is_empty() {
                        self.repo
                            .cancel_pending_subscriptions(CancelPendingEventSubscriptions {
                                subscription_ids,
                                actor,
                                reason: "group_provisioning_prepare_failed".to_string(),
                                cancelled_at_ms: (self.now_ms)(),
                                env: self.env.clone(),
                            })
                            .await
                            .map_err(map_repo_error)?;
                    }
                    return Err(error);
                }
            }
        }
        Ok(PreparedGroupEventSubscriptions {
            group_id: group_id.to_string(),
            subscription_ids,
            actor,
        })
    }

    async fn cancel(
        &self,
        prepared: &PreparedGroupEventSubscriptions,
        reason: &str,
    ) -> Result<(), ApplicationError> {
        if prepared.subscription_ids.is_empty() {
            return Ok(());
        }
        self.repo
            .cancel_pending_subscriptions(CancelPendingEventSubscriptions {
                subscription_ids: prepared.subscription_ids.clone(),
                actor: prepared.actor.clone(),
                reason: reason.to_string(),
                cancelled_at_ms: (self.now_ms)(),
                env: self.env.clone(),
            })
            .await
            .map_err(map_repo_error)?;
        Ok(())
    }

    async fn finalize(
        &self,
        prepared: &PreparedGroupEventSubscriptions,
        group: &Group,
        initial_session: Option<&Session>,
    ) -> Result<(), ApplicationError> {
        let runtime = self.group_provisioning.as_ref().ok_or_else(|| {
            ApplicationError::internal("Group Event provisioning finalization is not configured")
        })?;
        if group.id != prepared.group_id {
            return Err(ApplicationError::internal(
                "Prepared Event Subscription scope does not match the created Group",
            ));
        }
        if initial_session.is_some_and(|session| session.group_id != group.id) {
            return Err(ApplicationError::internal(
                "Initial Session does not belong to the created Group",
            ));
        }
        let finalized_at_ms = (self.now_ms)();
        let retention_until_ms = u64::from(runtime.event_retention_days)
            .checked_mul(MILLIS_PER_DAY)
            .and_then(|duration| finalized_at_ms.checked_add(duration))
            .ok_or_else(|| ApplicationError::internal("Event retention timestamp overflow"))?;
        let recorded_at = timestamp(finalized_at_ms)?;
        let events = group_creation_events(
            group,
            initial_session,
            &prepared.actor,
            &recorded_at,
            retention_until_ms,
            &self.env,
        )?;
        runtime
            .groups
            .finalize_provisioning(FinalizeGroupProvisioning {
                group_id: group.id.clone(),
                env: self.env.clone(),
                subscription_ids: prepared.subscription_ids.clone(),
                events,
                actor: prepared.actor.clone(),
                finalized_at_ms,
            })
            .await
            .map_err(|error| {
                ApplicationError::internal(format!(
                    "Group Event provisioning finalization failed: {error}"
                ))
            })
    }

    async fn recover_pending(
        &self,
        group_id: &str,
    ) -> Result<PreparedGroupEventSubscriptions, ApplicationError> {
        let subscriptions = self
            .repo
            .list_subscriptions(ListEventSubscriptionRecords {
                scope: Some(EventSubscriptionScope {
                    scope_type: EventSubscriptionScopeType::Group,
                    id: group_id.to_string(),
                }),
                status: Some(EventSubscriptionStatus::Pending),
                after_subscription_id: None,
                limit: 100,
                env: self.env.clone(),
            })
            .await
            .map_err(map_repo_error)?;
        Ok(PreparedGroupEventSubscriptions {
            group_id: group_id.to_string(),
            subscription_ids: subscriptions
                .into_iter()
                .map(|subscription| subscription.subscription_id)
                .collect(),
            actor: group_provisioning_recovery_actor(),
        })
    }

    async fn list_pending_groups(
        &self,
    ) -> Result<Vec<PendingGroupEventSubscriptions>, ApplicationError> {
        let mut grouped = BTreeMap::<String, (u64, Vec<String>)>::new();
        let mut after_subscription_id = None;
        loop {
            let page = self
                .repo
                .list_subscriptions(ListEventSubscriptionRecords {
                    scope: None,
                    status: Some(EventSubscriptionStatus::Pending),
                    after_subscription_id: after_subscription_id.clone(),
                    limit: 100,
                    env: self.env.clone(),
                })
                .await
                .map_err(map_repo_error)?;
            let page_len = page.len();
            after_subscription_id = page
                .last()
                .map(|subscription| subscription.subscription_id.clone());
            for subscription in page {
                if subscription.scope.scope_type != EventSubscriptionScopeType::Group {
                    continue;
                }
                let group_id = subscription.scope.id;
                let entry = grouped
                    .entry(group_id)
                    .or_insert_with(|| (subscription.created_at_ms, Vec::new()));
                entry.0 = entry.0.min(subscription.created_at_ms);
                entry.1.push(subscription.subscription_id);
            }
            if page_len < 100 {
                break;
            }
        }
        let actor = group_provisioning_recovery_actor();
        Ok(grouped
            .into_iter()
            .map(
                |(group_id, (created_at_ms, subscription_ids))| PendingGroupEventSubscriptions {
                    prepared: PreparedGroupEventSubscriptions {
                        group_id,
                        subscription_ids,
                        actor: actor.clone(),
                    },
                    created_at_ms,
                },
            )
            .collect())
    }

    async fn load_activated(
        &self,
        prepared: &PreparedGroupEventSubscriptions,
    ) -> Result<Vec<EventSubscription>, ApplicationError> {
        let mut subscriptions = Vec::with_capacity(prepared.subscription_ids.len());
        for subscription_id in &prepared.subscription_ids {
            let (record, revision) = self.load_subscription(subscription_id).await?;
            if record.status != EventSubscriptionStatus::Active {
                return Err(ApplicationError::internal(format!(
                    "Group Event Subscription '{subscription_id}' was not activated"
                )));
            }
            subscriptions.push(self.to_view(&record, &revision).await?);
        }
        Ok(subscriptions)
    }
}

fn group_provisioning_recovery_actor() -> EventActor {
    EventActor {
        actor_type: EventActorType::System,
        id: "group-provisioning-reconciler".to_string(),
        display_name: None,
    }
}

fn group_creation_events(
    group: &Group,
    initial_session: Option<&Session>,
    actor: &EventActor,
    recorded_at: &str,
    retention_until_ms: u64,
    env: &str,
) -> Result<Vec<AppendEventRecord>, ApplicationError> {
    let group_event_id = stable_creation_event_id("group.created", &group.id);
    let mut group_data = BTreeMap::new();
    group_data.insert(
        "group_kind".to_string(),
        json!(group_kind_name(group.group_kind)),
    );
    group_data.insert(
        "strategy".to_string(),
        json!(group_strategy_name(group.group_strategy)),
    );
    group_data.insert(
        "name".to_string(),
        json!(group.label.as_deref().unwrap_or(group.id.as_str())),
    );
    group_data.insert("status".to_string(), json!(group_status_name(group.status)));
    group_data.insert("version".to_string(), json!(group.version));
    let mut events = vec![AppendEventRecord {
        event: NewEvent {
            event_id: group_event_id.clone(),
            event_type: "group.created".to_string(),
            schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
            producer: "bcs.group.provisioning".to_string(),
            producer_key: format!("group.created:{}:v{}", group.id, group.version),
            occurred_at: timestamp(group.created_at)?,
            subject: EventSubject {
                subject_type: "group".to_string(),
                id: group.id.clone(),
            },
            scope: EventScope {
                group_id: Some(group.id.clone()),
                ..EventScope::default()
            },
            stream_key: format!("group:{}", group.id),
            actor: Some(actor.clone()),
            correlation_id: Some(format!("group-create:{}", group.id)),
            causation_event_id: None,
            trace_id: None,
            data: group_data,
        },
        recorded_at: recorded_at.to_string(),
        retention_until_ms,
        env: env.to_string(),
    }];

    if let Some(session) = initial_session {
        let version = u64::try_from(session.activation_count).map_err(|_| {
            ApplicationError::internal("Initial Session activation count is negative")
        })?;
        let mut session_data = BTreeMap::new();
        session_data.insert(
            "session_kind".to_string(),
            json!(session_kind_name(session.session_kind)),
        );
        session_data.insert(
            "status".to_string(),
            json!(session_status_name(session.status)),
        );
        session_data.insert(
            "created_by".to_string(),
            json!(
                session
                    .created_by
                    .as_deref()
                    .or(session.caller_id.as_deref())
                    .unwrap_or(actor.id.as_str())
            ),
        );
        session_data.insert("version".to_string(), json!(version));
        session_data.insert("initial".to_string(), json!(true));
        events.push(AppendEventRecord {
            event: NewEvent {
                event_id: stable_creation_event_id("session.created", &session.id),
                event_type: "session.created".to_string(),
                schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
                producer: "bcs.group.provisioning".to_string(),
                producer_key: format!("session.created:{}:v{version}", session.id),
                occurred_at: timestamp(session.created_at)?,
                subject: EventSubject {
                    subject_type: "session".to_string(),
                    id: session.id.clone(),
                },
                scope: EventScope {
                    group_id: Some(group.id.clone()),
                    session_id: Some(session.id.clone()),
                    ..EventScope::default()
                },
                stream_key: format!("session:{}", session.id),
                actor: Some(actor.clone()),
                correlation_id: Some(format!("group-create:{}", group.id)),
                causation_event_id: Some(group_event_id),
                trace_id: None,
                data: session_data,
            },
            recorded_at: recorded_at.to_string(),
            retention_until_ms,
            env: env.to_string(),
        });
    }
    Ok(events)
}

fn stable_creation_event_id(event_type: &str, subject_id: &str) -> String {
    let digest = hash_text(&format!("{event_type}:{subject_id}"));
    format!("evt_{}", &digest[..32])
}

fn group_kind_name(kind: GroupKind) -> &'static str {
    match kind {
        GroupKind::Normal => "normal",
        GroupKind::Dm => "dm",
    }
}

fn group_strategy_name(strategy: GroupStrategy) -> &'static str {
    match strategy {
        GroupStrategy::Chat => "chat",
        GroupStrategy::ManagerWorker => "manager_worker",
        GroupStrategy::StateMachine => "state_machine",
    }
}

fn group_status_name(status: GroupStatus) -> &'static str {
    match status {
        GroupStatus::Active => "active",
        GroupStatus::Completed => "completed",
        GroupStatus::Error => "error",
        GroupStatus::Closed => "closed",
        GroupStatus::Inactive => "inactive",
    }
}

fn session_kind_name(kind: SessionKind) -> &'static str {
    match kind {
        SessionKind::Chat => "chat",
        SessionKind::ServiceInvocation => "service_invocation",
    }
}

fn session_status_name(status: SessionStatus) -> &'static str {
    match status {
        SessionStatus::Running => "running",
        SessionStatus::Completed => "completed",
    }
}

fn patch_is_empty(patch: &bcs_service_api::application::v1::PatchEventSubscriptionRequest) -> bool {
    patch.name.is_none()
        && patch.event_filters.is_none()
        && patch.payload.is_none()
        && patch.sink.is_none()
        && patch.status.is_none()
}

fn validate_webhook_url(
    raw_url: &str,
    policy: &EventSubscriptionPolicy,
) -> Result<(), ApplicationError> {
    if raw_url.is_empty() || raw_url.len() > MAX_WEBHOOK_URL_BYTES {
        return Err(ApplicationError::invalid_webhook_url(
            "Webhook URL length is invalid",
        ));
    }
    let url = Url::parse(raw_url)
        .map_err(|_| ApplicationError::invalid_webhook_url("Webhook URL is invalid"))?;
    let http_loopback_allowed = url.scheme() == "http"
        && policy.allow_http_loopback
        && is_loopback_host(&url);
    let http_private_endpoint_allowed = url.scheme() == "http"
        && url.host_str().is_some_and(|host| {
            url.port_or_known_default().is_some_and(|port| {
                policy
                    .private_endpoint_allowlist
                    .iter()
                    .any(|entry| entry.matches_host_and_port(host, port))
            })
        });
    if (url.scheme() != "https" && !http_loopback_allowed && !http_private_endpoint_allowed)
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(ApplicationError::invalid_webhook_url(
            "Webhook URL violates endpoint policy",
        ));
    }
    let standard_port = match url.scheme() {
        "https" => 443,
        "http" => 80,
        _ => 0,
    };
    if !policy.allow_non_standard_ports
        && url.port_or_known_default() != Some(standard_port)
        && !url.host_str().is_some_and(|host| {
            url.port_or_known_default().is_some_and(|port| {
                policy
                    .private_endpoint_allowlist
                    .iter()
                    .any(|entry| entry.matches_host_and_port(host, port))
            })
        })
    {
        return Err(ApplicationError::invalid_webhook_url(
            "Webhook URL violates endpoint policy",
        ));
    }
    Ok(())
}

fn is_loopback_host(url: &Url) -> bool {
    match url.host() {
        Some(url::Host::Domain(domain)) => domain.eq_ignore_ascii_case("localhost"),
        Some(url::Host::Ipv4(address)) => address.is_loopback(),
        Some(url::Host::Ipv6(address)) => address.is_loopback(),
        None => false,
    }
}

fn validate_request_timeout(timeout_ms: u64, max_timeout_ms: u64) -> Result<(), ApplicationError> {
    if !(MIN_REQUEST_TIMEOUT_MS..=max_timeout_ms).contains(&timeout_ms) {
        return Err(ApplicationError::invalid(
            "invalid_webhook_timeout",
            "Webhook timeout is outside the configured range",
        ));
    }
    Ok(())
}

fn redact_endpoint(raw_url: &str) -> Result<EventWebhookEndpointView, ApplicationError> {
    let url = Url::parse(raw_url)
        .map_err(|_| ApplicationError::internal("stored Webhook URL is invalid"))?;
    let host = url
        .host_str()
        .ok_or_else(|| ApplicationError::internal("stored Webhook URL has no host"))?;
    Ok(EventWebhookEndpointView {
        scheme: url.scheme().to_string(),
        host: host.to_string(),
        path_hash: hash_text(url.path()),
    })
}

fn delivery_summary(
    delivery: &EventDeliveryRecord,
) -> Result<EventDeliverySummary, ApplicationError> {
    Ok(EventDeliverySummary {
        delivery_id: delivery.delivery_id.clone(),
        event_id: delivery.event_id.clone(),
        event_type: delivery.event_type.clone(),
        subscription_id: delivery.subscription_id.clone(),
        subscription_revision: delivery.subscription_revision,
        stream_key_hash: hash_text(&delivery.stream_key),
        sequence: delivery.sequence,
        status: delivery.status,
        attempt_count: delivery.attempt_count,
        last_http_status: delivery.last_http_status,
        last_error_category: delivery.last_error_category.clone(),
        created_at: timestamp(delivery.created_at_ms)?,
    })
}

fn attempt_summary(
    attempt: &EventDeliveryAttemptRecord,
) -> Result<EventDeliveryAttemptSummary, ApplicationError> {
    let result = match attempt.result {
        EventDeliveryAttemptRecordResult::Success => EventDeliveryAttemptResult::Success,
        EventDeliveryAttemptRecordResult::Retryable => EventDeliveryAttemptResult::Retryable,
        EventDeliveryAttemptRecordResult::Terminal => EventDeliveryAttemptResult::Terminal,
    };
    Ok(EventDeliveryAttemptSummary {
        attempt_no: attempt.attempt_no,
        started_at: timestamp(attempt.started_at_ms)?,
        completed_at: timestamp(attempt.completed_at_ms)?,
        latency_ms: attempt.latency_ms,
        result,
        http_status: attempt.http_status,
        error_category: attempt.error_category.clone(),
    })
}

fn test_envelope(
    subscription: &EventSubscriptionRecord,
    event_id: &str,
    now_ms: u64,
) -> Result<EventEnvelope, ApplicationError> {
    let occurred_at = timestamp(now_ms)?;
    Ok(EventEnvelope {
        spec_version: EVENT_SPEC_VERSION.to_string(),
        event_id: event_id.to_string(),
        event_type: "event_subscription.test".to_string(),
        schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
        source: EVENT_SOURCE.to_string(),
        occurred_at: occurred_at.clone(),
        recorded_at: occurred_at,
        subject: EventSubject {
            subject_type: "event_subscription".to_string(),
            id: subscription.subscription_id.clone(),
        },
        scope: event_scope(&subscription.scope),
        stream: EventStream {
            key: format!("event-subscription-test:{}", subscription.subscription_id),
            sequence: 1,
        },
        actor: None,
        correlation_id: None,
        causation_event_id: None,
        trace_id: None,
        data: BTreeMap::from([("test".to_string(), json!(true))]),
    })
}

fn event_scope(scope: &EventSubscriptionScope) -> EventScope {
    let mut event_scope = EventScope::default();
    match scope.scope_type {
        EventSubscriptionScopeType::Group => event_scope.group_id = Some(scope.id.clone()),
    }
    event_scope
}

fn timestamp(milliseconds: u64) -> Result<String, ApplicationError> {
    let milliseconds = i64::try_from(milliseconds)
        .map_err(|_| ApplicationError::internal("timestamp is outside supported range"))?;
    Utc.timestamp_millis_opt(milliseconds)
        .single()
        .map(|value| value.to_rfc3339_opts(SecondsFormat::Millis, true))
        .ok_or_else(|| ApplicationError::internal("timestamp is outside supported range"))
}

pub(crate) fn system_now_ms_for_workers() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn hash_text(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

fn map_repo_error(error: EventRepoError) -> ApplicationError {
    match error {
        EventRepoError::InvalidInput(message) => {
            ApplicationError::invalid("invalid_event_repository_input", message)
        }
        EventRepoError::Conflict(message) => {
            ApplicationError::event_subscription_revision_conflict(message)
        }
        EventRepoError::LimitReached(message) => {
            ApplicationError::event_subscription_limit_reached(message)
        }
        EventRepoError::NotFound(_) => {
            ApplicationError::event_subscription_not_found("Event Subscription not found")
        }
        internal @ (EventRepoError::CausationViolation(_)
        | EventRepoError::LeaseLost(_)
        | EventRepoError::Unsupported(_)
        | EventRepoError::Storage(_)) => {
            ApplicationError::internal(format!("Event repository failed: {internal}"))
        }
    }
}

fn map_revision_repo_error(error: EventRepoError) -> ApplicationError {
    match error {
        EventRepoError::Conflict(message) => {
            ApplicationError::event_subscription_revision_conflict(message)
        }
        other => map_repo_error(other),
    }
}

fn map_replay_repo_error(error: EventRepoError) -> ApplicationError {
    match error {
        EventRepoError::Conflict(message) | EventRepoError::InvalidInput(message) => {
            ApplicationError::event_delivery_not_replayable(message)
        }
        EventRepoError::NotFound(_) => {
            ApplicationError::event_delivery_not_found("Event Delivery not found")
        }
        other => map_repo_error(other),
    }
}

fn hide_visibility_error(error: ApplicationError) -> ApplicationError {
    match error {
        ApplicationError::Forbidden(_)
        | ApplicationError::ForbiddenCode { .. }
        | ApplicationError::NotFound { .. }
        | ApplicationError::Unauthenticated => {
            ApplicationError::event_subscription_not_found("Event Subscription not found")
        }
        other => other,
    }
}

fn is_visibility_denial(error: &ApplicationError) -> bool {
    matches!(
        error,
        ApplicationError::Forbidden(_)
            | ApplicationError::ForbiddenCode { .. }
            | ApplicationError::NotFound { .. }
            | ApplicationError::Unauthenticated
    )
}
