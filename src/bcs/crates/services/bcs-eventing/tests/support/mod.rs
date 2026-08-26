#![allow(dead_code)]

use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};

use async_trait::async_trait;
use bcs_config_api::EventingConfig;
use bcs_event_store::MemoryEventStore;
use bcs_eventing::{
    AuthorizedEventSubscriptionScope, EventCatalog, EventSubscriptionApplicationService,
    EventSubscriptionAuthorizationAction, EventSubscriptionAuthorizer, EventSubscriptionPolicy,
};
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
    CreateEventSubscription, CreateEventSubscriptionRequest, EventPayload, EventSinkInput,
};
use bcs_service_api::port::{
    EventDeliveryAttemptMetric, EventDeliveryDisposition, EventDeliveryError, EventDeliveryPort,
    EventDeliveryRequest, EventDeliveryResponse, EventErrorCategory, EventProductionMetric,
    EventingInstrumentationPort, WebhookGuardBlockReason,
};
use bcs_service_api::types::{
    EventActor, EventActorType, EventPayloadMode, EventSubscriptionScope,
    EventSubscriptionScopeType,
};

pub const NOW_MS: u64 = 1_787_028_000_000;

pub struct FixedAuthorizer {
    pub allowed: AtomicBool,
    pub full_payload_allowed: AtomicBool,
}

impl FixedAuthorizer {
    pub fn allow(full_payload_allowed: bool) -> Self {
        Self {
            allowed: AtomicBool::new(true),
            full_payload_allowed: AtomicBool::new(full_payload_allowed),
        }
    }
}

#[async_trait]
impl EventSubscriptionAuthorizer for FixedAuthorizer {
    async fn authorize(
        &self,
        _caller: &AuthenticatedCaller,
        _scope: &EventSubscriptionScope,
        _action: EventSubscriptionAuthorizationAction,
    ) -> Result<AuthorizedEventSubscriptionScope, ApplicationError> {
        if !self.allowed.load(Ordering::SeqCst) {
            return Err(ApplicationError::event_subscription_forbidden(
                "scope manager required",
            ));
        }
        Ok(AuthorizedEventSubscriptionScope {
            actor: EventActor {
                actor_type: EventActorType::Human,
                id: "human_owner".to_string(),
                display_name: None,
            },
            full_payload_allowed: self.full_payload_allowed.load(Ordering::SeqCst),
        })
    }
}

pub struct CaptureDelivery {
    pub requests: Mutex<Vec<EventDeliveryRequest>>,
    pub response: Mutex<EventDeliveryResponse>,
}

impl Default for CaptureDelivery {
    fn default() -> Self {
        Self {
            requests: Mutex::new(Vec::new()),
            response: Mutex::new(EventDeliveryResponse {
                disposition: EventDeliveryDisposition::Succeeded,
                http_status: Some(204),
                retry_after_ms: None,
                response_bytes_observed: 0,
                error_category: None,
                error_summary: None,
            }),
        }
    }
}

#[async_trait]
impl EventDeliveryPort for CaptureDelivery {
    async fn deliver(
        &self,
        request: EventDeliveryRequest,
    ) -> Result<EventDeliveryResponse, EventDeliveryError> {
        self.requests
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push(request);
        Ok(self
            .response
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone())
    }
}

#[derive(Default)]
pub struct CaptureMetrics {
    pub attempts: Mutex<Vec<EventDeliveryAttemptMetric>>,
    pub fanout_failures: Mutex<Vec<EventErrorCategory>>,
}

#[async_trait]
impl EventingInstrumentationPort for CaptureMetrics {
    async fn event_produced(&self, _metric: EventProductionMetric) {}

    async fn fanout_failed(&self, category: EventErrorCategory) {
        self.fanout_failures
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push(category);
    }

    async fn delivery_attempted(&self, metric: EventDeliveryAttemptMetric) {
        self.attempts
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push(metric);
    }

    async fn webhook_guard_blocked(&self, _reason: WebhookGuardBlockReason) {}
}

pub struct Harness {
    pub service: EventSubscriptionApplicationService,
    pub repo: Arc<MemoryEventStore>,
    pub groups: Arc<GroupCore>,
    pub authorizer: Arc<FixedAuthorizer>,
    pub delivery: Arc<CaptureDelivery>,
}

pub fn harness(full_payload_allowed: bool) -> Harness {
    harness_with_eventing_config(
        full_payload_allowed,
        EventingConfig {
            enabled: true,
            ..EventingConfig::default()
        },
    )
}

pub fn harness_with_eventing_config(
    full_payload_allowed: bool,
    eventing_config: EventingConfig,
) -> Harness {
    let repo = Arc::new(MemoryEventStore::new());
    let groups = Arc::new(GroupCore::with_repo(Arc::new(
        MemoryGroupRepo::new().with_event_store(repo.clone(), "test"),
    )));
    let authorizer = Arc::new(FixedAuthorizer::allow(full_payload_allowed));
    let delivery = Arc::new(CaptureDelivery::default());
    let id_counter = Arc::new(AtomicUsize::new(0));
    let service = EventSubscriptionApplicationService::new(
        repo.clone(),
        delivery.clone(),
        authorizer.clone(),
        Arc::new(EventCatalog::load_embedded().expect("embedded Event Catalog")),
        EventSubscriptionPolicy::from(&eventing_config),
        "test",
    )
    .with_group_provisioning(groups.clone(), 30)
    .with_runtime(
        Arc::new(|| NOW_MS),
        Arc::new(move |prefix| {
            let sequence = id_counter.fetch_add(1, Ordering::SeqCst) + 1;
            format!("{prefix}_{sequence}")
        }),
    );
    Harness {
        service,
        repo,
        groups,
        authorizer,
        delivery,
    }
}

pub fn caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-1".to_string()),
        user: Some(AuthenticatedUserIdentity {
            id: "owner".to_string(),
            username: "owner".to_string(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

pub fn bot_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-1".to_string()),
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: "bot-owner".to_string(),
            owner_id: "owner".to_string(),
            app_id: 7,
            agent_code: "agent-bot-owner".to_string(),
        }),
        app: None,
        access_key: None,
    }
}

pub fn group_scope() -> EventSubscriptionScope {
    EventSubscriptionScope {
        scope_type: EventSubscriptionScopeType::Group,
        id: "group-1".to_string(),
    }
}

pub fn create_command(
    filters: Vec<String>,
    payload_mode: EventPayloadMode,
) -> CreateEventSubscription {
    CreateEventSubscription {
        caller: caller(),
        request: CreateEventSubscriptionRequest {
            name: "workflow-observer".to_string(),
            scope: group_scope(),
            event_filters: filters,
            payload: EventPayload { mode: payload_mode },
            sink: EventSinkInput::Webhook {
                url: "https://events.example.com/bcs/events".to_string(),
                request_timeout_ms: Some(5_000),
            },
        },
    }
}
