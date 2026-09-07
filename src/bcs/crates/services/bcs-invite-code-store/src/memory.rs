use std::collections::HashMap;
use std::path::PathBuf;

use async_trait::async_trait;
use bcs_service_api::port::repo::{
    InviteCodeBindOutcome, InviteCodeRecord, InviteCodeRepoPort, InviteCodeStatus,
};
use bcs_service_api::ServiceResult;
use tokio::sync::RwLock;
use tracing::info;

/// In-memory invite-code repository with optional file persistence.
pub struct MemoryInviteCodeRepo {
    by_code_hash: RwLock<HashMap<String, InviteCodeRecord>>,
    data_dir: Option<PathBuf>,
}

impl MemoryInviteCodeRepo {
    pub fn new() -> Self {
        Self {
            by_code_hash: RwLock::new(HashMap::new()),
            data_dir: None,
        }
    }

    pub fn with_data_dir(data_dir: PathBuf) -> Self {
        Self {
            by_code_hash: RwLock::new(HashMap::new()),
            data_dir: Some(data_dir),
        }
    }

    async fn save_to_disk(&self) -> ServiceResult<()> {
        let Some(ref data_dir) = self.data_dir else {
            return Ok(());
        };
        tokio::fs::create_dir_all(data_dir).await?;
        let records: Vec<InviteCodeRecord> = self.by_code_hash.read().await.values().cloned().collect();
        tokio::fs::write(
            data_dir.join("invite_codes.json"),
            serde_json::to_string_pretty(&records)?,
        )
        .await?;
        Ok(())
    }

    pub async fn load_from_disk(&self) -> ServiceResult<()> {
        let Some(ref data_dir) = self.data_dir else {
            return Ok(());
        };
        let path = data_dir.join("invite_codes.json");
        if !path.exists() {
            return Ok(());
        }
        let data = tokio::fs::read_to_string(&path).await?;
        let records: Vec<InviteCodeRecord> = serde_json::from_str(&data)?;
        let mut by_code_hash = self.by_code_hash.write().await;
        by_code_hash.clear();
        for record in records {
            by_code_hash.insert(record.code_hash.clone(), record);
        }
        info!(count = by_code_hash.len(), "Loaded invite codes from disk");
        Ok(())
    }
}

impl Default for MemoryInviteCodeRepo {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl InviteCodeRepoPort for MemoryInviteCodeRepo {
    async fn insert_code(&self, record: InviteCodeRecord) -> ServiceResult<bool> {
        let mut by_code_hash = self.by_code_hash.write().await;
        if by_code_hash.contains_key(&record.code_hash) {
            return Ok(false);
        }
        by_code_hash.insert(record.code_hash.clone(), record);
        drop(by_code_hash);
        self.save_to_disk().await?;
        Ok(true)
    }

    async fn bind_code(
        &self,
        code_hash: &str,
        user_id: &str,
        bound_at: u64,
    ) -> ServiceResult<InviteCodeBindOutcome> {
        let mut by_code_hash = self.by_code_hash.write().await;
        let Some(existing_by_user) = by_code_hash
            .values()
            .find(|record| record.bound_user_id.as_deref() == Some(user_id))
            .cloned()
        else {
            let Some(record) = by_code_hash.get_mut(code_hash) else {
                return Ok(InviteCodeBindOutcome::Unavailable);
            };
            if record.status == InviteCodeStatus::Disabled {
                return Ok(InviteCodeBindOutcome::Unavailable);
            }
            if record.bound_user_id.as_deref() == Some(user_id) {
                return Ok(InviteCodeBindOutcome::AlreadyBoundToSameCode(record.clone()));
            }
            if let Some(bound_user_id) = record.bound_user_id.as_ref() {
                let mut cloned = record.clone();
                cloned.bound_user_id = Some(bound_user_id.clone());
                return Ok(InviteCodeBindOutcome::AlreadyBoundToDifferentCode(cloned));
            }
            record.bound_user_id = Some(user_id.to_string());
            record.bound_at = Some(bound_at);
            record.status = InviteCodeStatus::Bound;
            record.updated_at = bound_at;
            let record = record.clone();
            drop(by_code_hash);
            self.save_to_disk().await?;
            return Ok(InviteCodeBindOutcome::Bound(record));
        };
        if existing_by_user.code_hash == code_hash {
            return Ok(InviteCodeBindOutcome::AlreadyBoundToSameCode(existing_by_user));
        }
        Ok(InviteCodeBindOutcome::AlreadyBoundToDifferentCode(existing_by_user))
    }

    async fn find_by_user_id(&self, user_id: &str) -> ServiceResult<Option<InviteCodeRecord>> {
        Ok(self
            .by_code_hash
            .read()
            .await
            .values()
            .find(|record| record.bound_user_id.as_deref() == Some(user_id))
            .cloned())
    }

    async fn find_by_code_hash(&self, code_hash: &str) -> ServiceResult<Option<InviteCodeRecord>> {
        Ok(self.by_code_hash.read().await.get(code_hash).cloned())
    }
}

