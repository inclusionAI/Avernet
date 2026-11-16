use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot::core::{BotCore, ProviderCore};
use bcs_bot_store::{provider::MemoryProviderStore, MemoryBotRepo};
use bcs_service_api::{
    ProviderA2aPatch, ProviderAuthMode, ProviderCoreService, ProviderRecord, ProviderRepoPort,
    ServiceResult,
};
use serde_json::{json, Value};
use tokio::sync::Notify;

// Pause the rename after Core has read its snapshot, before persistence.
#[derive(Default)]
struct PausingRepo {
    inner: Arc<MemoryProviderStore>,
    rename_ready: Notify,
    resume_rename: Notify,
}

#[async_trait]
impl ProviderRepoPort for PausingRepo {
    async fn insert_provider(&self, provider: ProviderRecord) -> ServiceResult<()> {
        self.inner.insert_provider(provider).await
    }
    async fn get_provider(&self, id: &str) -> ServiceResult<Option<ProviderRecord>> {
        self.inner.get_provider(id).await
    }
    async fn get_provider_by_slug(&self, slug: &str) -> ServiceResult<Option<ProviderRecord>> {
        self.inner.get_provider_by_slug(slug).await
    }
    async fn list_providers(&self) -> ServiceResult<Vec<ProviderRecord>> {
        self.inner.list_providers().await
    }
    async fn update_provider_metadata(
        &self,
        id: &str,
        name: Option<&str>,
        config: Option<&str>,
        slug: Option<&str>,
        at: u64,
    ) -> ServiceResult<Option<ProviderRecord>> {
        if name == Some("Renamed") {
            self.rename_ready.notify_one();
            self.resume_rename.notified().await;
        }
        self.inner
            .update_provider_metadata(id, name, config, slug, at)
            .await
    }
    async fn update_provider_disabled(
        &self,
        id: &str,
        disabled: bool,
        at: u64,
    ) -> ServiceResult<Option<ProviderRecord>> {
        self.inner.update_provider_disabled(id, disabled, at).await
    }
}

fn core(repo: Arc<PausingRepo>, dir: &std::path::Path) -> Arc<ProviderCore> {
    let registry = Arc::new(BotCore::with_provider_repos(
        Arc::new(MemoryBotRepo::with_base_dir(dir.to_path_buf())),
        repo.clone(),
        repo.inner.clone(),
        repo.inner.clone(),
    ));
    Arc::new(ProviderCore::new(
        repo.clone(),
        repo.inner.clone(),
        repo.inner.clone(),
        registry,
    ))
}

#[tokio::test]
async fn empty_a2a_patch_does_not_restore_concurrently_cleared_owner() {
    // Cover omission/null (both map to None) and the empty object.
    for patch in [None, Some(ProviderA2aPatch::default())] {
        let dir = tempfile::tempdir().unwrap();
        let repo = Arc::new(PausingRepo::default());
        let core = core(repo.clone(), dir.path());
        let registered = core
            .register_provider_with_a2a(
                "Provider".into(),
                None,
                ProviderAuthMode::StaticBearer,
                "alice".into(),
                None,
                None,
                None,
                None,
                None,
                None,
                Some(ProviderA2aPatch {
                    default_owner: Some(Some("alice".into())),
                }),
            )
            .await
            .unwrap();
        let id = registered.provider.provider_id;
        let token = registered.provider_admin_token;
        let rename_core = core.clone();
        let rename_id = id.clone();
        let rename_token = token.clone();
        let rename = tokio::spawn(async move {
            rename_core
                .update_provider_with_a2a(
                    &rename_id,
                    &rename_token,
                    "alice",
                    Some("Renamed".into()),
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    patch,
                )
                .await
                .unwrap()
        });
        tokio::time::timeout(
            std::time::Duration::from_secs(5),
            repo.rename_ready.notified(),
        )
        .await
        .unwrap();
        let cleared = core
            .update_provider_with_a2a(
                &id,
                &token,
                "alice",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                Some(ProviderA2aPatch {
                    default_owner: Some(None),
                }),
            )
            .await
            .unwrap();
        let config: Value = serde_json::from_str(&cleared.config).unwrap();
        assert!(config["a2a"].get("default_owner").is_none());
        repo.resume_rename.notify_one();
        let renamed = rename.await.unwrap();
        assert_eq!(renamed.name, "Renamed");
        let stored = repo.get_provider(&id).await.unwrap().unwrap();
        let config: Value = serde_json::from_str(&stored.config).unwrap();
        assert!(
            config["a2a"].get("default_owner").is_none(),
            "rename restored a stale owner"
        );
        assert_eq!(stored.name, "Renamed");
    }
}

#[tokio::test]
async fn empty_a2a_patch_keeps_other_requested_config_updates() {
    let dir = tempfile::tempdir().unwrap();
    let repo = Arc::new(PausingRepo::default());
    let core = core(repo.clone(), dir.path());
    let registered = core
        .register_provider_with_a2a(
            "Provider".into(),
            None,
            ProviderAuthMode::StaticBearer,
            "alice".into(),
            None,
            None,
            None,
            None,
            None,
            None,
            Some(ProviderA2aPatch {
                default_owner: Some(Some("alice".into())),
            }),
        )
        .await
        .unwrap();
    let updated = core
        .update_provider_with_a2a(
            &registered.provider.provider_id,
            &registered.provider_admin_token,
            "alice",
            Some("Updated".into()),
            Some("https://example.com/webhook".into()),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            Some(ProviderA2aPatch::default()),
        )
        .await
        .unwrap();
    let config: Value = serde_json::from_str(&updated.config).unwrap();
    assert_eq!(updated.name, "Updated");
    assert_eq!(
        config["downlink"]["webhook_url"],
        json!("https://example.com/webhook")
    );
    assert_eq!(config["a2a"]["default_owner"], json!("alice"));
}
