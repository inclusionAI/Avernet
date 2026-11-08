//! Event Subscription authority cutover (bot-authority
//! plan Task 6.5, entry-map row 24): the standalone
//! authorizer reads the CURRENT Group management edge
//! from the strict authorization snapshot (spec §8.3
//! 资源快照) and judges the Human→Bot leg through the
//! live authority hook; the `list_bots_by_creator`
//! probe and the `bot.owner_id` claim comparison are
//! gone. Webhook delivery stays a PERSISTENT asset
//! (spec §8.4): losing the creating owner's edge keeps
//! delivery running — only management operations
//! consult current authority. The inline provisioner
//! resolves its mixed credentials through the same
//! hook (including the eventing-enabled,
//! subscriptions-empty prepare).

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_event_store::MemoryEventStore;
use bcs_eventing::{
    CoreEventSubscriptionAuthorizer, EventSubscriptionApplicationService,
    EventSubscriptionAuthorizationAction, EventSubscriptionAuthorizer,
    EventSubscriptionPolicy,
};
use bcs_group::GroupCore;
use bcs_service_api::application::v1::{
    AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
    CreateEventSubscription, CreateEventSubscriptionRequest, EventPayload,
    EventSinkInput, EventSubscriptionService,
    GroupEventSubscriptionProvisioner, InlineGroupEventSubscriptionRequest,
    PatchEventSubscription, PatchEventSubscriptionRequest,
    EventSubscriptionDesiredStatus,
};
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::{
    GroupCoreService, Participant, ParticipantRole,
    types::{
        EventActorType, EventPayloadMode, EventSubscriptionScope,
        EventSubscriptionScopeType,
    },
};
type GroupCoreFixture = Arc<GroupCore>;
use bcs_test_support::{LazySeedAuthorityHook, NoopBotRegistryCoreService};

/// The REAL authority chain; edges seed on the first read.
fn authority_chain() -> Arc<LazySeedAuthorityHook> {
    bcs_test_support::lazy_authority_chain().hook
}

async fn groups_core() -> GroupCoreFixture {
    let groups: Arc<GroupCore> = Arc::new(GroupCore::memory());
    let core = groups.clone();
    core.upsert(bcs_service_api::Group::new(
            "group-1",
            "bot-driver",
            vec![Participant::human(
                "human_originator",
                ParticipantRole::Observer,
            )],
        ))
        .await
        .expect("store group");
    groups
}

fn authorizer(
    groups: &GroupCoreFixture,
    hook: Arc<dyn BotAuthorityPort>,
) -> Arc<CoreEventSubscriptionAuthorizer> {
    Arc::new(CoreEventSubscriptionAuthorizer::new(
        groups.clone() as Arc<dyn GroupCoreService>,
        Arc::new(NoopBotRegistryCoreService),
        hook,
    ))
}

fn service(
    groups: &GroupCoreFixture,
    authorizer: Arc<dyn EventSubscriptionAuthorizer>,
    hook: Option<Arc<dyn BotAuthorityPort>>,
) -> EventSubscriptionApplicationService {
    EventSubscriptionApplicationService::new(
        Arc::new(MemoryEventStore::new()) as _,
        Arc::new(NoopDelivery),
        authorizer,
        Arc::new(bcs_eventing::EventCatalog::load_embedded().expect("catalog")),
        EventSubscriptionPolicy::from(&bcs_config_api::EventingConfig {
            enabled: true,
            ..bcs_config_api::EventingConfig::default()
        }),
        "test",
    )
    .with_group_provisioning(
        groups.clone() as Arc<dyn GroupCoreService>,
        30,
    )
    .with_bot_authority_hook(hook)
}

fn user_caller(user_id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-1".to_string()),
        user: Some(AuthenticatedUserIdentity {
            id: user_id.to_string(),
            username: user_id.to_string(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn hybrid_caller(user_id: &str, bot_id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: bot_id.to_string(),
            owner_id: user_id.to_string(),
            app_id: 7,
            agent_code: format!("agent-{bot_id}"),
        }),
        ..user_caller(user_id)
    }
}

fn group_scope() -> EventSubscriptionScope {
    EventSubscriptionScope {
        scope_type: EventSubscriptionScopeType::Group,
        id: "group-1".to_string(),
    }
}

fn create_command(caller: AuthenticatedCaller) -> CreateEventSubscription {
    CreateEventSubscription {
        caller,
        request: CreateEventSubscriptionRequest {
            name: "authority-matrix".to_string(),
            scope: group_scope(),
            event_filters: vec!["group.*".to_string()],
            payload: EventPayload {
                mode: EventPayloadMode::MetadataOnly,
            },
            sink: EventSinkInput::Webhook {
                url: "https://events.example.com/bcs/events".to_string(),
                request_timeout_ms: Some(5_000),
            },
        },
    }
}

fn disable_patch(
    caller: AuthenticatedCaller,
    subscription_id: &str,
    expected_revision: u64,
) -> PatchEventSubscription {
    PatchEventSubscription {
        caller,
        subscription_id: subscription_id.to_string(),
        expected_revision,
        patch: PatchEventSubscriptionRequest {
            name: None,
            event_filters: None,
            payload: None,
            sink: None,
            status: Some(EventSubscriptionDesiredStatus::Disabled),
        },
    }
}

#[tokio::test]
async fn authorize_follows_the_current_control_matrix() {
    let groups = groups_core().await;
    let authority = authority_chain();
    authority.seed_owner("bot-driver", "alice");
    authority.seed_manager("bot-driver", "morgan");

    let authorizer = authorizer(&groups, authority.clone());

    // A(owner): the actor is the SERVER-DERIVED Bot the
    // Human controls (no caller-claimed identity).
    let grant = authorizer
        .authorize(&user_caller("alice"), &group_scope(), EventSubscriptionAuthorizationAction::Create)
        .await
        .expect("the CURRENT owner manages the scope");
    assert_eq!(grant.actor.actor_type, EventActorType::Bot);
    assert_eq!(grant.actor.id, "bot-driver");

    // M(manager): the same judgment through the manager edge.
    let grant = authorizer
        .authorize(&user_caller("morgan"), &group_scope(), EventSubscriptionAuthorizationAction::Create)
        .await
        .expect("the CURRENT manager manages the scope");
    assert_eq!(grant.actor.actor_type, EventActorType::Bot);

    // X(unrelated): a PROVISIONED Human without any
    // edge (the authority surface only knows
    // provisioning users, spec §5.2) is denied on the
    // same live chain.
    authority.seed_manager("bot-driver", "xavier");
    authority.revoke_manager("bot-driver", "xavier");
    let denied = authorizer
        .authorize(&user_caller("xavier"), &group_scope(), EventSubscriptionAuthorizationAction::Create)
        .await
        .expect_err("an unrelated Human is denied");
    assert_eq!(denied.code(), "event_subscription_forbidden");

    // Manager withdrawal closes the entry on the SAME
    // authorizer — the fallback-free deny.
    authority.revoke_manager("bot-driver", "morgan");
    let denied = authorizer
        .authorize(&user_caller("morgan"), &group_scope(), EventSubscriptionAuthorizationAction::Create)
        .await
        .expect_err("the withdrawn manager is fallback-free denied");
    assert_eq!(denied.code(), "event_subscription_forbidden");

    // Hybrid User+Bot credentials face the same live edge.
    let denied = authorizer
        .authorize(&hybrid_caller("xavier", "bot-driver"), &group_scope(), EventSubscriptionAuthorizationAction::Create)
        .await
        .expect_err("an edgeless hybrid credential is denied");
    assert_eq!(denied.code(), "event_subscription_forbidden");
}

#[tokio::test]
async fn handover_moves_the_management_judgment_of_the_persistent_asset() {
    let groups = groups_core().await;
    let authority = authority_chain();
    authority.seed_owner("bot-driver", "alice");
    let authorizer = authorizer(&groups, authority.clone());
    let svc = service(&groups, authorizer.clone(), Some(authority.clone()));

    // A creates a webhook asset.
    let created = svc
        .create(create_command(user_caller("alice")))
        .await
        .expect("the CURRENT owner creates the asset");

    // A→B handover on the driver Bot: A's edge is gone, the
    // webhook asset persists (spec §8.4) and the MANAGEMENT
    // op re-checks CURRENT authority.
    authority.revoke_owner("bot-driver", "alice");
    authority.seed_owner("bot-driver", "bob");

    let denied = svc
        .patch(disable_patch(
            user_caller("alice"),
            &created.subscription_id,
            created.revision,
        ))
        .await
        .expect_err("the revoked creator cannot manage the persistent asset");
    assert_eq!(denied.code(), "event_subscription_forbidden");

    let patched = svc
        .patch(disable_patch(
            user_caller("bob"),
            &created.subscription_id,
            created.revision,
        ))
        .await
        .expect("the CURRENT resource administrator manages the asset");
    assert_eq!(
        patched.status,
        bcs_service_api::types::EventSubscriptionStatus::Disabled
    );
}

#[tokio::test]
async fn webhook_delivery_continues_after_the_creator_loses_the_edge() {
    let groups = groups_core().await;
    let authority = authority_chain();
    authority.seed_owner("bot-driver", "alice");
    let repo = Arc::new(MemoryEventStore::new());
    let authorizer = authorizer(&groups, authority.clone());
    let delivery = Arc::new(CaptureDelivery::default());
    let svc = EventSubscriptionApplicationService::new(
        repo.clone() as _,
        delivery.clone(),
        authorizer,
        Arc::new(bcs_eventing::EventCatalog::load_embedded().expect("catalog")),
        EventSubscriptionPolicy::from(&bcs_config_api::EventingConfig {
            enabled: true,
            ..bcs_config_api::EventingConfig::default()
        }),
        "test",
    )
    .with_group_provisioning(groups.clone(), 30);
    let created = svc
        .create(create_command(user_caller("alice")))
        .await
        .expect("the CURRENT owner creates the asset");

    // The creator loses the management edge. Delivery —
    // the durable asset's HTTP dispatch — consults NO
    // authority on its path (spec §8.4).
    authority.revoke_owner("bot-driver", "alice");

    append_group_created_event(&repo).await;
    let fanout = bcs_eventing::EventFanoutWorker::new(
        repo.clone(),
        Arc::new(NoopMetrics),
        Arc::new(bcs_eventing::EventCatalog::load_embedded().expect("catalog")),
        "test",
        1_000,
        10,
        262_144,
    );
    let fanned = fanout.run_once("authority-test").await.expect("fanout");
    assert_eq!(fanned, 1, "the fanout still sees the subscription");
    let dispatcher = bcs_eventing::EventDispatcher::new(
        repo.clone(),
        delivery.clone(),
        Arc::new(NoopMetrics),
        bcs_eventing::EventRetryPolicy::from(
            &bcs_config_api::EventingConfig::default().retry,
        ),
        "test",
        1_000,
        10,
        2,
        2,
    );
    dispatcher.run_once("authority-test").await.expect("dispatch");
    assert_eq!(
        delivery
            .requests
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .len(),
        1,
        "webhook is a persistent integration asset: delivery continues"
    );
    let _ = &created;
}

#[tokio::test]
async fn inline_provisioner_resolves_through_the_authority_hook() {
    let groups = groups_core().await;
    let authority = authority_chain();
    authority.seed_owner("bot-driver", "alice");
    let authorizer = authorizer(&groups, authority.clone());
    let svc = service(&groups, authorizer.clone(), Some(authority.clone()));

    // Eventing on and subscriptions EMPTY: the prepare still
    // resolves the actor through the hook (6.5 inline path).
    let prepared = svc
        .prepare(
            &hybrid_caller("alice", "bot-driver"),
            "group-1",
            Vec::<InlineGroupEventSubscriptionRequest>::new(),
        )
        .await
        .expect("the mixed credential resolves through the live edge");
    assert_eq!(prepared.actor.actor_type, EventActorType::Bot);
    assert_eq!(prepared.actor.id, "bot-driver");
    assert!(prepared.subscription_ids.is_empty());

    // Edgeless hybrid credential: fail closed. The
    // provisioned-but-edgeless Human resolves to no
    // authorized principal (the A→B handover
    // semantics are pinned in the other test).
    authority.seed_manager("bot-driver", "xavier");
    authority.revoke_manager("bot-driver", "xavier");
    let denied = svc
        .prepare(
            &hybrid_caller("xavier", "bot-driver"),
            "group-1",
            Vec::<InlineGroupEventSubscriptionRequest>::new(),
        )
        .await
        .expect_err("an edgeless hybrid credential denies the inline provisioner");
    assert_eq!(denied.code(), "forbidden");

    // Failing authority read: internal, never a deny-as-403.
    let failing = EventSubscriptionApplicationService::new(
        Arc::new(MemoryEventStore::new()) as _,
        Arc::new(NoopDelivery),
        authorizer.clone(),
        Arc::new(bcs_eventing::EventCatalog::load_embedded().expect("catalog")),
        EventSubscriptionPolicy::from(&bcs_config_api::EventingConfig {
            enabled: true,
            ..bcs_config_api::EventingConfig::default()
        }),
        "test",
    )
    .with_bot_authority_hook(Some(bcs_test_support::failing_authority_hook()));
    let error = failing
        .prepare(
            &hybrid_caller("alice", "bot-driver"),
            "group-1",
            Vec::<InlineGroupEventSubscriptionRequest>::new(),
        )
        .await
        .expect_err("a failing authority read is an internal error");
    assert_eq!(error.code(), "internal_error");

    // Pure Human caller keeps the Human selection (the hook
    // is not consulted for it).
    let human_only = service(&groups, authorizer, None);
    let prepared = human_only
        .prepare(
            &user_caller("alice"),
            "group-1",
            Vec::<InlineGroupEventSubscriptionRequest>::new(),
        )
        .await
        .expect("Human-only selection is unchanged");
    assert_eq!(prepared.actor.actor_type, EventActorType::Human);
}

// ---- test-only ports ----

#[derive(Default)]
struct CaptureDelivery {
    requests: Mutex<Vec<bcs_service_api::port::EventDeliveryRequest>>,
}

#[async_trait]
impl bcs_service_api::port::EventDeliveryPort for CaptureDelivery {
    async fn deliver(
        &self,
        request: bcs_service_api::port::EventDeliveryRequest,
    ) -> Result<
        bcs_service_api::port::EventDeliveryResponse,
        bcs_service_api::port::EventDeliveryError,
    > {
        self.requests
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push(request);
        Ok(bcs_service_api::port::EventDeliveryResponse {
            disposition: bcs_service_api::port::EventDeliveryDisposition::Succeeded,
            http_status: Some(204),
            retry_after_ms: None,
            response_bytes_observed: 0,
            error_category: None,
            error_summary: None,
        })
    }
}

struct NoopDelivery;

#[async_trait]
impl bcs_service_api::port::EventDeliveryPort for NoopDelivery {
    async fn deliver(
        &self,
        _request: bcs_service_api::port::EventDeliveryRequest,
    ) -> Result<
        bcs_service_api::port::EventDeliveryResponse,
        bcs_service_api::port::EventDeliveryError,
    > {
        Ok(bcs_service_api::port::EventDeliveryResponse {
            disposition: bcs_service_api::port::EventDeliveryDisposition::Succeeded,
            http_status: Some(204),
            retry_after_ms: None,
            response_bytes_observed: 0,
            error_category: None,
            error_summary: None,
        })
    }
}

struct NoopMetrics;

#[async_trait]
impl bcs_service_api::port::EventingInstrumentationPort for NoopMetrics {
    async fn event_produced(
        &self,
        _metric: bcs_service_api::port::EventProductionMetric,
    ) {
    }

    async fn fanout_failed(&self, _category: bcs_service_api::port::EventErrorCategory) {}

    async fn delivery_attempted(
        &self,
        _metric: bcs_service_api::port::EventDeliveryAttemptMetric,
    ) {
    }

    async fn webhook_guard_blocked(
        &self,
        _reason: bcs_service_api::port::WebhookGuardBlockReason,
    ) {
    }
}

async fn append_group_created_event(repo: &Arc<MemoryEventStore>) {
    use bcs_service_api::port::repo::{AppendEventRecord, EventRepoPort};
    use bcs_service_api::port::NewEvent;
    use bcs_service_api::types::{
        EVENT_SCHEMA_VERSION_V1, EventScope, EventSubject,
    };
    use std::collections::BTreeMap;

    repo.append_event(AppendEventRecord {
        event: NewEvent {
            event_id: "evt-authority".to_string(),
            event_type: "group.created".to_string(),
            schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
            producer: "authority-cutover-test".to_string(),
            producer_key: "evt-authority".to_string(),
            occurred_at: "2026-08-19T00:00:00.000Z".to_string(),
            subject: EventSubject {
                subject_type: "group".to_string(),
                id: "group-1".to_string(),
            },
            scope: EventScope {
                group_id: Some("group-1".to_string()),
                ..EventScope::default()
            },
            stream_key: "group:group-1".to_string(),
            actor: None,
            correlation_id: None,
            causation_event_id: None,
            trace_id: None,
            data: BTreeMap::new(),
        },
        recorded_at: "2026-08-19T00:00:00.001Z".to_string(),
        retention_until_ms: u64::MAX - 1,
        env: "test".to_string(),
    })
    .await
    .expect("append group event");
}
