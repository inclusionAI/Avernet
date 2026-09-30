//! Transport-neutral OpenAPI V1 collaboration-template application contract.
//!
//! Collaboration templates are a read-only catalog, so the V1 projection reuses
//! the legacy `CollaborationTemplateService` response types verbatim. The V1
//! facade only translates the legacy error vocabulary into the V1
//! `ApplicationError` and presents the same list/get use cases under the
//! internal ownership prefix. Catalog reads are not scoped to a Bot or Session,
//! so these commands carry no caller principal.

use async_trait::async_trait;

use super::ApplicationError;
pub use crate::application::collaboration_template::{
    CollaborationTemplateDetail, CollaborationTemplateFormat, CollaborationTemplateListResponse,
};

/// List the collaboration-template catalog.
///
/// `requested_language` selects a preferred language; when `None` the facade
/// falls back to `accept_language` and then the registry default. `tags` is an
/// optional comma-separated filter that the caller has already split and
/// trimmed.
#[derive(Debug, Clone, Default)]
pub struct ListCollaborationTemplates {
    pub requested_language: Option<String>,
    pub accept_language: Option<String>,
    pub tags: Vec<String>,
}

/// Fetch a single collaboration template by identifier and selected language.
#[derive(Debug, Clone)]
pub struct GetCollaborationTemplate {
    pub template_id: String,
    pub requested_language: Option<String>,
    pub accept_language: Option<String>,
    pub format: CollaborationTemplateFormat,
}

/// V1 application facade for the collaboration-template catalog.
///
/// Implementations delegate to the legacy `CollaborationTemplateService` and
/// are not caller-scoped: the delivery adapter authenticates the Gateway
/// Principal on the protected boundary, but no per-caller authorization is
/// applied to catalog reads.
#[async_trait]
pub trait CollaborationTemplateService: Send + Sync {
    async fn list_templates(
        &self,
        command: ListCollaborationTemplates,
    ) -> Result<CollaborationTemplateListResponse, ApplicationError>;

    async fn get_template(
        &self,
        query: GetCollaborationTemplate,
    ) -> Result<CollaborationTemplateDetail, ApplicationError>;
}