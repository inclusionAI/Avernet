//! SQL persistence for collaboration drafts. Every operation is a bounded point
//! lookup or transaction, except explicit deletion of one draft's revisions.
use async_trait::async_trait;
use bcs_db_api::{DbError, DbPlugin, DbStatement, DbTransactionStep, DbValue, db_get_column};
use bcs_service_api::{
    port::repo::collaboration_draft::{CollaborationDraftRepoPort, DraftRepoError},
    types::collaboration_draft::{DraftMetadata, StoredDraftRevision},
};
use std::sync::Arc;

pub struct DbCollaborationDraftStore {
    db: Arc<dyn DbPlugin>,
}

impl DbCollaborationDraftStore {
    pub fn new(db: Arc<dyn DbPlugin>) -> Self {
        Self { db }
    }
    fn key(&self, id: &str) -> Vec<DbValue> {
        vec![id.to_string().into()]
    }
    fn insert_revision(&self, r: &StoredDraftRevision) -> Result<DbStatement, DraftRepoError> {
        Ok(DbStatement::with_params(
            "INSERT INTO bcs_collaboration_draft_revisions (draft_id,revision,payload_json,revoked) VALUES (?,?,?,0)",
            vec![
                r.content.draft_id.clone().into(),
                (r.content.revision as i64).into(),
                serde_json::to_string(r)
                    .map_err(|_| DraftRepoError::Failed)?
                    .into(),
            ],
        ))
    }
}

fn failure(e: DbError) -> DraftRepoError {
    if e.is_duplicate_key() || matches!(e, DbError::ConditionFailed { .. }) {
        DraftRepoError::Conflict
    } else {
        // SQL and driver errors can contain input values. Never return them to callers.
        DraftRepoError::Failed
    }
}

#[async_trait]
impl CollaborationDraftRepoPort for DbCollaborationDraftStore {
    async fn metadata(&self, id: &str) -> Result<Option<DraftMetadata>, DraftRepoError> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT draft_id,owner_bot_id,head_revision,created_at,deleted FROM bcs_collaboration_drafts WHERE draft_id=?",
            self.key(id),
        )).await.map_err(failure)?;
        rows.first()
            .map(|r| {
                Ok(DraftMetadata {
                    draft_id: db_get_column(r, "draft_id").map_err(failure)?,
                    owner_bot_id: db_get_column(r, "owner_bot_id").map_err(failure)?,
                    head_revision: db_get_column::<i64>(r, "head_revision").map_err(failure)?
                        as u64,
                    created_at: db_get_column(r, "created_at").map_err(failure)?,
                    deleted: db_get_column::<i64>(r, "deleted").map_err(failure)? != 0,
                })
            })
            .transpose()
    }

    async fn revision(
        &self,
        id: &str,
        revision: u64,
    ) -> Result<Option<StoredDraftRevision>, DraftRepoError> {
        let mut params = self.key(id);
        params.push((revision as i64).into());
        let rows = self.db.query(DbStatement::with_params(
            "SELECT r.payload_json,r.revoked FROM bcs_collaboration_draft_revisions r JOIN bcs_collaboration_drafts d ON d.draft_id=r.draft_id WHERE r.draft_id=? AND r.revision=? AND d.deleted=0",
            params,
        )).await.map_err(failure)?;
        rows.first()
            .map(|row| {
                let json: String = db_get_column(row, "payload_json").map_err(failure)?;
                let mut revision: StoredDraftRevision =
                    serde_json::from_str(&json).map_err(|_| DraftRepoError::Failed)?;
                revision.revoked = db_get_column::<i64>(row, "revoked").map_err(failure)? != 0;
                Ok(revision)
            })
            .transpose()
    }

    async fn create(&self, m: DraftMetadata, r: StoredDraftRevision) -> Result<(), DraftRepoError> {
        self.db.transaction(vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                "INSERT INTO bcs_collaboration_drafts (draft_id,owner_bot_id,head_revision,created_at,deleted) VALUES (?,?,1,?,0)",
                vec![m.draft_id.into(), m.owner_bot_id.into(), m.created_at.into()],
            )),
            DbTransactionStep::Execute(self.insert_revision(&r)?),
        ]).await.map_err(failure)?;
        Ok(())
    }

    async fn append(
        &self,
        owner: &str,
        expected: u64,
        r: StoredDraftRevision,
    ) -> Result<(), DraftRepoError> {
        let mut params = self.key(&r.content.draft_id);
        params.extend([owner.to_string().into(), (expected as i64).into()]);
        self.db.transaction(vec![
            DbTransactionStep::ExecuteChecked {
                statement: DbStatement::with_params(
                    "UPDATE bcs_collaboration_drafts SET head_revision=head_revision+1 WHERE draft_id=? AND owner_bot_id=? AND head_revision=? AND deleted=0",
                    params,
                ),
                expected_affected_rows: 1,
            },
            DbTransactionStep::Execute(self.insert_revision(&r)?),
        ]).await.map_err(failure)?;
        Ok(())
    }

    async fn revoke(&self, owner: &str, id: &str, revision: u64) -> Result<(), DraftRepoError> {
        let mut params = self.key(id);
        params.extend([(revision as i64).into(), owner.to_string().into()]);
        self.db.execute(DbStatement::with_params(
            "UPDATE bcs_collaboration_draft_revisions SET revoked=1 WHERE draft_id=? AND revision=? AND EXISTS (SELECT 1 FROM bcs_collaboration_drafts d WHERE d.draft_id=bcs_collaboration_draft_revisions.draft_id AND d.owner_bot_id=? AND d.deleted=0)",
            params,
        )).await.map_err(failure)?;
        Ok(())
    }

    async fn delete(&self, owner: &str, id: &str) -> Result<(), DraftRepoError> {
        let mut params = self.key(id);
        params.push(owner.to_string().into());
        self.db.transaction(vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                "UPDATE bcs_collaboration_drafts SET deleted=1,head_revision=0 WHERE draft_id=? AND owner_bot_id=?",
                params.clone(),
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                "DELETE FROM bcs_collaboration_draft_revisions WHERE draft_id=? AND EXISTS (SELECT 1 FROM bcs_collaboration_drafts d WHERE d.draft_id=bcs_collaboration_draft_revisions.draft_id AND d.owner_bot_id=? AND d.deleted=1)",
                params,
            )),
        ]).await.map_err(failure)?;
        Ok(())
    }
}
