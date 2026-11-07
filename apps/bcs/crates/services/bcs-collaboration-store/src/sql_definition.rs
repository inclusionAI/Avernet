use super::*;
impl MySqlCollaborationStore {

    async fn upsert_definition_internal(
        &self,
        definition: CollaborationDefinition,
        source_yaml: Option<String>,
    ) -> ServiceResult<()> {
        let normalized_json = definition_json(&definition)?;
        let metadata_json = serde_json::to_string(&definition.metadata).map_err(|error| {
            ServiceError::InternalError(format!("definition metadata serialize: {error}"))
        })?;
        let content_hash = sha256_hex(normalized_json.as_bytes());
        let definition_ref = CollaborationDefinitionRef {
            id: definition.id.clone(),
            version: definition.version,
        };
        if let Some((existing_hash, _, existing_yaml)) = self
            .find_definition_record_metadata(&definition_ref)
            .await?
        {
            if existing_hash != content_hash {
                return Err(definition_conflict_error(
                    &definition.id,
                    definition.version,
                    &existing_hash,
                    &content_hash,
                ));
            }
            if let Some(source_yaml) = source_yaml.as_deref() {
                if let Some(existing_yaml) =
                    existing_yaml.as_deref().filter(|value| !value.is_empty())
                {
                    if existing_yaml != source_yaml {
                        return Err(ServiceError::Conflict(format!(
                            "CollaborationDefinition '{}@{}' already exists with different source YAML",
                            definition.id, definition.version
                        )));
                    }
                } else {
                    let sql = format!(
                        "UPDATE bcs_collaboration_definitions \
                         SET source_format = 'yaml', yaml_text = ?, {} \
                         WHERE env = ? AND definition_id = ? AND version = ? \
                           AND record_status = 'active'",
                        self.flavor.set_modified_now()
                    );
                    self.db
                        .execute(DbStatement::with_params(
                            sql,
                            vec![
                                DbValue::from(source_yaml),
                                DbValue::from(self.env.as_str()),
                                DbValue::from(definition.id.as_str()),
                                DbValue::from(definition.version),
                            ],
                        ))
                        .await
                        .map_err(|error| {
                            ServiceError::InternalError(format!(
                                "collaboration definition source backfill: {error}"
                            ))
                        })?;
                }
            }
            return Ok(());
        }
        let blob_id = format!(
            "collab-def:{}:{}:{}",
            definition.id,
            definition.version,
            &content_hash[..16]
        );
        let description = definition.metadata.description.as_deref();
        let source_format = if source_yaml.is_some() {
            "yaml"
        } else {
            "json"
        };
        let blob_upsert = self.flavor.on_conflict_update(
            &["env", "blob_id"],
            &[
                "content_hash",
                "content_encoding",
                "content_size",
                "content",
                "external_uri",
            ],
            &[("gmt_modified", self.flavor.now())],
        );
        let blob_sql = format!(
            "INSERT INTO bcs_collaboration_definition_blobs \
             (env, blob_id, content_hash, content_encoding, content_size, \
              content, external_uri, created_by) \
             VALUES (?, ?, ?, 'identity', ?, ?, NULL, NULL) \
             {blob_upsert}"
        );
        let insert_ignore = self.flavor.insert_or_ignore();
        let sql = format!(
            "{insert_ignore} INTO bcs_collaboration_definitions \
             (env, definition_id, version, name, description, source_format, \
              content_hash, blob_id, yaml_text, normalized_json, metadata_json, \
              record_status, created_by) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL)"
        );
        self.db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    blob_sql,
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(blob_id.as_str()),
                        DbValue::from(content_hash.as_str()),
                        DbValue::from(normalized_json.len() as u64),
                        DbValue::from(normalized_json.as_bytes().to_vec()),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    sql,
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(definition.id.as_str()),
                        DbValue::from(definition.version),
                        DbValue::from(definition.name.as_str()),
                        DbValue::from(description),
                        DbValue::from(source_format),
                        DbValue::from(content_hash.as_str()),
                        DbValue::from(blob_id.as_str()),
                        DbValue::from(source_yaml.as_deref()),
                        DbValue::from(normalized_json.as_str()),
                        DbValue::from(metadata_json.as_str()),
                    ],
                )),
            ])
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("collaboration definition upsert: {error}"))
            })?;
        let Some((stored_hash, _, _)) = self
            .find_definition_record_metadata(&definition_ref)
            .await?
        else {
            return Err(ServiceError::InternalError(format!(
                "collaboration definition insert did not create row: {}@{}",
                definition.id, definition.version
            )));
        };
        if stored_hash != content_hash {
            return Err(definition_conflict_error(
                &definition.id,
                definition.version,
                &stored_hash,
                &content_hash,
            ));
        }
        Ok(())
    }

    async fn find_definition_metadata(
        &self,
        definition: &CollaborationDefinitionRef,
    ) -> ServiceResult<Option<(String, Option<String>)>> {
        Ok(self
            .find_definition_record_metadata(definition)
            .await?
            .map(|(content_hash, blob_id, _)| (content_hash, blob_id)))
    }

    async fn find_definition_record_metadata(
        &self,
        definition: &CollaborationDefinitionRef,
    ) -> ServiceResult<Option<(String, Option<String>, Option<String>)>> {
        let sql = "SELECT content_hash, blob_id, yaml_text \
                   FROM bcs_collaboration_definitions \
                   WHERE env = ? AND definition_id = ? AND version = ? \
                     AND record_status = 'active' \
                   LIMIT 1";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(definition.id.as_str()),
                    DbValue::from(definition.version),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("collaboration definition lookup: {error}"))
            })?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let content_hash: String = db_get_column(&row, "content_hash")
            .map_err(|error| ServiceError::InternalError(format!("content_hash: {error}")))?;
        let blob_id: Option<String> = db_get_column_opt(&row, "blob_id")
            .map_err(|error| ServiceError::InternalError(format!("blob_id: {error}")))?;
        let yaml_text: Option<String> = db_get_column_opt(&row, "yaml_text")
            .map_err(|error| ServiceError::InternalError(format!("yaml_text: {error}")))?;
        Ok(Some((content_hash, blob_id, yaml_text)))
    }

    pub(super) async fn definition_metadata(
        &self,
        definition: &CollaborationDefinitionRef,
    ) -> ServiceResult<(String, Option<String>)> {
        self.find_definition_metadata(definition)
            .await?
            .ok_or_else(|| {
                ServiceError::InternalError(format!(
                    "collaboration definition not found for binding: {}@{}",
                    definition.id, definition.version
                ))
            })
    }
}


#[async_trait]
impl StateMachineDefinitionRepoPort for MySqlCollaborationStore {
    async fn upsert(&self, definition: CollaborationDefinition) -> ServiceResult<()> {
        self.upsert_definition_internal(definition, None).await
    }

    async fn upsert_with_source_yaml(
        &self,
        definition: CollaborationDefinition,
        source_yaml: String,
    ) -> ServiceResult<()> {
        self.upsert_definition_internal(definition, Some(source_yaml))
            .await
    }

    async fn get(&self, id: &str, version: i32) -> ServiceResult<Option<CollaborationDefinition>> {
        Ok(self
            .get_record(id, version)
            .await?
            .map(|record| record.definition))
    }

    async fn get_record(
        &self,
        id: &str,
        version: i32,
    ) -> ServiceResult<Option<CollaborationDefinitionRecord>> {
        let sql = "SELECT normalized_json, yaml_text, source_format, content_hash \
                   FROM bcs_collaboration_definitions \
                   WHERE env = ? AND definition_id = ? AND version = ? \
                     AND record_status = 'active' \
                   LIMIT 1";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(id),
                    DbValue::from(version),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("collaboration definition get: {error}"))
            })?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let source_format: Option<String> = db_get_column_opt(&row, "source_format")
            .map_err(|error| ServiceError::InternalError(format!("source_format: {error}")))?;
        let content_hash: Option<String> = db_get_column_opt(&row, "content_hash")
            .map_err(|error| ServiceError::InternalError(format!("content_hash: {error}")))?;
        let yaml_text: Option<String> = db_get_column_opt(&row, "yaml_text")
            .map_err(|error| ServiceError::InternalError(format!("yaml_text: {error}")))?;
        let normalized_json: Option<String> = db_get_column_opt(&row, "normalized_json")
            .map_err(|error| ServiceError::InternalError(format!("normalized_json: {error}")))?;
        let definition = if let Some(raw) = normalized_json.filter(|raw| !raw.is_empty()) {
            serde_json::from_str(&raw).map_err(|error| {
                ServiceError::InternalError(format!("definition json parse: {error}"))
            })?
        } else if let Some(raw) = yaml_text.as_deref().filter(|raw| !raw.is_empty()) {
            serde_yaml::from_str(raw).map_err(|error| {
                ServiceError::InternalError(format!("definition yaml parse: {error}"))
            })?
        } else {
            return Ok(None);
        };
        Ok(Some(CollaborationDefinitionRecord {
            definition,
            source_format,
            yaml_text,
            content_hash,
        }))
    }

    async fn save_run_snapshot(
        &self,
        run: &StateMachineRun,
        group_version: i32,
        definition: &CollaborationDefinition,
        resolved_participant_bindings: Option<&BTreeMap<String, ResolvedParticipantBinding>>,
        execution_plan: Option<&StateMachineExecutionPlanSnapshot>,
    ) -> ServiceResult<()> {
        validate_snapshot_envelope(definition, execution_plan)?;
        let execution_plan_json = execution_plan
            .map(|snapshot| serde_json::to_string(&snapshot.plan))
            .transpose()
            .map_err(|error| ServiceError::InternalError(format!("execution plan json: {error}")))?;
        let snapshot_json = definition_json(definition)?;
        let content_hash = sha256_hex(snapshot_json.as_bytes());
        let resolved_participant_bindings_json = match resolved_participant_bindings {
            Some(bindings) if !bindings.is_empty() => {
                Some(serde_json::to_string(bindings).map_err(|error| {
                    ServiceError::InternalError(format!(
                        "resolved participant bindings json: {error}"
                    ))
                })?)
            }
            _ => None,
        };
        let snapshot_upsert = self.flavor.on_conflict_nothing(&["env", "run_id"]);
        let sql = format!(
            "INSERT INTO bcs_state_machine_definition_snapshots \
             (env, run_id, group_id, session_id, group_version, \
              definition_id, definition_version, definition_content_hash, \
              snapshot_blob_id, snapshot_json, resolved_participant_bindings_json, source_format, \
              execution_plan_json, execution_plan_content_hash, execution_plan_compiler_version) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'json', ?, ?, ?) \
             {snapshot_upsert}"
        );
        self.db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run.run_id.as_str()),
                    DbValue::from(run.group_id.as_str()),
                    DbValue::from(run.session_id.as_str()),
                    DbValue::from(group_version),
                    DbValue::from(definition.id.as_str()),
                    DbValue::from(definition.version),
                    DbValue::from(content_hash.as_str()),
                    DbValue::from(snapshot_json.as_str()),
                    DbValue::from(resolved_participant_bindings_json.as_deref()),
                    DbValue::from(execution_plan_json.as_deref()),
                    DbValue::from(execution_plan.map(|snapshot| snapshot.content_hash.as_str())),
                    DbValue::from(execution_plan.map(|snapshot| snapshot.compiler_version.as_str())),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("definition snapshot upsert: {error}"))
            })?;
        Ok(())
    }

    async fn get_run_snapshot(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<StateMachineRunSnapshot>> {
        let sql = "SELECT snapshot_json, resolved_participant_bindings_json, execution_plan_json, \
                          execution_plan_content_hash, execution_plan_compiler_version \
                   FROM bcs_state_machine_definition_snapshots \
                   WHERE env = ? AND run_id = ? \
                   LIMIT 1";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(run_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("definition snapshot get: {error}"))
            })?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let snapshot_json: Option<String> = db_get_column_opt(&row, "snapshot_json")
            .map_err(|error| ServiceError::InternalError(format!("snapshot_json: {error}")))?;
        let raw_plan: Option<String> = snapshot_column(&row, "execution_plan_json")?;
        let content_hash: Option<String> = snapshot_column(&row, "execution_plan_content_hash")?;
        let compiler_version: Option<String> = snapshot_column(&row, "execution_plan_compiler_version")?;
        let definition: CollaborationDefinition = match snapshot_json {
            Some(raw) if !raw.is_empty() => serde_json::from_str(&raw).map_err(|error| {
                ServiceError::InternalError(format!("definition snapshot parse: {error}"))
            })?,
            _ if raw_plan.is_none() && content_hash.is_none() && compiler_version.is_none() => return Ok(None),
            _ => return Err(ServiceError::InternalError("definition snapshot JSON is missing".into())),
        };
        let execution_plan = match (raw_plan, content_hash, compiler_version) {
            (None, None, None) => None,
            (Some(raw), Some(content_hash), Some(compiler_version)) => {
                let plan = serde_json::from_str(&raw).map_err(|error| {
                    ServiceError::InternalError(format!("execution plan snapshot parse: {error}"))
                })?;
                Some(StateMachineExecutionPlanSnapshot { plan, content_hash, compiler_version })
            }
            _ => return Err(ServiceError::InternalError("incomplete execution plan snapshot".into())),
        };
        validate_snapshot_envelope(&definition, execution_plan.as_ref())?;
        let bindings_json: Option<String> = snapshot_column(&row, "resolved_participant_bindings_json")?;
        let resolved_participant_bindings = bindings_json
            .map(|raw| serde_json::from_str(&raw))
            .transpose()
            .map_err(|error| ServiceError::InternalError(format!("snapshot participant bindings: {error}")))?;
        Ok(Some(StateMachineRunSnapshot { definition, execution_plan, resolved_participant_bindings }))
    }
}
