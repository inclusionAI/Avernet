//! P1.1/P1.4 seam-contract tests: the Bot caller-configuration and Provider
//! caller-capability reads exist as explicit contract methods on the
//! control-plane/provider seams, and every default fails closed with a
//! visible "not supported" error instead of a silent success. The SQL-backed
//! implementations land in a later batch; these tests lock the port shape
//! and the no-silent-stub rule.

use async_trait::async_trait;
use bcs_domain::caller_identity::CallerIdentitySnapshot;
use bcs_service_api::core::{BotControlPlaneCoreService, ProviderCoreService};
use bcs_service_api::port::repo::{
    BotControlPlaneRepoPort, BotCallerConfiguration, CallerConfigurationSync, ProviderRepoPort,
};
use bcs_service_api::types::{
    BotCandidateReadQuery, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotControlPlaneRecord, BotSearchCandidateQuery, BotTaskModesQuery, ProviderRecord,
    ServiceError, ServiceResult,
};

fn not_configured<T>(what: &str) -> ServiceResult<T> {
    Err(ServiceError::InvalidOperation {
        message: format!("stub does not implement {what}"),
        request_id: None,
    })
}

struct StubControlPlaneRepo;

#[async_trait]
impl BotControlPlaneRepoPort for StubControlPlaneRepo {
    async fn get_control_plane(&self, _bot_id: &str, _env: &str) -> ServiceResult<Option<BotControlPlaneRecord>> {
        not_configured("get_control_plane")
    }
    async fn list_control_plane_candidates(&self, _query: BotCandidateReadQuery) -> ServiceResult<(Vec<bcs_service_api::types::BotCandidateReadRecord>, u64)> {
        not_configured("list_control_plane_candidates")
    }
    async fn search_control_plane_candidates(&self, _query: BotSearchCandidateQuery) -> ServiceResult<(Vec<bcs_service_api::types::BotCandidateReadRecord>, u64)> {
        not_configured("search_control_plane_candidates")
    }
    async fn list_control_plane_by_creator(&self, _query: BotControlPlaneOwnedQuery) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        not_configured("list_control_plane_by_creator")
    }
    async fn list_control_plane_by_task_modes(&self, _query: BotTaskModesQuery) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        not_configured("list_control_plane_by_task_modes")
    }
    async fn patch_control_plane(&self, _bot_id: &str, _env: &str, _patch: BotControlPlanePatch) -> ServiceResult<Option<BotControlPlaneRecord>> {
        not_configured("patch_control_plane")
    }
}

struct StubControlPlaneCore;

#[async_trait]
impl BotControlPlaneCoreService for StubControlPlaneCore {
    async fn get_record(&self, _bot_id: &str, _env: &str) -> ServiceResult<Option<BotControlPlaneRecord>> {
        not_configured("get_record")
    }
    async fn get(&self, _bot_id: &str, _env: &str) -> ServiceResult<Option<bcs_service_api::core::BotControlPlaneView>> {
        not_configured("get")
    }
    async fn get_by_ids(&self, _bot_ids: &[String], _env: &str) -> ServiceResult<Vec<bcs_service_api::core::BotControlPlaneView>> {
        not_configured("get_by_ids")
    }
    async fn list_candidates(&self, _query: BotCandidateReadQuery) -> ServiceResult<(Vec<bcs_service_api::core::BotControlPlaneCandidate>, u64)> {
        not_configured("list_candidates")
    }
    async fn list_by_creator(&self, _query: BotControlPlaneOwnedQuery) -> ServiceResult<Vec<bcs_service_api::core::BotControlPlaneView>> {
        not_configured("list_by_creator")
    }
    async fn patch(&self, _bot_id: &str, _env: &str, _patch: BotControlPlanePatch) -> ServiceResult<Option<bcs_service_api::core::BotControlPlaneView>> {
        not_configured("patch")
    }
}

struct StubProviderRepo;

#[async_trait]
impl ProviderRepoPort for StubProviderRepo {
    async fn insert_provider(&self, _provider: ProviderRecord) -> ServiceResult<()> {
        not_configured("insert_provider")
    }
    async fn get_provider(&self, _provider_id: &str) -> ServiceResult<Option<ProviderRecord>> {
        not_configured("get_provider")
    }
    async fn get_provider_by_slug(&self, _slug: &str) -> ServiceResult<Option<ProviderRecord>> {
        not_configured("get_provider_by_slug")
    }
    async fn list_providers(&self) -> ServiceResult<Vec<ProviderRecord>> {
        not_configured("list_providers")
    }
    async fn update_provider_metadata(
        &self,
        _provider_id: &str,
        _name: Option<&str>,
        _config: Option<&str>,
        _slug: Option<&str>,
        _updated_at: u64,
    ) -> ServiceResult<Option<ProviderRecord>> {
        not_configured("update_provider_metadata")
    }
    async fn update_provider_disabled(
        &self,
        _provider_id: &str,
        _disabled: bool,
        _updated_at: u64,
    ) -> ServiceResult<Option<ProviderRecord>> {
        not_configured("update_provider_disabled")
    }
}

fn enabled_snapshot(revision: u64) -> CallerIdentitySnapshot {
    serde_json::from_value(serde_json::json!({
        "source_revision": revision,
        "caller_identity": { "enabled": true }
    }))
    .unwrap()
}

#[tokio::test]
async fn repo_caller_configuration_defaults_fail_closed() {
    let repo = StubControlPlaneRepo;
    // Target-read default must be an explicit error, never a fabricated
    // "unconfigured" or disabled snapshot.
    let err = repo.get_caller_configuration("bot_a", "prod").await.unwrap_err();
    assert!(matches!(err, ServiceError::InvalidOperation { .. }));
    let err = repo
        .sync_caller_configuration("bot_a", "prod", enabled_snapshot(7))
        .await
        .unwrap_err();
    assert!(matches!(err, ServiceError::InvalidOperation { .. }));
    // The provider-scoped variants inherit the same explicit refusal.
    let err = repo
        .get_provider_caller_configuration("bot_a", "prod", "provider_a")
        .await
        .unwrap_err();
    assert!(matches!(err, ServiceError::InvalidOperation { .. }));
    let err = repo
        .sync_provider_caller_configuration("bot_a", "prod", "provider_a", enabled_snapshot(7))
        .await
        .unwrap_err();
    assert!(matches!(err, ServiceError::InvalidOperation { .. }));
}

#[tokio::test]
async fn core_caller_configuration_defaults_fail_closed() {
    let core = StubControlPlaneCore;
    let err = core.get_caller_configuration("bot_a", "prod").await.unwrap_err();
    assert!(matches!(err, ServiceError::InvalidOperation { .. }));
    let err = core
        .sync_caller_configuration("bot_a", "prod", enabled_snapshot(7))
        .await
        .unwrap_err();
    assert!(matches!(err, ServiceError::InvalidOperation { .. }));
    let err = core
        .get_provider_caller_configuration("bot_a", "prod", "provider_a")
        .await
        .unwrap_err();
    assert!(matches!(err, ServiceError::InvalidOperation { .. }));
    let err = core
        .sync_provider_caller_configuration("bot_a", "prod", "provider_a", enabled_snapshot(7))
        .await
        .unwrap_err();
    assert!(matches!(err, ServiceError::InvalidOperation { .. }));
}

#[tokio::test]
async fn provider_capability_defaults_fail_closed() {
    let repo = StubProviderRepo;
    let err = repo.get_caller_capabilities("provider_a").await.unwrap_err();
    assert!(matches!(err, ServiceError::InvalidOperation { .. }));

    let core = StubProviderCore;
    let err = core.get_caller_capabilities("provider_a").await.unwrap_err();
    assert!(matches!(err, ServiceError::InvalidOperation { .. }));
}

struct StubProviderCore;

#[async_trait]
impl ProviderCoreService for StubProviderCore {
    async fn register_provider(
        &self,
        _name: String,
        _webhook_url: Option<String>,
        _auth_mode: bcs_domain::ProviderAuthMode,
        _created_by: String,
        _protocol_version: Option<String>,
        _coordination: Option<bcs_domain::ProviderCoordinationConfig>,
    ) -> ServiceResult<bcs_service_api::core::RegisteredProvider> {
        not_configured("register_provider")
    }
    async fn authenticate_provider_admin(&self, _token: &str) -> ServiceResult<ProviderRecord> {
        not_configured("authenticate_provider_admin")
    }
    async fn get_downlink_credential(
        &self,
        _provider_id: &str,
    ) -> ServiceResult<bcs_domain::provider::ProviderCredential> {
        not_configured("get_downlink_credential")
    }
    async fn get_provider(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord> {
        not_configured("get_provider")
    }
    async fn update_provider(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
        _authenticated_staff_id: &str,
        _name: Option<String>,
        _webhook_url: Option<String>,
        _protocol_version: Option<String>,
        _coordination: Option<bcs_domain::ProviderCoordinationConfig>,
        _organization_management: Option<bcs_domain::ProviderOrganizationManagementConfig>,
    ) -> ServiceResult<ProviderRecord> {
        not_configured("update_provider")
    }
    async fn set_provider_disabled(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
        _authenticated_staff_id: &str,
        _disabled: bool,
    ) -> ServiceResult<ProviderRecord> {
        not_configured("set_provider_disabled")
    }
}

#[test]
fn caller_configuration_sync_is_a_closed_decision_set() {
    let snapshot = enabled_snapshot(42);
    assert_eq!(
        CallerConfigurationSync::applied(snapshot.clone()),
        CallerConfigurationSync::Applied { snapshot: snapshot.clone() }
    );
    assert_eq!(
        CallerConfigurationSync::unchanged(snapshot.clone()),
        CallerConfigurationSync::Unchanged { snapshot: snapshot.clone() }
    );
    assert_eq!(
        CallerConfigurationSync::stale(snapshot),
        CallerConfigurationSync::Stale { snapshot: enabled_snapshot(42) }
    );
    assert_eq!(
        CallerConfigurationSync::Conflict { source_revision: 7 },
        CallerConfigurationSync::Conflict { source_revision: 7 }
    );
    // The read-back projection expresses "never configured" as a null
    // snapshot, not a fabricated revision.
    assert_eq!(
        BotCallerConfiguration { snapshot: None },
        BotCallerConfiguration { snapshot: None }
    );
}