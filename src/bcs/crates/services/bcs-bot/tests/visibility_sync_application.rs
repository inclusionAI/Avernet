use std::{sync::Arc, time::Duration};

use async_trait::async_trait;
use bcs_bot::{Bot, BotCore, BotOnboarding, VisibilitySyncCoordinator};
use bcs_service_api::{
    AdminBotOnboardCommand, BotCapabilities, BotManagementService, BotOnboardCommand,
    BotOnboardingService, BotRegistryCoreService, BotVisibilityCommand, Skill,
    port::{VisibilitySyncPort, VisibilitySyncRequest},
};
use bcs_test_support::NoopRelationCoreService;
use tempfile::TempDir;
use tokio::sync::{Mutex, Notify, Semaphore};

#[derive(Default)]
struct RecordingVisibilitySyncPort {
    requests: Mutex<Vec<VisibilitySyncRequest>>,
    request_recorded: Notify,
}

impl RecordingVisibilitySyncPort {
    async fn wait_for_requests(&self, count: usize) {
        tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                let notified = self.request_recorded.notified();
                if self.requests.lock().await.len() >= count {
                    break;
                }
                notified.await;
            }
        })
        .await
        .expect("visibility sync request should arrive");
    }
}

#[async_trait]
impl VisibilitySyncPort for RecordingVisibilitySyncPort {
    async fn sync_visibility(&self, request: VisibilitySyncRequest) {
        self.requests.lock().await.push(request);
        self.request_recorded.notify_waiters();
    }
}

struct BlockingVisibilitySyncPort {
    requests: Mutex<Vec<VisibilitySyncRequest>>,
    request_recorded: Notify,
    release: Semaphore,
}

impl BlockingVisibilitySyncPort {
    fn new() -> Self {
        Self {
            requests: Mutex::new(Vec::new()),
            request_recorded: Notify::new(),
            release: Semaphore::new(0),
        }
    }

    async fn wait_for_requests(&self, count: usize) {
        tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                let notified = self.request_recorded.notified();
                if self.requests.lock().await.len() >= count {
                    break;
                }
                notified.await;
            }
        })
        .await
        .expect("visibility sync request should arrive");
    }
}

#[async_trait]
impl VisibilitySyncPort for BlockingVisibilitySyncPort {
    async fn sync_visibility(&self, request: VisibilitySyncRequest) {
        self.requests.lock().await.push(request);
        self.request_recorded.notify_waiters();

        let permit = self.release.acquire().await.unwrap();
        permit.forget();
    }
}

async fn register_bot(registry: &Arc<BotCore>, bot_uuid: &str, visibility: &str) {
    registry
        .register_with_owner_and_token(
            bot_uuid.to_string(),
            BotCapabilities {
                name: Some(bot_uuid.to_string()),
                summary: Some("Visibility test".to_string()),
                domains: vec!["ops".to_string()],
                skills: vec![Skill::new("monitor")],
                visibility: visibility.to_string(),
                ..BotCapabilities::default()
            },
            "test-owner",
            "test-token",
        )
        .await
        .unwrap();
}

fn onboarding(registry: Arc<BotCore>, visibility_sync: VisibilitySyncCoordinator) -> BotOnboarding {
    BotOnboarding::new(
        registry,
        Arc::new(NoopRelationCoreService),
        false,
        Some("protected".to_string()),
    )
    .with_visibility_sync(visibility_sync)
}

#[tokio::test]
async fn visibility_sync_application_services_run_after_successful_mutations() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_uuid in ["bot-visibility", "bot-onboard", "bot-admin"] {
        register_bot(&registry, bot_uuid, "public").await;
    }
    let sync = Arc::new(RecordingVisibilitySyncPort::default());
    let coordinator = VisibilitySyncCoordinator::new(registry.clone(), sync.clone());
    let bot = Bot::new(registry.clone()).with_visibility_sync(coordinator.clone());
    let onboarding = onboarding(registry, coordinator);

    bot.set_visibility(BotVisibilityCommand {
        caller_actor_id: Some("bot-visibility".to_string()),
        bot_id: "bot-visibility".to_string(),
        visibility: "private".to_string(),
    })
    .await
    .unwrap();
    onboarding
        .onboard_bot(BotOnboardCommand {
            bot_uuid: "bot-onboard".to_string(),
            name: "Onboarded".to_string(),
            summary: None,
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
            binding_channels: None,
            agent_code: None,
            agent_token: None,
            actor_identity: None,
        })
        .await
        .unwrap();
    onboarding
        .admin_onboard_bot(AdminBotOnboardCommand {
            bot_uuid: "bot-admin".to_string(),
            name: Some("Admin onboarded".to_string()),
            summary: None,
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
            binding_channels: None,
            actor_identity: None,
        })
        .await
        .unwrap();

    sync.wait_for_requests(3).await;
    let requests = sync.requests.lock().await;
    assert!(requests.iter().any(|request| {
        request.bot_uuid == "bot-visibility"
            && request.capabilities.visibility == "private"
            && request.capabilities.summary.as_deref() == Some("Visibility test")
            && request.capabilities.domains == ["ops"]
            && request.capabilities.skills == [Skill::new("monitor")]
    }));
    assert!(requests.iter().any(|request| {
        request.bot_uuid == "bot-onboard"
            && request.capabilities.name.as_deref() == Some("Onboarded")
    }));
    assert!(requests.iter().any(|request| {
        request.bot_uuid == "bot-admin"
            && request.capabilities.name.as_deref() == Some("Admin onboarded")
    }));
}

#[tokio::test]
async fn set_visibility_waits_for_sync_completion() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "bot-visible", "public").await;
    let sync = Arc::new(BlockingVisibilitySyncPort::new());
    let coordinator = VisibilitySyncCoordinator::new(registry.clone(), sync.clone());
    let bot = Bot::new(registry.clone()).with_visibility_sync(coordinator);

    let task = tokio::spawn(async move {
        bot.set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("bot-visible".to_string()),
            bot_id: "bot-visible".to_string(),
            visibility: "private".to_string(),
        })
        .await
    });
    sync.wait_for_requests(1).await;

    assert!(!task.is_finished());
    assert_eq!(
        registry
            .get("bot-visible")
            .await
            .unwrap()
            .capabilities
            .visibility,
        "private"
    );
    assert_eq!(
        sync.requests.lock().await[0].capabilities.visibility,
        "private"
    );

    sync.release.add_permits(1);
    let result = tokio::time::timeout(Duration::from_secs(1), task)
        .await
        .expect("set_visibility should finish after sync completes")
        .unwrap()
        .unwrap();
    assert_eq!(result.bot_uuid, "bot-visible");
    assert_eq!(result.visibility, "private");
}

#[tokio::test]
async fn onboarding_returns_before_background_sync_completes() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "bot-onboard", "public").await;
    let sync = Arc::new(BlockingVisibilitySyncPort::new());
    let coordinator = VisibilitySyncCoordinator::new(registry.clone(), sync.clone());
    let onboarding = onboarding(registry, coordinator);

    let result = tokio::time::timeout(
        Duration::from_secs(1),
        onboarding.admin_onboard_bot(AdminBotOnboardCommand {
            bot_uuid: "bot-onboard".to_string(),
            name: Some("Latest name".to_string()),
            summary: None,
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
            binding_channels: None,
            actor_identity: None,
        }),
    )
    .await
    .expect("onboarding should not wait for external sync")
    .unwrap();
    assert!(result.onboarded);

    sync.wait_for_requests(1).await;

    let requests = sync.requests.lock().await;
    assert_eq!(
        requests[0].capabilities.name.as_deref(),
        Some("Latest name")
    );
    drop(requests);

    sync.release.add_permits(1);
}
