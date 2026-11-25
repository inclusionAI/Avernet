use super::*;
#[async_trait]
impl StateMachineDefinitionRepoPort for MemoryCollaborationStore {
    async fn upsert(&self, definition: CollaborationDefinition) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        let key = (definition.id.clone(), definition.version);
        let incoming_json = definition_json(&definition)?;
        let incoming_hash = sha256_hex(incoming_json.as_bytes());
        if let Some(existing) = inner.definitions.get(&key) {
            let existing_json = definition_json(existing)?;
            let existing_hash = sha256_hex(existing_json.as_bytes());
            if existing_hash != incoming_hash {
                return Err(definition_conflict_error(
                    &definition.id,
                    definition.version,
                    &existing_hash,
                    &incoming_hash,
                ));
            }
            inner
                .definition_sources
                .entry(key)
                .or_insert(DefinitionSourceRecord {
                    source_format: "json".to_string(),
                    yaml_text: None,
                    content_hash: incoming_hash,
                });
            return Ok(());
        }
        inner.definitions.insert(key.clone(), definition);
        inner.definition_sources.insert(
            key,
            DefinitionSourceRecord {
                source_format: "json".to_string(),
                yaml_text: None,
                content_hash: incoming_hash,
            },
        );
        Ok(())
    }

    async fn upsert_with_source_yaml(
        &self,
        definition: CollaborationDefinition,
        source_yaml: String,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        let key = (definition.id.clone(), definition.version);
        let incoming_json = definition_json(&definition)?;
        let incoming_hash = sha256_hex(incoming_json.as_bytes());
        if let Some(existing) = inner.definitions.get(&key) {
            let existing_json = definition_json(existing)?;
            let existing_hash = sha256_hex(existing_json.as_bytes());
            if existing_hash != incoming_hash {
                return Err(definition_conflict_error(
                    &definition.id,
                    definition.version,
                    &existing_hash,
                    &incoming_hash,
                ));
            }
            if let Some(existing_source) = inner.definition_sources.get(&key) {
                if let Some(existing_yaml) = existing_source.yaml_text.as_deref() {
                    if existing_yaml != source_yaml {
                        return Err(ServiceError::Conflict(format!(
                            "CollaborationDefinition '{}@{}' already exists with different source YAML",
                            definition.id, definition.version
                        )));
                    }
                }
            }
            inner.definition_sources.insert(
                key,
                DefinitionSourceRecord {
                    source_format: "yaml".to_string(),
                    yaml_text: Some(source_yaml),
                    content_hash: incoming_hash,
                },
            );
            return Ok(());
        }
        inner.definitions.insert(key.clone(), definition);
        inner.definition_sources.insert(
            key,
            DefinitionSourceRecord {
                source_format: "yaml".to_string(),
                yaml_text: Some(source_yaml),
                content_hash: incoming_hash,
            },
        );
        Ok(())
    }

    async fn get(&self, id: &str, version: i32) -> ServiceResult<Option<CollaborationDefinition>> {
        let inner = self.inner.read().await;
        Ok(inner.definitions.get(&(id.to_string(), version)).cloned())
    }

    async fn get_record(
        &self,
        id: &str,
        version: i32,
    ) -> ServiceResult<Option<CollaborationDefinitionRecord>> {
        let inner = self.inner.read().await;
        let key = (id.to_string(), version);
        let Some(definition) = inner.definitions.get(&key).cloned() else {
            return Ok(None);
        };
        let source = inner.definition_sources.get(&key);
        let computed_hash = if source.is_none() {
            let normalized_json = definition_json(&definition)?;
            Some(sha256_hex(normalized_json.as_bytes()))
        } else {
            None
        };
        Ok(Some(CollaborationDefinitionRecord {
            definition,
            source_format: source.map(|source| source.source_format.clone()),
            yaml_text: source.and_then(|source| source.yaml_text.clone()),
            content_hash: source
                .map(|source| source.content_hash.clone())
                .or(computed_hash),
        }))
    }

    async fn save_run_snapshot(
        &self,
        run: &StateMachineRun,
        _group_version: i32,
        definition: &CollaborationDefinition,
        resolved_participant_bindings: Option<&BTreeMap<String, ResolvedParticipantBinding>>,
        execution_plan: Option<&StateMachineExecutionPlanSnapshot>,
    ) -> ServiceResult<()> {
        validate_snapshot_envelope(definition, execution_plan)?;
        let mut inner = self.inner.write().await;
        if inner.run_snapshots.contains_key(&run.run_id) {
            return Ok(());
        }
        inner.run_snapshots.insert(run.run_id.clone(), StateMachineRunSnapshot {
            definition: definition.clone(),
            execution_plan: execution_plan.cloned(),
            resolved_participant_bindings: resolved_participant_bindings.cloned(),
        });
        if let Some(resolved) = resolved_participant_bindings {
            inner
                .run_resolved_participant_bindings
                .entry(run.run_id.clone())
                .or_insert_with(|| resolved.clone());
        }
        Ok(())
    }

    async fn get_run_snapshot(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<StateMachineRunSnapshot>> {
        let inner = self.inner.read().await;
        Ok(inner.run_snapshots.get(run_id).cloned())
    }
}
