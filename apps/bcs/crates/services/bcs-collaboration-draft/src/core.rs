use async_trait::async_trait;
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use bcs_service_api::{
    core::collaboration_draft::*,
    port::repo::collaboration_draft::{CollaborationDraftRepoPort, DraftRepoError},
};
use hmac::{Hmac, Mac};
use rand::{RngCore, rngs::OsRng};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, sync::Arc};

/// Loaded by the composition root from protected, persistent key files.
pub struct DraftSigningKeys {
    deployment: String,
    active: String,
    keys: BTreeMap<String, Vec<u8>>,
}

impl DraftSigningKeys {
    pub fn new(
        deployment: String,
        active: String,
        keys: BTreeMap<String, Vec<u8>>,
    ) -> Result<Self, DraftCoreError> {
        if deployment.trim().is_empty()
            || active.is_empty()
            || !keys.contains_key(&active)
            || keys.iter().any(|(id, key)| id.is_empty() || key.len() < 32)
        {
            return Err(DraftCoreError::Invalid);
        }
        Ok(Self {
            deployment,
            active,
            keys,
        })
    }

    fn mac(&self, r: &StoredDraftRevision) -> Result<Hmac<Sha256>, DraftCoreError> {
        let key = self.keys.get(&r.key_id).ok_or(DraftCoreError::Storage)?;
        let mut mac = Hmac::<Sha256>::new_from_slice(key).map_err(|_| DraftCoreError::Storage)?;
        let input = serde_json::to_vec(&(
            "bcs.collaboration-draft.read.v1",
            &self.deployment,
            &r.content.draft_id,
            r.content.revision,
            &r.share_nonce,
        ))
        .map_err(|_| DraftCoreError::Storage)?;
        mac.update(&input);
        Ok(mac)
    }
}

pub struct CollaborationDraftCore {
    repo: Arc<dyn CollaborationDraftRepoPort>,
    signing: DraftSigningKeys,
}

impl CollaborationDraftCore {
    pub fn new(repo: Arc<dyn CollaborationDraftRepoPort>, signing: DraftSigningKeys) -> Self {
        Self { repo, signing }
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
        let mut nonce = [0u8; 32];
        OsRng
            .try_fill_bytes(&mut nonce)
            .map_err(|_| DraftCoreError::Storage)?;
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
            share_nonce: URL_SAFE_NO_PAD.encode(nonce),
            key_id: self.signing.active.clone(),
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
        access: DraftAccess,
        id: &str,
        revision: u64,
    ) -> Result<DraftRevision, DraftCoreError> {
        match access {
            DraftAccess::Owner(owner) => {
                if self.owned(&owner, id).await?.deleted {
                    return Err(DraftCoreError::Unavailable);
                }
                Ok(self.stored(id, revision).await?.content)
            }
            DraftAccess::ReadToken(token) => {
                // Uniform errors for malformed, missing, revoked and wrong-version references.
                if valid_id(id).is_err() || valid_revision(revision).is_err() {
                    return Err(DraftCoreError::Unavailable);
                }
                let bytes = URL_SAFE_NO_PAD
                    .decode(&token)
                    .map_err(|_| DraftCoreError::Unavailable)?;
                if bytes.len() != 32 || URL_SAFE_NO_PAD.encode(&bytes) != token {
                    return Err(DraftCoreError::Unavailable);
                }
                let r = self.stored(id, revision).await?;
                if r.revoked {
                    return Err(DraftCoreError::Unavailable);
                }
                self.signing
                    .mac(&r)?
                    .verify_slice(&bytes)
                    .map_err(|_| DraftCoreError::Unavailable)?;
                Ok(r.content)
            }
        }
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
        let capability = URL_SAFE_NO_PAD.encode(self.signing.mac(&r)?.finalize().into_bytes());
        Ok(DraftReference {
            schema_version: 2,
            draft_id: id.into(),
            revision,
            read_token: capability,
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
