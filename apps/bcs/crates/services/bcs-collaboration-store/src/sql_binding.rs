use super::*;
#[async_trait]
impl GroupRuntimeBindingRepoPort for MySqlCollaborationStore {
    async fn upsert(&self, binding: GroupRuntimeBinding) -> ServiceResult<()> {
        self.bind_default_definition(
            &binding.group_id,
            binding.group_version,
            binding.default_definition,
            Some(binding.participant_bindings),
            binding.auto_start_on_service_invocation,
        )
        .await
    }

    async fn get(&self, group_id: &str) -> ServiceResult<Option<GroupRuntimeBinding>> {
        let sql = "SELECT group_version, default_definition_id, default_definition_version, \
                          auto_start_on_service_invocation, participant_bindings_json \
                   FROM bcs_group_runtime_bindings \
                   WHERE env = ? AND group_id = ? AND record_status = 'active' \
                     AND next_group_version = ? \
                   ORDER BY group_version DESC \
                   LIMIT 1";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(group_id),
                    DbValue::from(CURRENT_GROUP_VERSION_SENTINEL),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("group runtime binding get: {error}"))
            })?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let group_version: i32 = db_get_column(&row, "group_version")
            .map_err(|error| ServiceError::InternalError(format!("group_version: {error}")))?;
        let definition_id: Option<String> = db_get_column_opt(&row, "default_definition_id")
            .map_err(|error| {
                ServiceError::InternalError(format!("default_definition_id: {error}"))
            })?;
        let definition_version: Option<i32> = db_get_column_opt(&row, "default_definition_version")
            .map_err(|error| {
                ServiceError::InternalError(format!("default_definition_version: {error}"))
            })?;
        let auto_start_on_service_invocation =
            db_get_column_opt::<i32>(&row, "auto_start_on_service_invocation")
                .map_err(|error| {
                    ServiceError::InternalError(format!(
                        "auto_start_on_service_invocation: {error}"
                    ))
                })?
                .map_or(false, |value| value != 0);
        let participant_bindings_json: Option<String> =
            db_get_column_opt(&row, "participant_bindings_json").map_err(|error| {
                ServiceError::InternalError(format!("participant_bindings_json: {error}"))
            })?;
        let participant_bindings = match participant_bindings_json {
            Some(raw) if !raw.is_empty() => serde_json::from_str(&raw).map_err(|error| {
                ServiceError::InternalError(format!("participant bindings parse: {error}"))
            })?,
            _ => BTreeMap::new(),
        };
        let default_definition = match (definition_id, definition_version) {
            (Some(id), Some(version)) => Some(CollaborationDefinitionRef { id, version }),
            _ => None,
        };
        Ok(Some(GroupRuntimeBinding {
            group_id: group_id.to_string(),
            group_version,
            default_definition,
            participant_bindings,
            auto_start_on_service_invocation,
        }))
    }

    async fn delete(&self, group_id: &str) -> ServiceResult<bool> {
        let result = self
            .db
            .execute(DbStatement::with_params(
                "DELETE FROM bcs_group_runtime_bindings WHERE env = ? AND group_id = ?",
                vec![DbValue::from(self.env.as_str()), DbValue::from(group_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("group runtime binding delete: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    async fn bind_default_definition(
        &self,
        group_id: &str,
        group_version: i32,
        definition: Option<CollaborationDefinitionRef>,
        participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
        auto_start_on_service_invocation: bool,
    ) -> ServiceResult<()> {
        let (definition_id, definition_version, content_hash, blob_id) = match definition.as_ref() {
            Some(definition) => {
                let (content_hash, blob_id) = self.definition_metadata(definition).await?;
                (
                    Some(definition.id.clone()),
                    Some(definition.version),
                    Some(content_hash),
                    blob_id,
                )
            }
            None => (None, None, None, None),
        };
        let participant_bindings_json = match participant_bindings {
            Some(bindings) if !bindings.is_empty() => {
                Some(serde_json::to_string(&bindings).map_err(|error| {
                    ServiceError::InternalError(format!("participant bindings json: {error}"))
                })?)
            }
            _ => None,
        };
        let close_previous = format!(
            "UPDATE bcs_group_runtime_bindings \
             SET next_group_version = ?, {} \
             WHERE env = ? AND group_id = ? AND record_status = 'active' \
               AND next_group_version = ? AND group_version < ?",
            self.flavor.set_modified_now()
        );
        let binding_upsert = self.flavor.on_conflict_update(
            &["env", "group_id", "group_version"],
            &[
                "next_group_version",
                "default_definition_id",
                "default_definition_version",
                "definition_content_hash",
                "definition_blob_id",
                "auto_start_on_service_invocation",
                "participant_bindings_json",
                "updated_by",
            ],
            &[
                ("record_status", "'active'"),
                ("gmt_modified", self.flavor.now()),
            ],
        );
        let upsert_current = format!(
            "INSERT INTO bcs_group_runtime_bindings \
             (env, group_id, group_version, next_group_version, \
              default_definition_id, default_definition_version, \
              definition_content_hash, definition_blob_id, \
              auto_start_on_service_invocation, participant_bindings_json, \
              record_status, updated_by) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL) \
             {binding_upsert}"
        );
        self.db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    close_previous,
                    vec![
                        DbValue::from(group_version),
                        DbValue::from(self.env.as_str()),
                        DbValue::from(group_id),
                        DbValue::from(CURRENT_GROUP_VERSION_SENTINEL),
                        DbValue::from(group_version),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    upsert_current,
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(group_id),
                        DbValue::from(group_version),
                        DbValue::from(CURRENT_GROUP_VERSION_SENTINEL),
                        DbValue::from(definition_id.as_deref()),
                        definition_version
                            .map(DbValue::from)
                            .unwrap_or(DbValue::Null),
                        DbValue::from(content_hash.as_deref()),
                        DbValue::from(blob_id.as_deref()),
                        DbValue::from(auto_start_on_service_invocation),
                        DbValue::from(participant_bindings_json.as_deref()),
                    ],
                )),
            ])
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("group runtime binding upsert: {error}"))
            })?;
        Ok(())
    }

    async fn bind_default_definition_if_current(
        &self,
        group_id: &str,
        _group_version: i32,
        expected_definition: Option<CollaborationDefinitionRef>,
        definition: Option<CollaborationDefinitionRef>,
        participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
        auto_start_on_service_invocation: bool,
    ) -> ServiceResult<bool> {
        let (definition_id, definition_version, content_hash, blob_id) = match definition.as_ref() {
            Some(definition) => {
                let (content_hash, blob_id) = self.definition_metadata(definition).await?;
                (
                    Some(definition.id.clone()),
                    Some(definition.version),
                    Some(content_hash),
                    blob_id,
                )
            }
            None => (None, None, None, None),
        };
        let participant_bindings_json = match participant_bindings {
            Some(bindings) if !bindings.is_empty() => {
                Some(serde_json::to_string(&bindings).map_err(|error| {
                    ServiceError::InternalError(format!("participant bindings json: {error}"))
                })?)
            }
            _ => None,
        };
        let expected_definition_id = expected_definition
            .as_ref()
            .map(|definition| definition.id.as_str());
        let expected_definition_version = expected_definition
            .as_ref()
            .map(|definition| definition.version);
        let sql = format!(
            "UPDATE bcs_group_runtime_bindings \
                   SET default_definition_id = ?, default_definition_version = ?, \
                       definition_content_hash = ?, definition_blob_id = ?, \
                       auto_start_on_service_invocation = ?, participant_bindings_json = ?, \
                       {} \
                   WHERE env = ? AND group_id = ? AND record_status = 'active' \
                     AND next_group_version = ? \
                     AND ( \
                       (? IS NULL AND default_definition_id IS NULL AND default_definition_version IS NULL) \
                       OR (default_definition_id = ? AND default_definition_version = ?) \
                     )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(definition_id.as_deref()),
                    definition_version
                        .map(DbValue::from)
                        .unwrap_or(DbValue::Null),
                    DbValue::from(content_hash.as_deref()),
                    DbValue::from(blob_id.as_deref()),
                    DbValue::from(auto_start_on_service_invocation),
                    DbValue::from(participant_bindings_json.as_deref()),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(group_id),
                    DbValue::from(CURRENT_GROUP_VERSION_SENTINEL),
                    DbValue::from(expected_definition_id),
                    DbValue::from(expected_definition_id),
                    expected_definition_version
                        .map(DbValue::from)
                        .unwrap_or(DbValue::Null),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("group runtime binding CAS update: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }
}
