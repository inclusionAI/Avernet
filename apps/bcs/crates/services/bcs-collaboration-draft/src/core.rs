use async_trait::async_trait;
use bcs_service_api::{
    core::collaboration_draft::*,
    port::repo::collaboration_draft::{CollaborationDraftRepoPort, DraftRepoError},
};
use sha2::{Digest, Sha256};
use std::sync::Arc;

pub struct CollaborationDraftCore {
    repo: Arc<dyn CollaborationDraftRepoPort>,
}

impl CollaborationDraftCore {
    pub fn new(repo: Arc<dyn CollaborationDraftRepoPort>) -> Self {
        Self { repo }
    }

    async fn owned(&self, owner: &str, id: &str) -> Result<DraftMetadata, DraftCoreError> {
        valid_id(id)?;
        let meta = self
            .repo
            .metadata(id)
            .await
            .map_err(repo_error)?
            .ok_or(DraftCoreError::Unavailable)?;
        if owner.is_empty() || meta.owner_bot_id != owner {
            return Err(DraftCoreError::Forbidden);
        }
        Ok(meta)
    }

    async fn stored(&self, id: &str, revision: u64) -> Result<StoredDraftRevision, DraftCoreError> {
        valid_id(id)?;
        valid_revision(revision)?;
        let r = self
            .repo
            .revision(id, revision)
            .await
            .map_err(repo_error)?
            .ok_or(DraftCoreError::Unavailable)?;
        if r.content.draft_id != id
            || r.content.revision != revision
            || r.content.size_bytes != r.content.definition_yaml.len()
            || r.content.size_bytes > MAX_DRAFT_BYTES
            || r.content.content_hash
                != hex::encode(Sha256::digest(r.content.definition_yaml.as_bytes()))
        {
            return Err(DraftCoreError::Storage);
        }
        Ok(r)
    }

    fn new_revision(
        &self,
        owner: &str,
        id: &str,
        revision: u64,
        content: DraftContent,
    ) -> Result<StoredDraftRevision, DraftCoreError> {
        valid_id(id)?;
        valid_revision(revision)?;
        if owner.is_empty()
            || content.definition_yaml.len() > MAX_DRAFT_BYTES
            || content
                .title
                .as_ref()
                .is_some_and(|t| t.trim().is_empty() || t.chars().count() > 120)
        {
            return Err(DraftCoreError::Invalid);
        }
        Ok(StoredDraftRevision {
            content: DraftRevision {
                draft_id: id.into(),
                revision,
                content_hash: hex::encode(Sha256::digest(content.definition_yaml.as_bytes())),
                size_bytes: content.definition_yaml.len(),
                definition_yaml: content.definition_yaml,
                title: content.title,
                created_by: owner.into(),
                created_at: chrono::Utc::now().to_rfc3339(),
            },
            revoked: false,
        })
    }

    async fn existing_create(
        &self,
        owner: &str,
        id: &str,
        content: &DraftContent,
    ) -> Result<SavedDraftRevision, DraftCoreError> {
        let meta = self.owned(owner, id).await?;
        if meta.deleted {
            return Err(DraftCoreError::Unavailable);
        }
        let first = self.stored(id, 1).await?.content;
        if first.definition_yaml != content.definition_yaml || first.title != content.title {
            return Err(DraftCoreError::Conflict);
        }
        Ok(SavedDraftRevision {
            content: first,
            created: false,
        })
    }
}

#[async_trait]
impl CollaborationDraftCoreService for CollaborationDraftCore {
    async fn create(
        &self,
        owner: &str,
        id: &str,
        content: DraftContent,
    ) -> Result<SavedDraftRevision, DraftCoreError> {
        let r = self.new_revision(owner, id, 1, content.clone())?;
        // Point-read before INSERT preserves owner-first errors on existing IDs.
        if self.repo.metadata(id).await.map_err(repo_error)?.is_some() {
            return self.existing_create(owner, id, &content).await;
        }
        let meta = DraftMetadata {
            draft_id: id.into(),
            owner_bot_id: owner.into(),
            head_revision: 1,
            created_at: r.content.created_at.clone(),
            deleted: false,
        };
        match self.repo.create(meta, r.clone()).await {
            Ok(()) => Ok(SavedDraftRevision {
                content: r.content,
                created: true,
            }),
            Err(DraftRepoError::Conflict) => self.existing_create(owner, id, &content).await,
            Err(e) => Err(repo_error(e)),
        }
    }

    async fn update(
        &self,
        owner: &str,
        id: &str,
        expected: u64,
        content: DraftContent,
    ) -> Result<DraftRevision, DraftCoreError> {
        valid_revision(expected)?;
        let meta = self.owned(owner, id).await?;
        if meta.deleted {
            return Err(DraftCoreError::Unavailable);
        }
        let r = self.new_revision(
            owner,
            id,
            expected.checked_add(1).ok_or(DraftCoreError::Invalid)?,
            content,
        )?;
        self.repo
            .append(owner, expected, r.clone())
            .await
            .map_err(repo_error)?;
        Ok(r.content)
    }

    async fn metadata(&self, owner: &str, id: &str) -> Result<DraftMetadata, DraftCoreError> {
        self.owned(owner, id).await
    }

    async fn read(
        &self,
        id: &str,
        revision: u64,
    ) -> Result<DraftRevision, DraftCoreError> {
        let r = self.stored(id, revision).await?;
        if r.revoked {
            return Err(DraftCoreError::Unavailable);
        }
        Ok(r.content)
    }

    async fn reference(
        &self,
        owner: &str,
        id: &str,
        revision: u64,
    ) -> Result<DraftReference, DraftCoreError> {
        if self.owned(owner, id).await?.deleted {
            return Err(DraftCoreError::Unavailable);
        }
        let r = self.stored(id, revision).await?;
        if r.revoked {
            return Err(DraftCoreError::Unavailable);
        }
        Ok(DraftReference {
            schema_version: 2,
            draft_id: id.into(),
            revision,
        })
    }

    async fn revoke(&self, owner: &str, id: &str, revision: u64) -> Result<(), DraftCoreError> {
        if self.owned(owner, id).await?.deleted {
            return Err(DraftCoreError::Unavailable);
        }
        self.stored(id, revision).await?;
        self.repo
            .revoke(owner, id, revision)
            .await
            .map_err(repo_error)
    }

    async fn delete(&self, owner: &str, id: &str) -> Result<(), DraftCoreError> {
        self.owned(owner, id).await?;
        self.repo.delete(owner, id).await.map_err(repo_error)
    }
}

fn valid_id(id: &str) -> Result<(), DraftCoreError> {
    if uuid::Uuid::parse_str(id).is_ok_and(|v| v.to_string() == id) {
        Ok(())
    } else {
        Err(DraftCoreError::Invalid)
    }
}

fn valid_revision(revision: u64) -> Result<(), DraftCoreError> {
    if (1..=MAX_DRAFT_REVISION).contains(&revision) {
        Ok(())
    } else {
        Err(DraftCoreError::Invalid)
    }
}

fn repo_error(error: DraftRepoError) -> DraftCoreError {
    match error {
        DraftRepoError::Conflict => DraftCoreError::Conflict,
        DraftRepoError::Failed => DraftCoreError::Storage,
    }
}
