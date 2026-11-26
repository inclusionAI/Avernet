//! Immutable workflow-revision directory for frozen FSM group workflows
//! (plan W), implementing `DistGroupDirectoryPort` over the host-selected
//! DbPlugin (SQLite migration 049 / MySQL 051, `fsm_workflow_revision`).
//!
//! One publication freezes one (scope, group) to a store-assigned immutable
//! revision: the workflow definition, the compiled validation snapshot from
//! the injected [`FsmValidatorCoreService`] (plan T5) and the frozen
//! participant-binding manifest are stored as one object row addressed by
//! `(scope, group, workflow_id, revision)`; the group's current publish
//! pointer is a separate row updated only after that object verifiably
//! exists — object first, pointer second, inside one DbPlugin transaction.
//! Object rows have no update path, so one revision identity can never
//! carry different content. Sessions pin the revision that `resolve` handed
//! them; `load_workflow` verifies scope and the canonical content digest and
//! fails closed on missing, old-format or corrupted rows, never falling back
//! to the current publication. The directory performs no GC: pinned and
//! active-context revisions stay addressable, and any future reclamation
//! must prove a revision unreferenced (no Session spec pin and no live
//! recoverable context) before removing it.
//!
//! DB cost per method (SQLite/MySQL, one DbPlugin connection):
//! - `publish` (new content): 1 indexed pointer read + 1 transaction of two
//!   writes (object INSERT, pointer INSERT-or-guarded UPDATE); O(1) rows.
//! - `publish` (identical retry): 2 indexed reads (pointer + object digest
//!   verification), no writes — AlreadyApplied with the same revision.
//! - `resolve`: 2 indexed reads (pointer, then object at the pointer's
//!   revision), each at most one row; digest is recomputed from that row.
//! - `load_workflow`: 1 indexed read (`load_key_hash` + digest filter,
//!   deterministic `ORDER BY group_id LIMIT 1`), then in-memory digest
//!   verification over the single row's bytes. No full scans anywhere.
use crate::identity::{digest_fields, digest_json, hex_of};
use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbRow, DbStatement, DbTransactionStep, DbValue};
use bcs_dist_model::{
    Digest, GroupId, ParticipantBinding, SessionKind, SessionLimits, SessionSpec, ShardId, Sid,
    TaskScope, TimestampMillis, WorkflowRef,
};
use bcs_domain::collaboration::StateMachineDefinition;
use bcs_service_api::core::dist_runner::{
    FsmCapabilities, FsmValidatorCoreService, ValidatedWorkflow,
};
use bcs_service_api::port::dist_agent::{
    DistGroupDirectoryPort, FrozenGroupDefinition, GroupDirectoryError,
};
use bcs_service_api::port::repo::distributed::common::{IdempotentWrite, StoreError};
use serde::de::DeserializeOwned;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

/// Stored row format of the revision object. A row carrying any other
/// marker is an old format and fails closed instead of being guessed.
pub const REVISION_CONTENT_FORMAT: &str = "v1";
/// Stored format of the compiled validation snapshot.
const COMPILED_CONTENT_FORMAT: &str = "v1";

pub const CODE_WORKFLOW_REVISION_KEY_INVALID: &str = "workflow_revision_key_invalid";
pub const CODE_WORKFLOW_REVISION_CORRUPT: &str = "workflow_revision_corrupt";
pub const CODE_WORKFLOW_REVISION_TAKEN: &str = "workflow_revision_taken";
pub const CODE_WORKFLOW_PUBLICATION_INVALID: &str = "workflow_publication_invalid";

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.to_owned() }
}

fn not_applied(code: &str) -> StoreError {
    StoreError::NotApplied { code: code.to_owned() }
}

/// Canonical composite content digest of one publication: the very digest a
/// pinned `WorkflowRef` carries and every load re-verifies. Sorted-key
/// canonicalization (`digest_json`) makes it independent of provider byte
/// layout drift.
pub fn composite_content_digest(
    definition: &serde_json::Value,
    participants: &serde_json::Value,
    limits: &serde_json::Value,
) -> Digest {
    digest_json(&serde_json::json!({
        "composite": "fsm-workflow-revision:v1",
        "definition": definition,
        "participants": participants,
        "limits": limits,
    }))
}

fn validate_scope(scope: &TaskScope) -> Result<(), StoreError> {
    if scope.tenant.0.is_empty() || scope.principal.0.is_empty() {
        return Err(invalid(CODE_WORKFLOW_REVISION_KEY_INVALID));
    }
    Ok(())
}

fn validate_key(scope: &TaskScope, group: &GroupId, workflow_id: &str) -> Result<(), StoreError> {
    validate_scope(scope)?;
    if group.0.is_empty() || workflow_id.is_empty() {
        return Err(invalid(CODE_WORKFLOW_REVISION_KEY_INVALID));
    }
    Ok(())
}

/// Unique object identity of one immutable revision: binds (scope, group,
/// workflow, revision). Doubles as the unique key of the revision row.
pub fn revision_key_id(
    scope: &TaskScope,
    group: &GroupId,
    workflow_id: &str,
    revision: &str,
) -> String {
    let fields = [
        "fsm-workflow-revision:v1".as_bytes(),
        scope.tenant.0.as_bytes(),
        scope.principal.0.as_bytes(),
        group.0.as_bytes(),
        workflow_id.as_bytes(),
        revision.as_bytes(),
    ];
    hex_of(&digest_fields(&fields)).to_owned()
}

/// Load address of a pinned revision: the group travels inside the session's
/// frozen definition, and `load_workflow` verifies the digest instead, so
/// the load index binds (scope, workflow, revision) without the group.
pub fn load_key_id(scope: &TaskScope, workflow_id: &str, revision: &str) -> String {
    let fields = [
        "fsm-workflow-load:v1".as_bytes(),
        scope.tenant.0.as_bytes(),
        scope.principal.0.as_bytes(),
        workflow_id.as_bytes(),
        revision.as_bytes(),
    ];
    hex_of(&digest_fields(&fields)).to_owned()
}

/// Unique pointer identity of one group's current publication.
fn pointer_key_id(scope: &TaskScope, group: &GroupId) -> String {
    let fields = [
        "fsm-group-publication:v1".as_bytes(),
        scope.tenant.0.as_bytes(),
        scope.principal.0.as_bytes(),
        group.0.as_bytes(),
    ];
    hex_of(&digest_fields(&fields)).to_owned()
}

fn now_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|since| since.as_millis() as i64)
        .unwrap_or(0)
}

/// Column reader with the corrupt-row error already in scope.
fn text_of(row: &DbRow, column: &str) -> Result<String, GroupDirectoryError> {
    row.get_string(column)
        .map_err(|_| GroupDirectoryError::InvalidRevision)?
        .ok_or_else(|| GroupDirectoryError::InvalidRevision)
}

/// Verify the stored row's scope identity against the caller's trusted scope.
fn stored_scope_of(
    row: &DbRow,
    scope: &TaskScope,
) -> Result<(), GroupDirectoryError> {
    if text_of(row, "scope_tenant")? != scope.tenant.0
        || text_of(row, "scope_principal")? != scope.principal.0
    {
        return Err(GroupDirectoryError::InvalidRevision);
    }
    Ok(())
}

fn stored<T: DeserializeOwned>(json: &str) -> Result<T, GroupDirectoryError> {
    serde_json::from_str(json).map_err(|_| GroupDirectoryError::InvalidRevision)
}

/// The persisted revision object as loaded for a directory read.
struct RevisionRow {
    content_digest: String,
    definition: StateMachineDefinition,
    participants: Vec<ParticipantBinding>,
    limits: SessionLimits,
}

/// Read one revision object row by its full key. Identity columns are
/// re-checked against the caller's scope; the stored content digest is never
/// trusted — it is recomputed from the stored bytes.
async fn read_revision_row(
    db: &dyn DbPlugin,
    sql: &str,
    params: Vec<DbValue>,
    scope: &TaskScope,
) -> Result<Option<RevisionRow>, GroupDirectoryError> {
    let rows = db
        .query(DbStatement::with_params(sql, params))
        .await
        .map_err(|_| GroupDirectoryError::Unavailable)?;
    let Some(row) = rows.into_iter().next() else {
        return Ok(None);
    };
    stored_scope_of(&row, scope)?;
    if text_of(&row, "content_format")? != REVISION_CONTENT_FORMAT {
        // Old or unknown stored format: an explicit error, never a guess.
        return Err(GroupDirectoryError::InvalidRevision);
    }
    let definition: StateMachineDefinition = stored(&text_of(&row, "definition_json")?)?;
    let participants: Vec<ParticipantBinding> = stored(&text_of(&row, "bindings_json")?)?;
    let limits: SessionLimits = stored(&text_of(&row, "limits_json")?)?;
    let content_digest = composite_content_digest(
        &serde_json::to_value(&definition).map_err(|_| GroupDirectoryError::InvalidRevision)?,
        &serde_json::to_value(&participants).map_err(|_| GroupDirectoryError::InvalidRevision)?,
        &serde_json::to_value(&limits).map_err(|_| GroupDirectoryError::InvalidRevision)?,
    );
    // The recomputed digest must match the digest stored with the object;
    // tampered bytes break here, never at the caller.
    if content_digest.0 != text_of(&row, "content_digest")? {
        return Err(GroupDirectoryError::InvalidRevision);
    }
    Ok(Some(RevisionRow {
        content_digest: content_digest.0,
        definition,
        participants,
        limits,
    }))
}

/// The DbPlugin-backed immutable workflow-revision directory.
pub struct DbWorkflowRevisionStore {
    db: Arc<dyn DbPlugin>,
    validator: Arc<dyn FsmValidatorCoreService>,
    capabilities: FsmCapabilities,
}

impl DbWorkflowRevisionStore {
    /// The caller runs the dialect's `fsm_workflow_revision` migration
    /// first; a missing table surfaces as an honest store error, never as
    /// empty data. The shared validator (plan T5) gates every publication
    /// before anything is stored.
    pub fn new(
        db: Arc<dyn DbPlugin>,
        validator: Arc<dyn FsmValidatorCoreService>,
        capabilities: FsmCapabilities,
    ) -> Result<Self, StoreError> {
        Ok(Self {
            db,
            validator,
            capabilities,
        })
    }

    /// Publish write side: freeze one (scope, group) to a new store-assigned
    /// immutable revision and move the current publish pointer to it. The
    /// revision identity (monotonic per (scope, group) in publication order)
    /// and the canonical content digest are assigned here; callers never
    /// propose them. Object row first, pointer second, in one DbPlugin
    /// transaction. An identical retry of the current content republishes
    /// the SAME revision with no writes.
    pub async fn publish(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        definition_id: &str,
        definition: &StateMachineDefinition,
        participants: &[ParticipantBinding],
        limits: &SessionLimits,
    ) -> Result<(WorkflowRef, IdempotentWrite), StoreError> {
        validate_key(scope, group, definition_id)?;
        if participants.is_empty() {
            return Err(invalid(CODE_WORKFLOW_PUBLICATION_INVALID));
        }

        let definition_value = serde_json::to_value(definition)
            .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;
        let participants_value = serde_json::to_value(participants)
            .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;
        let limits_value =
            serde_json::to_value(limits).map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;
        let content_digest =
            composite_content_digest(&definition_value, &participants_value, &limits_value);

        // The publication gate (plan T5): a rejection happens before any
        // write exists. The synthesized spec is validation-only input; the
        // real WorkflowRef is assigned below and pinned into the compiled
        // snapshot.
        let validated = self
            .validator
            .validate(
                &SessionSpec {
                    schema_version: 1,
                    sid: Sid("workflow-publication".into()),
                    shard: ShardId(0),
                    scope: scope.clone(),
                    kind: SessionKind::FsmGroup,
                    group: group.clone(),
                    workflow: WorkflowRef {
                        definition_id: definition_id.to_owned(),
                        revision: String::new(),
                        digest: content_digest.clone(),
                    },
                    participants: participants.to_vec(),
                    limits: limits.clone(),
                    digest: content_digest.clone(),
                    created_at: TimestampMillis(0),
                },
                definition,
                &self.capabilities,
            )
            .map_err(|e| invalid(&format!("{CODE_WORKFLOW_PUBLICATION_INVALID}:{e}")))?;

        // Current pointer: the idempotency basis and the revision counter.
        let pointer_key = pointer_key_id(scope, group);
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT workflow_id, current_revision, current_digest, revision_counter, \
                 scope_tenant, scope_principal, group_id \
                 FROM bcs_fsm_group_publications WHERE pointer_key_hash = ? LIMIT 1",
                vec![DbValue::String(pointer_key.clone())],
            ))
            .await
            .map_err(|_| not_applied("workflow_pointer_read"))?;

        let mut next_number: u64 = 1;
        if let Some(row) = rows.into_iter().next() {
            let tenant = row
                .get_string("scope_tenant")
                .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?
                .ok_or_else(|| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;
            let principal = row
                .get_string("scope_principal")
                .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?
                .ok_or_else(|| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;
            let stored_group = row
                .get_string("group_id")
                .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?
                .ok_or_else(|| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;
            if tenant != scope.tenant.0
                || principal != scope.principal.0
                || stored_group != group.0
            {
                return Err(invalid("workflow_publication_hash_collision"));
            }
            let workflow_id = row
                .get_string("workflow_id")
                .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?
                .ok_or_else(|| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;
            let current_revision = row
                .get_string("current_revision")
                .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?
                .ok_or_else(|| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;
            let current_digest = row
                .get_string("current_digest")
                .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?
                .ok_or_else(|| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;
            let counter = u64::try_from(
                row.get_i64("revision_counter")
                    .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?
                    .ok_or_else(|| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?,
            )
            .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;

            // Identical retry of the current publication: verify the pinned
            // object still holds the exact content, then answer the same
            // reference with zero writes. A torn pointer (unreadable object)
            // is an honest error, never AlreadyApplied.
            if workflow_id == definition_id && current_digest == content_digest.0 {
                let reference = WorkflowRef {
                    definition_id: workflow_id,
                    revision: current_revision,
                    digest: Digest(current_digest),
                };
                self.verify_current_object(scope, group, &reference, &content_digest)
                    .await?;
                return Ok((reference, IdempotentWrite::AlreadyApplied));
            }
            next_number = counter
                .checked_add(1)
                .ok_or_else(|| invalid(CODE_WORKFLOW_REVISION_TAKEN))?;
        }

        let reference = WorkflowRef {
            definition_id: definition_id.to_owned(),
            revision: next_number.to_string(),
            digest: content_digest.clone(),
        };
        let mut compiled: ValidatedWorkflow = validated;
        compiled.pinned = reference.clone();
        let definition_json = serde_json::to_string(definition)
            .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;
        let bindings_json = serde_json::to_string(participants)
            .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;
        let limits_json = serde_json::to_string(limits)
            .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;
        let compiled_json = serde_json::to_string(&compiled)
            .map_err(|_| invalid(CODE_WORKFLOW_REVISION_CORRUPT))?;

        let object_insert = DbStatement::with_params(
            "INSERT INTO bcs_fsm_workflow_revisions \
             (revision_key_hash, load_key_hash, scope_tenant, scope_principal, group_id, \
              workflow_id, revision, content_format, content_digest, definition_json, \
              bindings_json, limits_json, compiled_format, compiled_json, published_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            vec![
                DbValue::String(revision_key_id(scope, group, definition_id, &reference.revision)),
                DbValue::String(load_key_id(scope, definition_id, &reference.revision)),
                DbValue::String(scope.tenant.0.clone()),
                DbValue::String(scope.principal.0.clone()),
                DbValue::String(group.0.clone()),
                DbValue::String(definition_id.to_owned()),
                DbValue::String(reference.revision.clone()),
                DbValue::String(REVISION_CONTENT_FORMAT.to_owned()),
                DbValue::String(content_digest.0.clone()),
                DbValue::String(definition_json),
                DbValue::String(bindings_json),
                DbValue::String(limits_json),
                DbValue::String(COMPILED_CONTENT_FORMAT.to_owned()),
                DbValue::String(compiled_json),
                DbValue::I64(now_millis()),
            ],
        );
        let outcome = match next_number {
            1 => {
                // First publication: create the pointer row after the
                // object row, in the same transaction.
                let pointer_insert = DbStatement::with_params(
                    "INSERT INTO bcs_fsm_group_publications \
                     (pointer_key_hash, scope_tenant, scope_principal, group_id, workflow_id, \
                      current_revision, current_digest, revision_counter, updated_at) \
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    vec![
                        DbValue::String(pointer_key),
                        DbValue::String(scope.tenant.0.clone()),
                        DbValue::String(scope.principal.0.clone()),
                        DbValue::String(group.0.clone()),
                        DbValue::String(definition_id.to_owned()),
                        DbValue::String(reference.revision.clone()),
                        DbValue::String(content_digest.0.clone()),
                        DbValue::U64(next_number),
                        DbValue::I64(now_millis()),
                    ],
                );
                self.db
                    .transaction(vec![
                        DbTransactionStep::Execute(object_insert),
                        DbTransactionStep::Execute(pointer_insert),
                    ])
                    .await
            }
            _ => {
                // Guard the pointer move on the counter this publication was
                // derived from: a concurrent publication that won the same
                // next revision first (unique object identity) or moved the
                // counter rolls this whole transaction back — never an
                // in-place overwrite.
                let counter_guard = next_number - 1;
                let pointer_update = DbStatement::with_params(
                    "UPDATE bcs_fsm_group_publications SET workflow_id = ?, current_revision = ?, \
                     current_digest = ?, revision_counter = ?, updated_at = ? \
                     WHERE pointer_key_hash = ? AND revision_counter = ?",
                    vec![
                        DbValue::String(definition_id.to_owned()),
                        DbValue::String(reference.revision.clone()),
                        DbValue::String(content_digest.0.clone()),
                        DbValue::U64(next_number),
                        DbValue::I64(now_millis()),
                        DbValue::String(pointer_key),
                        DbValue::U64(counter_guard),
                    ],
                );
                self.db
                    .transaction(vec![
                        DbTransactionStep::Execute(object_insert),
                        DbTransactionStep::ExecuteChecked {
                            statement: pointer_update,
                            expected_affected_rows: 1,
                        },
                    ])
                    .await
            }
        };
        // A counter-guard miss means a concurrent publication won this
        // revision number first; any other failure is an honest refused
        // write. Both roll back atomically, so neither is a guessed success.
        match outcome {
            Ok(_) => {}
            Err(bcs_db_api::DbError::ConditionFailed { .. }) => {
                return Err(not_applied(CODE_WORKFLOW_REVISION_TAKEN))
            }
            Err(_) => return Err(not_applied("workflow_publication_write")),
        }
        Ok((reference, IdempotentWrite::Applied))
    }

    /// Verify the pointer's pinned object still holds exactly the digest the
    /// pointer claims. Used for the AlreadyApplied answer so republish can
    /// never confirm over a torn pointer.
    async fn verify_current_object(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        reference: &WorkflowRef,
        content_digest: &Digest,
    ) -> Result<(), StoreError> {
        let row = read_revision_row(
            self.db.as_ref(),
            "SELECT content_format, content_digest, definition_json, bindings_json, limits_json, \
             scope_tenant, scope_principal FROM bcs_fsm_workflow_revisions \
             WHERE revision_key_hash = ? LIMIT 1",
            vec![DbValue::String(revision_key_id(
                scope,
                group,
                &reference.definition_id,
                &reference.revision,
            ))],
            scope,
        )
        .await
        .map_err(|_| not_applied(CODE_WORKFLOW_REVISION_CORRUPT))?;
        match row {
            Some(row) if row.content_digest == content_digest.0 => Ok(()),
            _ => Err(not_applied(CODE_WORKFLOW_REVISION_CORRUPT)),
        }
    }
}

#[async_trait]
impl DistGroupDirectoryPort for DbWorkflowRevisionStore {
    /// Resolve/freeze only for a new session: return the group's current
    /// publication with the exact workflow reference the session pins.
    async fn resolve(
        &self,
        scope: &TaskScope,
        group: &GroupId,
    ) -> Result<FrozenGroupDefinition, GroupDirectoryError> {
        validate_scope(scope).map_err(|_| GroupDirectoryError::NotFound)?;
        if group.0.is_empty() {
            return Err(GroupDirectoryError::NotFound);
        }
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT workflow_id, current_revision, current_digest, \
                 scope_tenant, scope_principal, group_id \
                 FROM bcs_fsm_group_publications WHERE pointer_key_hash = ? LIMIT 1",
                vec![DbValue::String(pointer_key_id(scope, group))],
            ))
            .await
            .map_err(|_| GroupDirectoryError::Unavailable)?;
        let Some(pointer) = rows.into_iter().next() else {
            return Err(GroupDirectoryError::NotFound);
        };
        // The pointer row is only an address: identity columns must match
        // the caller's trusted scope before it authorizes anything.
        let tenant = text_of(&pointer, "scope_tenant")?;
        let principal = text_of(&pointer, "scope_principal")?;
        if tenant != scope.tenant.0 || principal != scope.principal.0 {
            return Err(GroupDirectoryError::NotFound);
        }

        let workflow_id = text_of(&pointer, "workflow_id")?;
        let current_revision = text_of(&pointer, "current_revision")?;
        let current_digest = text_of(&pointer, "current_digest")?;
        let reference = WorkflowRef {
            definition_id: workflow_id.clone(),
            revision: current_revision.clone(),
            digest: Digest(current_digest),
        };
        // The pointer must never hand out an unreadable object: the current
        // revision row is loaded and digest-verified before resolving.
        let Some(row) = read_revision_row(
            self.db.as_ref(),
            "SELECT content_format, content_digest, definition_json, bindings_json, \
             limits_json, scope_tenant, scope_principal FROM bcs_fsm_workflow_revisions \
             WHERE revision_key_hash = ? LIMIT 1",
            vec![DbValue::String(revision_key_id(scope, group, &workflow_id, &current_revision))],
            scope,
        )
        .await?
        else {
            return Err(GroupDirectoryError::InvalidRevision);
        };
        if row.content_digest != reference.digest.0 {
            return Err(GroupDirectoryError::InvalidRevision);
        }
        Ok(FrozenGroupDefinition {
            group: group.clone(),
            workflow: reference,
            participants: row.participants,
            limits: row.limits,
        })
    }

    /// Load the exact immutable revision and verify its digest, never the
    /// current group configuration. The group travels inside the session's
    /// frozen definition; the digest selects the one matching row.
    async fn load_workflow(
        &self,
        scope: &TaskScope,
        reference: &WorkflowRef,
    ) -> Result<StateMachineDefinition, GroupDirectoryError> {
        validate_scope(scope).map_err(|_| GroupDirectoryError::InvalidRevision)?;
        if reference.definition_id.is_empty()
            || reference.revision.is_empty()
            || reference.digest.0.is_empty()
        {
            return Err(GroupDirectoryError::InvalidRevision);
        }
        // Same (scope, workflow, revision) may exist for several groups with
        // different content: the pinned digest picks the row and every load
        // re-derives the digest from bytes; a missing or mismatching row is
        // an explicit error, never a fallback to the current publication.
        let row = read_revision_row(
            self.db.as_ref(),
            "SELECT content_format, content_digest, definition_json, bindings_json, \
             limits_json, scope_tenant, scope_principal FROM bcs_fsm_workflow_revisions \
             WHERE load_key_hash = ? AND content_digest = ? \
             ORDER BY group_id LIMIT 1",
            vec![
                DbValue::String(load_key_id(scope, &reference.definition_id, &reference.revision)),
                DbValue::String(reference.digest.0.clone()),
            ],
            scope,
        )
        .await?;
        match row {
            Some(row) => Ok(row.definition),
            None => Err(GroupDirectoryError::InvalidRevision),
        }
    }
}
