use async_trait::async_trait;
use bcs_service_api::{
    application::collaboration_draft::*,
    application::collaboration_runtime::{
        CollaborationRuntimeService, ValidateCollaborationDefinitionYamlCommand,
    },
    core::collaboration_draft::{CollaborationDraftCoreService, DraftCoreError},
};
use std::sync::Arc;

pub struct CollaborationDraftApplication {
    core: Arc<dyn CollaborationDraftCoreService>,
    validator: Arc<dyn CollaborationRuntimeService>,
    judge_available: bool,
}

impl CollaborationDraftApplication {
    pub fn new(
        core: Arc<dyn CollaborationDraftCoreService>,
        validator: Arc<dyn CollaborationRuntimeService>,
        judge_available: bool,
    ) -> Self {
        Self {
            core,
            validator,
            judge_available,
        }
    }
}

fn application_error(e: DraftCoreError) -> DraftError {
    match e {
        DraftCoreError::Invalid => DraftError::Invalid,
        DraftCoreError::Unavailable => DraftError::Unavailable,
        DraftCoreError::Forbidden => DraftError::Forbidden,
        DraftCoreError::Conflict => DraftError::Conflict,
        DraftCoreError::Storage => DraftError::ServiceUnavailable,
    }
}

#[async_trait]
impl CollaborationDraftService for CollaborationDraftApplication {
    async fn create(
        &self,
        owner: &str,
        id: &str,
        content: DraftContent,
    ) -> Result<SavedDraftRevision, DraftError> {
        self.core
            .create(owner, id, content)
            .await
            .map_err(application_error)
    }
    async fn update(
        &self,
        owner: &str,
        id: &str,
        expected: u64,
        content: DraftContent,
    ) -> Result<DraftRevision, DraftError> {
        self.core
            .update(owner, id, expected, content)
            .await
            .map_err(application_error)
    }
    async fn metadata(&self, owner: &str, id: &str) -> Result<DraftMetadata, DraftError> {
        self.core
            .metadata(owner, id)
            .await
            .map_err(application_error)
    }
    async fn read(
        &self,
        id: &str,
        revision: u64,
    ) -> Result<DraftRevision, DraftError> {
        self.core
            .read(id, revision)
            .await
            .map_err(application_error)
    }
    async fn reference(
        &self,
        owner: &str,
        id: &str,
        revision: u64,
    ) -> Result<DraftReference, DraftError> {
        self.core
            .reference(owner, id, revision)
            .await
            .map_err(application_error)
    }
    async fn validate(
        &self,
        id: &str,
        revision: u64,
    ) -> Result<DraftValidation, DraftError> {
        let content = self
            .core
            .read(id, revision)
            .await
            .map_err(application_error)?;
        let validation = self
            .validator
            .validate_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
                definition_yaml: content.definition_yaml,
                judge_available: self.judge_available,
            })
            .await
            .map_err(|_| DraftError::ServiceUnavailable)?;
        Ok(DraftValidation {
            draft_id: content.draft_id,
            revision: content.revision,
            content_hash: content.content_hash,
            validated_at: chrono::Utc::now().to_rfc3339(),
            validator: concat!(
                "bcs/",
                env!("CARGO_PKG_VERSION"),
                "/collaboration-definition"
            )
            .into(),
            validation,
        })
    }
    async fn revoke(&self, owner: &str, id: &str, revision: u64) -> Result<(), DraftError> {
        self.core
            .revoke(owner, id, revision)
            .await
            .map_err(application_error)
    }
    async fn delete(&self, owner: &str, id: &str) -> Result<(), DraftError> {
        self.core.delete(owner, id).await.map_err(application_error)
    }
}
