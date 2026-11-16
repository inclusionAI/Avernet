use async_trait::async_trait;
use bcs_bot::BotControlPlaneCore;
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_service_api::{
    BotCandidateReadQuery, BotCandidateReadRecord, BotCapabilities, BotControlPlaneCoreService,
    BotControlPlaneOwnedQuery, BotControlPlanePatch, BotControlPlaneRecord,
    BotControlPlaneRepoPort, BotRepoPort, BotSearchCandidateQuery, BotTaskModesQuery,
    FriendCheckInStrategy, ServiceResult,
};
use std::sync::Arc;

// Commit another request just before the store applies the strategy patch.
// A core-derived public value would replay a stale visibility snapshot here.
struct InterleavingRepo {
    inner: Arc<MemoryBotRepo>,
    concurrent_patch: BotControlPlanePatch,
}
#[async_trait]
impl BotControlPlaneRepoPort for InterleavingRepo {
    async fn get_control_plane(
        &self,
        id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        self.inner.get_control_plane(id, env).await
    }
    async fn patch_control_plane(
        &self,
        id: &str,
        env: &str,
        patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        self.inner
            .patch_control_plane(id, env, self.concurrent_patch.clone())
            .await?;
        self.inner.patch_control_plane(id, env, patch).await
    }
    async fn list_control_plane_candidates(
        &self,
        _: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotCandidateReadRecord>, u64)> {
        unreachable!()
    }
    async fn search_control_plane_candidates(
        &self,
        _: BotSearchCandidateQuery,
    ) -> ServiceResult<(Vec<BotCandidateReadRecord>, u64)> {
        unreachable!()
    }
    async fn list_control_plane_by_creator(
        &self,
        _: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        unreachable!()
    }
    async fn list_control_plane_by_task_modes(
        &self,
        _: BotTaskModesQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        unreachable!()
    }
}
async fn run_interleaving(concurrent_patch: BotControlPlanePatch) -> BotControlPlaneRecord {
    let temp = tempfile::tempdir().unwrap();
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    repo.register_with_owner_and_token(
        "bot".into(),
        BotCapabilities {
            name: Some("Race Bot".into()),
            ..Default::default()
        },
        "owner",
        "token",
    )
    .await
    .unwrap();
    let core = BotControlPlaneCore::new(
        Arc::new(InterleavingRepo {
            inner: repo.clone(),
            concurrent_patch,
        }),
        Arc::new(MemoryProviderStore::new()),
        Arc::new(MemoryProviderStore::new()),
    );
    let env = bcs_config::resolve_env_str();
    core.patch(
        "bot",
        &env,
        BotControlPlanePatch {
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        },
    )
    .await
    .unwrap();
    repo.get_control_plane("bot", &env).await.unwrap().unwrap()
}
#[tokio::test]
async fn open_patch_must_preserve_concurrent_private_visibility() {
    let record = run_interleaving(BotControlPlanePatch {
        visibility: Some("private".into()),
        ..Default::default()
    })
    .await;
    assert_eq!(
        record.visibility, "private",
        "strategy-only patch must not undo concurrent privacy change"
    );
}
#[tokio::test]
async fn open_patch_preserves_concurrent_department_restriction() {
    let record = run_interleaving(BotControlPlanePatch {
        visibility: Some("protected".into()),
        friend_ext: Some(
            serde_json::from_value(serde_json::json!({
                "view_scope_agent_friend_deps": [{"deptNo":"D1", "deptName":"Engineering"}]
            }))
            .unwrap(),
        ),
        ..Default::default()
    })
    .await;
    assert!(
        !record.friend_ext["view_scope_agent_friend_deps"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        record.visibility, "protected",
        "concurrent active scope must prevent public visibility"
    );
}
