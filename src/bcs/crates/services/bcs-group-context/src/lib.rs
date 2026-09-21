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