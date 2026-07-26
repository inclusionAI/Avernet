use std::{
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};

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
    active: AtomicUsize,
    max_active: AtomicUsize,
}

impl BlockingVisibilitySyncPort {
    fn new() -> Self {
        Self {
            requests: Mutex::new(Vec::new()),
            request_recorded: Notify::new(),
            release: Semaphore::new(0),
            active: AtomicUsize::new(0),
            max_active: AtomicUsize::new(0),
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

    async fn wait_until_idle(&self) {
        tokio::time::timeout(Duration::from_secs(1), async {
            while self.active.load(Ordering::SeqCst) != 0 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("visibility sync requests should finish");
    }
}

#[async_trait]
impl VisibilitySyncPort for BlockingVisibilitySyncPort {
    async fn sync_visibility(&self, request: VisibilitySyncRequest) {
        let active = self.active.fetch_add(1, Ordering::SeqCst) + 1;
        self.max_active.fetch_max(active, Ordering::SeqCst);
        self.requests.lock().await.push(request);
        self.request_recorded.notify_waiters();

        let permit = self.release.acquire().await.unwrap();
        permit.forget();
        self.active.fetch_sub(1, Ordering::SeqCst);
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
async fn visibility_sync_application_services_schedule_after_successful_mutations() {
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
async fn visibility_sync_management_and_onboarding_share_one_serial_job_per_bot() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "bot-shared", "public").await;
    let sync = Arc::new(BlockingVisibilitySyncPort::new());
    let coordinator = VisibilitySyncCoordinator::new(registry.clone(), sync.clone());
    let bot = Bot::new(registry.clone()).with_visibility_sync(coordinator.clone());
    let onboarding = onboarding(registry, coordinator);

    tokio::time::timeout(
        Duration::from_secs(1),
        bot.set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("bot-shared".to_string()),
            bot_id: "bot-shared".to_string(),
            visibility: "private".to_string(),
        }),
    )
    .await
    .expect("application use case must not wait for external sync")
    .unwrap();
    sync.wait_for_requests(1).await;

    tokio::time::timeout(
        Duration::from_secs(1),
        onboarding.admin_onboard_bot(AdminBotOnboardCommand {
            bot_uuid: "bot-shared".to_string(),
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
    .expect("application use case must not wait for external sync")
    .unwrap();

    assert_eq!(sync.requests.lock().await.len(), 1);
    sync.release.add_permits(1);
    sync.wait_for_requests(2).await;

    let requests = sync.requests.lock().await;
    assert_eq!(requests[0].capabilities.visibility, "private");
    assert_eq!(
        requests[1].capabilities.name.as_deref(),
        Some("Latest name")
    );
    assert_eq!(sync.max_active.load(Ordering::SeqCst), 1);
    drop(requests);

    sync.release.add_permits(1);
    sync.wait_until_idle().await;
}
