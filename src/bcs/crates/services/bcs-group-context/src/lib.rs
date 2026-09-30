//! Group Context service implementations — core, application, and noop repo.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::group_context::{ContextView, TemplateView};
use bcs_service_api::application::group_context::{
    CreateByTemplateRequest, CreateResponse, GroupContextService, RetrieveRequest,
    RetrieveResponse, StatusRequest, StatusResponse, UpdateContentRequest, UpdateResponse,
};
use bcs_service_api::core::group_context::{
    CreateByTemplateCommand, CreateResult, GroupContextCoreService, RetrieveCommand,
    RetrieveResult, StatusCommand, StatusResult, UpdateContentCommand, UpdateResult,
};
use bcs_service_api::port::repo::group_context::{
    AuditEntry, GroupContextRepoPort, InsertContextRequest, ScopeKey, SupersedeRequest,
};

// ── Noop repo ──────────────────────────────────────────────────────────────

/// A no-op repo that returns empty lists for reads and errors for writes.
/// Useful for tests and bootstrapping before the real store is wired.
pub struct NoopGroupContextRepo;

#[async_trait]
impl GroupContextRepoPort for NoopGroupContextRepo {
    async fn find_active_in_scope(
        &self,
        _scope: &ScopeKey,
        _domain: Option<&str>,
        _limit: u32,
    ) -> Result<Vec<ContextView>, String> {
        Ok(Vec::new())
    }

    async fn insert_context(
        &self,
        _request: InsertContextRequest,
    ) -> Result<bcs_domain::group_context::ContextEntry, String> {
        Err("noop: insert_context not implemented".to_string())
    }

    async fn supersede(
        &self,
        _request: SupersedeRequest,
    ) -> Result<bcs_service_api::port::repo::group_context::SupersedeOutcome, String>
    {
        Err("noop: supersede not implemented".to_string())
    }

    async fn find_entries_by_domain(
        &self,
        _scope: &ScopeKey,
        _domain: &str,
        _key: Option<&str>,
        _active_only: bool,
        _limit: u32,
    ) -> Result<Vec<bcs_domain::group_context::ContextEntry>, String> {
        Ok(Vec::new())
    }

    async fn find_candidates_for_retrieve(
        &self,
        _scope: &ScopeKey,
        _domain: Option<&str>,
        _query: Option<&str>,
        _limit: u32,
    ) -> Result<Vec<bcs_domain::group_context::RetrievalItem>, String> {
        Ok(Vec::new())
    }

    async fn list_active_contexts_for_actor(
        &self,
        _scope: &ScopeKey,
        _actor_id: &str,
        _domain: Option<&str>,
        _limit: u32,
    ) -> Result<Vec<ContextView>, String> {
        Ok(Vec::new())
    }

    async fn list_creatable_templates_for_actor(
        &self,
        _scope: &ScopeKey,
        _actor_id: &str,
        _domain: Option<&str>,
        _limit: u32,
    ) -> Result<Vec<TemplateView>, String> {
        Ok(Vec::new())
    }

    async fn find_policy_template(
        &self,
        _template_id: &str,
    ) -> Result<Option<bcs_domain::group_context::PolicyTemplate>, String> {
        Ok(None)
    }

    async fn list_policy_templates(
        &self,
        _domain: Option<&str>,
        _limit: u32,
    ) -> Result<Vec<bcs_domain::group_context::PolicyTemplate>, String> {
        Ok(Vec::new())
    }

    async fn write_retrieve_audit(&self, _audit: AuditEntry) -> Result<(), String> {
        Ok(())
    }
}

// ── Core service ───────────────────────────────────────────────────────────

pub struct GroupContextCore {
    repo: Arc<dyn GroupContextRepoPort>,
}

impl GroupContextCore {
    pub fn new(repo: Arc<dyn GroupContextRepoPort>) -> Self {
        Self { repo }
    }
}

#[async_trait]
impl GroupContextCoreService for GroupContextCore {
    async fn status(&self, cmd: StatusCommand) -> Result<StatusResult, String> {
        let scope = ScopeKey {
            tenant_id: cmd.tenant_id,
            group_id: cmd.group_id,
            session_id: cmd.session_id,
            run_id: cmd.run_id,
        };
        let contexts = self
            .repo
            .list_active_contexts_for_actor(
                &scope,
                &cmd.actor_id,
                cmd.domain.as_deref(),
                cmd.limit,
            )
            .await?;
        let templates = self
            .repo
            .list_creatable_templates_for_actor(
                &scope,
                &cmd.actor_id,
                cmd.domain.as_deref(),
                cmd.limit,
            )
            .await?;
        Ok(StatusResult {
            contexts,
            templates,
        })
    }

    async fn create_by_template(
        &self,
        _cmd: CreateByTemplateCommand,
    ) -> Result<CreateResult, String> {
        Err("noop: create_by_template not implemented".to_string())
    }

    async fn update_content(
        &self,
        _cmd: UpdateContentCommand,
    ) -> Result<UpdateResult, String> {
        Err("noop: update_content not implemented".to_string())
    }

    async fn retrieve(&self, cmd: RetrieveCommand) -> Result<RetrieveResult, String> {
        let scope = ScopeKey {
            tenant_id: cmd.tenant_id,
            group_id: cmd.group_id,
            session_id: cmd.session_id,
            run_id: cmd.run_id,
        };
        let items = self
            .repo
            .find_candidates_for_retrieve(
                &scope,
                cmd.domain.as_deref(),
                cmd.query.as_deref(),
                cmd.limit,
            )
            .await?;
        Ok(RetrieveResult { items })
    }
}

// ── Application service ────────────────────────────────────────────────────

pub struct GroupContextApplication {
    core: Arc<dyn GroupContextCoreService>,
}

impl GroupContextApplication {
    pub fn new(core: Arc<dyn GroupContextCoreService>) -> Self {
        Self { core }
    }
}

#[async_trait]
impl GroupContextService for GroupContextApplication {
    async fn status(&self, request: StatusRequest) -> Result<StatusResponse, String> {
        let cmd = StatusCommand {
            tenant_id: request.tenant_id,
            group_id: request.group_id,
            session_id: request.session_id,
            run_id: request.run_id,
            actor_id: request.actor_id,
            domain: request.domain,
            limit: request.limit.unwrap_or(20),
        };
        let result = self.core.status(cmd).await?;
        Ok(StatusResponse {
            contexts: result.contexts,
            templates: result.templates,
        })
    }

    async fn create_by_template(
        &self,
        origin: bcs_domain::group_context::Origin,
        request: CreateByTemplateRequest,
    ) -> Result<CreateResponse, String> {
        let cmd = CreateByTemplateCommand {
            tenant_id: origin.tenant_id,
            group_id: origin.group_id,
            session_id: origin.session_id,
            run_id: origin.run_id,
            actor_id: origin.actor_id,
            template_id: request.template_id,
            domain: request.domain,
            key: request.key,
            content: request.content,
        };
        let result = self.core.create_by_template(cmd).await?;
        Ok(CreateResponse {
            entry_id: result.entry.id,
            version: result.entry.version,
            created_at_ms: result.entry.time.tx_time,
        })
    }

    async fn update_content(
        &self,
        origin: bcs_domain::group_context::Origin,
        request: UpdateContentRequest,
    ) -> Result<UpdateResponse, String> {
        let cmd = UpdateContentCommand {
            tenant_id: origin.tenant_id,
            group_id: origin.group_id,
            session_id: origin.session_id,
            run_id: origin.run_id,
            actor_id: origin.actor_id,
            entry_id: request.entry_id,
            new_content: request.new_content,
            expected_version: request.expected_version,
        };
        let result = self.core.update_content(cmd).await?;
        Ok(UpdateResponse {
            old_entry_id: result.old_entry_id,
            new_entry_id: result.new_entry.id,
            new_version: result.new_entry.version,
        })
    }

    async fn retrieve(
        &self,
        origin: bcs_domain::group_context::Origin,
        request: RetrieveRequest,
    ) -> Result<RetrieveResponse, String> {
        let cmd = RetrieveCommand {
            tenant_id: origin.tenant_id,
            group_id: origin.group_id,
            session_id: origin.session_id,
            run_id: origin.run_id,
            actor_id: origin.actor_id,
            domain: request.domain,
            query: request.query,
            limit: request.limit,
        };
        let result = self.core.retrieve(cmd).await?;
        Ok(RetrieveResponse { items: result.items })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::group_context::{ContextEntry, Granularity, Origin};

    fn test_core() -> GroupContextCore {
        GroupContextCore::new(Arc::new(NoopGroupContextRepo))
    }

    fn test_app() -> GroupContextApplication {
        GroupContextApplication::new(Arc::new(test_core()))
    }

    fn test_origin() -> Origin {
        Origin {
            tenant_id: "tenant-1".to_string(),
            group_id: "group-1".to_string(),
            session_id: Some("sess-1".to_string()),
            run_id: Some("run-1".to_string()),
            actor_id: "bot-1".to_string(),
        }
    }

    fn test_scope() -> ScopeKey {
        ScopeKey {
            tenant_id: "t".to_string(),
            group_id: "g".to_string(),
            session_id: None,
            run_id: None,
        }
    }

    // ── NoopGroupContextRepo ──────────────────────────────────────────────

    #[tokio::test]
    async fn noop_find_active_in_scope_returns_empty() {
        let repo = NoopGroupContextRepo;
        assert!(repo.find_active_in_scope(&test_scope(), None, 10).await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn noop_insert_context_returns_error() {
        let repo = NoopGroupContextRepo;
        let entry = ContextEntry {
            id: "e1".into(),
            domain: "test".into(),
            key: "k".into(),
            content: serde_json::Value::Null,
            origin: test_origin(),
            time: Default::default(),
            granularity: Granularity::Session,
            template_id: "t1".into(),
            version: 1,
            superseded_by: None,
            lineage: vec![],
        };
        assert!(repo.insert_context(InsertContextRequest { entry }).await.is_err());
    }

    #[tokio::test]
    async fn noop_supersede_returns_error() {
        let repo = NoopGroupContextRepo;
        let req = SupersedeRequest {
            entry_id: "e1".into(),
            new_version: 2,
            prev_version: 1,
            new_entry_id: "e2".into(),
            tx_time_ms: 0,
            actor_id: "bot-1".into(),
        };
        assert!(repo.supersede(req).await.is_err());
    }

    #[tokio::test]
    async fn noop_find_entries_by_domain_returns_empty() {
        let repo = NoopGroupContextRepo;
        assert!(repo.find_entries_by_domain(&test_scope(), "d", None, true, 10).await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn noop_find_candidates_for_retrieve_returns_empty() {
        let repo = NoopGroupContextRepo;
        assert!(repo.find_candidates_for_retrieve(&test_scope(), None, None, 10).await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn noop_find_policy_template_returns_none() {
        let repo = NoopGroupContextRepo;
        assert!(repo.find_policy_template("t1").await.unwrap().is_none());
    }

    #[tokio::test]
    async fn noop_list_policy_templates_returns_empty() {
        let repo = NoopGroupContextRepo;
        assert!(repo.list_policy_templates(None, 10).await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn noop_write_retrieve_audit_ok() {
        let repo = NoopGroupContextRepo;
        repo.write_retrieve_audit(AuditEntry {
            entry_id: "e1".into(),
            actor_id: "bot-1".into(),
            op: "retrieve".into(),
            tx_time_ms: 0,
        }).await.unwrap();
    }

    // ── GroupContextCore ──────────────────────────────────────────────────

    #[tokio::test]
    async fn core_retrieve_returns_empty() {
        let core = test_core();
        let cmd = RetrieveCommand {
            tenant_id: "t".into(),
            group_id: "g".into(),
            session_id: None,
            run_id: None,
            actor_id: "bot-1".into(),
            domain: None,
            query: None,
            limit: 20,
        };
        assert!(core.retrieve(cmd).await.unwrap().items.is_empty());
    }

    #[tokio::test]
    async fn core_create_by_template_returns_error() {
        let core = test_core();
        let cmd = CreateByTemplateCommand {
            tenant_id: "t".into(),
            group_id: "g".into(),
            session_id: None,
            run_id: None,
            actor_id: "bot-1".into(),
            template_id: "t1".into(),
            domain: "test".into(),
            key: "k".into(),
            content: serde_json::Value::Null,
        };
        assert!(core.create_by_template(cmd).await.is_err());
    }

    #[tokio::test]
    async fn core_update_content_returns_error() {
        let core = test_core();
        let cmd = UpdateContentCommand {
            tenant_id: "t".into(),
            group_id: "g".into(),
            session_id: None,
            run_id: None,
            actor_id: "bot-1".into(),
            entry_id: "e1".into(),
            new_content: serde_json::Value::Null,
            expected_version: None,
        };
        assert!(core.update_content(cmd).await.is_err());
    }

    // ── GroupContextApplication ───────────────────────────────────────────

    #[tokio::test]
    async fn app_retrieve_returns_empty() {
        let app = test_app();
        let request = RetrieveRequest {
            domain: None,
            query: None,
            limit: 20,
        };
        assert!(app.retrieve(test_origin(), request).await.unwrap().items.is_empty());
    }

    #[tokio::test]
    async fn app_create_by_template_returns_error() {
        let app = test_app();
        let request = CreateByTemplateRequest {
            template_id: "t1".into(),
            domain: "test".into(),
            key: "k".into(),
            content: serde_json::Value::Null,
        };
        assert!(app.create_by_template(test_origin(), request).await.is_err());
    }

    #[tokio::test]
    async fn app_update_content_returns_error() {
        let app = test_app();
        let request = UpdateContentRequest {
            entry_id: "e1".into(),
            new_content: serde_json::Value::Null,
            expected_version: None,
        };
        assert!(app.update_content(test_origin(), request).await.is_err());
    }

    // ── StatusRequest::into_origin ────────────────────────────────────────

    #[test]
    fn status_request_into_origin_preserves_all_fields() {
        let req = StatusRequest {
            tenant_id: "tenant-1".into(),
            group_id: "group-1".into(),
            session_id: Some("sess-1".into()),
            run_id: Some("run-1".into()),
            actor_id: "bot-1".into(),
            domain: None,
            limit: None,
        };
        let origin = req.into_origin();
        assert_eq!(origin.tenant_id, "tenant-1");
        assert_eq!(origin.group_id, "group-1");
        assert_eq!(origin.session_id.as_deref(), Some("sess-1"));
        assert_eq!(origin.run_id.as_deref(), Some("run-1"));
        assert_eq!(origin.actor_id, "bot-1");
    }

    // ── RetrieveRequest serde default ─────────────────────────────────────

    #[test]
    fn retrieve_request_default_limit_is_20() {
        let req: RetrieveRequest = serde_json::from_str(r#"{"domain":"test"}"#).unwrap();
        assert_eq!(req.limit, 20);
    }
}