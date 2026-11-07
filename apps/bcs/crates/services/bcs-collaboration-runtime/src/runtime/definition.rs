use super::*;
async fn ensure_candidate_definition_slot_is_compatible(
    definitions: &dyn StateMachineDefinitionRepoPort,
    candidate_ref: &CollaborationDefinitionRef,
    incoming_definition: &CollaborationDefinition,
    incoming_source_yaml: &str,
) -> Result<(), CollaborationRuntimeError> {
    let Some(existing) = definitions
        .get_record(&candidate_ref.id, candidate_ref.version)
        .await?
    else {
        return Ok(());
    };
    let existing_json = normalized_definition_json(&existing.definition)?;
    let incoming_json = normalized_definition_json(incoming_definition)?;
    if existing_json != incoming_json {
        return Err(CollaborationRuntimeError::Conflict(format!(
            "CollaborationDefinition '{}@{}' already exists with different content",
            candidate_ref.id, candidate_ref.version
        )));
    }
    if let Some(existing_yaml) = existing
        .yaml_text
        .as_deref()
        .filter(|existing_yaml| !existing_yaml.is_empty())
    {
        if existing_yaml != incoming_source_yaml {
            return Err(CollaborationRuntimeError::Conflict(format!(
                "CollaborationDefinition '{}@{}' already exists with different source YAML",
                candidate_ref.id, candidate_ref.version
            )));
        }
    }
    Ok(())
}impl CollaborationRuntime {

    pub(super) fn compile_for_execution(
        &self,
        definition: CollaborationDefinition,
    ) -> Result<crate::fixed_loop::CompiledAuthoringDefinition, CollaborationRuntimeError> {
        let v2 = matches!(&definition.runtime,
            CollaborationRuntimeDefinition::StateMachine(machine) if machine.version == 2);
        if !v2 {
            let execution = validate_definition(definition)?;
            return Ok(crate::fixed_loop::CompiledAuthoringDefinition {
                definition: execution.definition.clone(), execution, plan: None,
            });
        }
        if !self.loop_execution_enabled {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "v2 execution is disabled until runtime and recovery support is enabled".into(),
            ));
        }
        let compiled = crate::fixed_loop::compile_authoring_definition_with_instrumentation(
            definition, &self.fixed_loop_limits, self.loop_instrumentation.as_deref(),
        ).map_err(|error| CollaborationRuntimeError::InvalidDefinition(format!(
                "{} {}: {}", error.code, error.path, error.message
            )))?;
        if compiled.plan.as_ref().is_some_and(|plan|
            plan.compiler_version == crate::fixed_loop::NESTED_FIXED_LOOP_COMPILER_VERSION)
            && !self.nested_loop_execution_enabled
        {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "state_machine.loop.nesting execution is not enabled for new Runs".into(),
            ));
        }
        Ok(compiled)
    }

    pub(super) async fn resolve_definition(
        &self,
        cmd: &StartStateMachineRunCommand,
        group_binding: Option<&GroupRuntimeBinding>,
    ) -> Result<ResolvedDefinition, CollaborationRuntimeError> {
        if let Some(yaml) = &cmd.definition_yaml {
            validate_v2_definition_input(serde_yaml::from_str(yaml)
                .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?)?;
            let definition: CollaborationDefinition = serde_yaml::from_str(yaml)
                .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?;
            reject_explicit_participant_roles(&definition)?;
            return Ok(ResolvedDefinition {
                definition,
                source: ResolvedDefinitionSource::Inline,
            });
        }
        if let Some(value) = &cmd.definition {
            validate_v2_definition_input(serde_yaml::to_value(value)
                .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?)?;
            let definition: CollaborationDefinition = serde_json::from_value(value.clone())
                .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?;
            reject_explicit_participant_roles(&definition)?;
            return Ok(ResolvedDefinition {
                definition,
                source: ResolvedDefinitionSource::Inline,
            });
        }
        if let Some(definition_ref) = &cmd.definition_ref {
            let definition = self
                .definitions
                .get(&definition_ref.id, definition_ref.version)
                .await?
                .ok_or_else(|| {
                    CollaborationRuntimeError::DefinitionNotFound(
                        definition_ref.id.clone(),
                        definition_ref.version,
                    )
                })?;
            return Ok(ResolvedDefinition {
                definition,
                source: ResolvedDefinitionSource::Stored,
            });
        }
        if let Some(binding) = group_binding {
            if let Some(definition_ref) = &binding.default_definition {
                let definition = self
                    .definitions
                    .get(&definition_ref.id, definition_ref.version)
                    .await?
                    .ok_or_else(|| {
                        CollaborationRuntimeError::DefinitionNotFound(
                            definition_ref.id.clone(),
                            definition_ref.version,
                        )
                    })?;
                return Ok(ResolvedDefinition {
                    definition,
                    source: ResolvedDefinitionSource::Stored,
                });
            }
        }
        Err(CollaborationRuntimeError::InvalidRequest(
            "group has no default collaboration definition binding; create or bind the group definition before starting a state-machine run".to_string(),
        ))
    }

    pub(super) async fn group_collaboration_definition_view(
        &self,
        group_id: &str,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        self.groups.get(group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {group_id}"))
        })?;
        let binding = self.bindings.get(group_id).await?;
        let participant_bindings = binding
            .as_ref()
            .map(|binding| binding.participant_bindings.clone())
            .unwrap_or_default();
        let default_definition = binding
            .as_ref()
            .and_then(|binding| binding.default_definition.clone());
        let Some(definition_ref) = default_definition.clone() else {
            return Ok(GroupCollaborationDefinitionView {
                group_id: group_id.to_string(),
                default_definition: None,
                definition: None,
                definition_yaml: None,
                yaml_source: DefinitionYamlSource::NoDefinition,
                participant_bindings,
            });
        };
        let record = self
            .definitions
            .get_record(&definition_ref.id, definition_ref.version)
            .await?
            .ok_or_else(|| {
                CollaborationRuntimeError::DefinitionNotFound(
                    definition_ref.id.clone(),
                    definition_ref.version,
                )
            })?;
        let (definition_yaml, yaml_source) = definition_yaml_for_record(&record);
        Ok(GroupCollaborationDefinitionView {
            group_id: group_id.to_string(),
            default_definition,
            definition: Some(record.definition),
            definition_yaml,
            yaml_source,
            participant_bindings,
        })
    }

    pub(super) async fn load_run_compiled(
        &self,
        run: &StateMachineRun,
    ) -> Result<CompiledStateMachine, CollaborationRuntimeError> {
        let loaded = self.load_run_definition_for_view(run).await?;
        if loaded.plan.is_some() && !self.loop_execution_enabled {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "v2 execution is disabled until runtime and recovery support is enabled".into(),
            ));
        }
        Ok(loaded.execution)
    }

    /// Read-only projections do not enable execution or depend on current limits.
    pub(super) async fn load_run_definition_for_view(
        &self,
        run: &StateMachineRun,
    ) -> Result<crate::fixed_loop::CompiledAuthoringDefinition, CollaborationRuntimeError> {
        match self.definitions.get_run_snapshot(&run.run_id).await? {
            Some(snapshot) => crate::snapshot::load_state_machine_snapshot(snapshot),
            None => {
                let definition = self
                    .definitions
                    .get(&run.definition_id, run.definition_version)
                    .await?
                    .ok_or_else(|| {
                        CollaborationRuntimeError::DefinitionNotFound(
                            run.definition_id.clone(),
                            run.definition_version,
                        )
                    })?;
                if matches!(&definition.runtime,
                    CollaborationRuntimeDefinition::StateMachine(machine) if machine.version == 2)
                {
                    return Err(CollaborationRuntimeError::Internal(ServiceError::InternalError(
                        "v2 Run has no immutable snapshot; recompilation is forbidden".into(),
                    )));
                }
                let execution = validate_definition(definition)?;
                Ok(crate::fixed_loop::CompiledAuthoringDefinition {
                    definition: execution.definition.clone(), execution, plan: None,
                })
            }
        }
    }

    pub(super) async fn patch_group_collaboration_definition_impl(
        &self,
        cmd: PatchGroupCollaborationDefinitionCommand,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        let group = self.groups.get(&cmd.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", cmd.group_id))
        })?;
        let binding = self.bindings.get(&cmd.group_id).await?.ok_or_else(|| {
            CollaborationRuntimeError::Conflict(
                "group has no default collaboration definition binding".to_string(),
            )
        })?;
        if binding.default_definition.as_ref() != Some(&cmd.base_definition) {
            return Err(CollaborationRuntimeError::Conflict(
                "base_definition does not match current group default definition".to_string(),
            ));
        }
        let mut definition = parse_authoring_definition_yaml(&cmd.definition_yaml)?;
        definition.id = cmd.base_definition.id.clone();
        definition.version = cmd.base_definition.version.checked_add(1).ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(
                "base_definition.version is too large to create a next version".to_string(),
            )
        })?;
        reject_explicit_participant_roles(&definition)?;
        let compiled = self.compile_for_execution(definition)?;
        let candidate_ref = CollaborationDefinitionRef {
            id: compiled.definition.id.clone(),
            version: compiled.definition.version,
        };
        let final_participant_bindings = cmd
            .participant_bindings
            .unwrap_or_else(|| binding.participant_bindings.clone());
        validate_runtime_participant_bindings(
            &compiled.definition,
            &group,
            &final_participant_bindings,
        )?;
        resolve_participant_bindings(
            &group,
            &compiled.execution,
            Some(&GroupRuntimeBinding {
                group_id: group.id.clone(),
                group_version: group.version,
                default_definition: Some(candidate_ref.clone()),
                participant_bindings: final_participant_bindings.clone(),
                auto_start_on_service_invocation: binding.auto_start_on_service_invocation,
            }),
        )?;
        ensure_candidate_definition_slot_is_compatible(
            self.definitions.as_ref(),
            &candidate_ref,
            &compiled.definition,
            &cmd.definition_yaml,
        )
        .await?;
        self.definitions
            .upsert_with_source_yaml(compiled.definition, cmd.definition_yaml)
            .await?;
        let rebound = self
            .bindings
            .bind_default_definition_if_current(
                &cmd.group_id,
                group.version,
                Some(cmd.base_definition),
                Some(candidate_ref),
                Some(final_participant_bindings),
                binding.auto_start_on_service_invocation,
            )
            .await?;
        if !rebound {
            return Err(CollaborationRuntimeError::Conflict(
                "group default definition changed while applying patch".to_string(),
            ));
        }
        self.group_collaboration_definition_view(&cmd.group_id)
            .await
    }

    pub(super) async fn upgrade_group_collaboration_definition_impl(
        &self,
        cmd: UpgradeGroupCollaborationDefinitionCommand,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        if cmd.target_definition.id != cmd.base_definition.id {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "target_definition.id must match base_definition.id".to_string(),
            ));
        }
        if cmd.target_definition.version <= cmd.base_definition.version {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "target_definition.version must be greater than base_definition.version"
                    .to_string(),
            ));
        }
        let group = self.groups.get(&cmd.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", cmd.group_id))
        })?;
        let binding = self.bindings.get(&cmd.group_id).await?.ok_or_else(|| {
            CollaborationRuntimeError::Conflict(
                "group has no default collaboration definition binding".to_string(),
            )
        })?;
        if binding.default_definition.as_ref() != Some(&cmd.base_definition) {
            return Err(CollaborationRuntimeError::Conflict(
                "base_definition does not match current group default definition".to_string(),
            ));
        }
        let definition = self
            .definitions
            .get(&cmd.target_definition.id, cmd.target_definition.version)
            .await?
            .ok_or_else(|| {
                CollaborationRuntimeError::DefinitionNotFound(
                    cmd.target_definition.id.clone(),
                    cmd.target_definition.version,
                )
            })?;
        let compiled = self.compile_for_execution(definition)?;
        let final_participant_bindings = cmd
            .participant_bindings
            .unwrap_or_else(|| binding.participant_bindings.clone());
        validate_runtime_participant_bindings(
            &compiled.definition,
            &group,
            &final_participant_bindings,
        )?;
        resolve_participant_bindings(
            &group,
            &compiled.execution,
            Some(&GroupRuntimeBinding {
                group_id: group.id.clone(),
                group_version: group.version,
                default_definition: Some(cmd.target_definition.clone()),
                participant_bindings: final_participant_bindings.clone(),
                auto_start_on_service_invocation: binding.auto_start_on_service_invocation,
            }),
        )?;
        let rebound = self
            .bindings
            .bind_default_definition_if_current(
                &cmd.group_id,
                group.version,
                Some(cmd.base_definition),
                Some(cmd.target_definition),
                Some(final_participant_bindings),
                binding.auto_start_on_service_invocation,
            )
            .await?;
        if !rebound {
            return Err(CollaborationRuntimeError::Conflict(
                "group default definition changed while applying upgrade".to_string(),
            ));
        }
        self.group_collaboration_definition_view(&cmd.group_id)
            .await
    }

    pub(super) async fn configure_group_runtime_impl(
        &self,
        cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        let group = self.groups.get(&cmd.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", cmd.group_id))
        })?;

        let participant_bindings = cmd.participant_bindings;
        let mut definition_for_validation = None;
        let default_definition = if let Some(yaml) = cmd.definition_yaml {
            validate_v2_definition_input(serde_yaml::from_str(&yaml)
                .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?)?;
            let definition: CollaborationDefinition = serde_yaml::from_str(&yaml)
                .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?;
            reject_explicit_participant_roles(&definition)?;
            let compiled = self.compile_for_execution(definition)?;
            let definition_ref = CollaborationDefinitionRef {
                id: compiled.definition.id.clone(),
                version: compiled.definition.version,
            };
            definition_for_validation = Some(compiled.definition.clone());
            self.definitions
                .upsert_with_source_yaml(compiled.definition, yaml)
                .await?;
            Some(definition_ref)
        } else if let Some(value) = cmd.definition {
            validate_v2_definition_input(serde_yaml::to_value(&value)
                .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?)?;
            let definition: CollaborationDefinition = serde_json::from_value(value)
                .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?;
            reject_explicit_participant_roles(&definition)?;
            let compiled = self.compile_for_execution(definition)?;
            let definition_ref = CollaborationDefinitionRef {
                id: compiled.definition.id.clone(),
                version: compiled.definition.version,
            };
            definition_for_validation = Some(compiled.definition.clone());
            self.definitions.upsert(compiled.definition).await?;
            Some(definition_ref)
        } else if let Some(definition_ref) = cmd.definition_ref {
            let definition = self
                .definitions
                .get(&definition_ref.id, definition_ref.version)
                .await?
                .ok_or_else(|| {
                    CollaborationRuntimeError::DefinitionNotFound(
                        definition_ref.id.clone(),
                        definition_ref.version,
                    )
                })?;
            let compiled = self.compile_for_execution(definition)?;
            definition_for_validation = Some(compiled.definition);
            Some(definition_ref)
        } else {
            None
        };
        if !participant_bindings.is_empty() {
            let definition = definition_for_validation.as_ref().ok_or_else(|| {
                CollaborationRuntimeError::InvalidParticipantBinding(
                    "participant_bindings require a default collaboration definition".to_string(),
                )
            })?;
            validate_runtime_participant_bindings(definition, &group, &participant_bindings)?;
        }
        let requires_human_input_channel =
            definition_for_validation
                .as_ref()
                .is_some_and(|definition| {
                    matches!(
                        &definition.runtime,
                        CollaborationRuntimeDefinition::StateMachine(state_machine)
                            if state_machine.human_input_channel.is_some()
                    )
                });

        self.bindings
            .bind_default_definition(
                &cmd.group_id,
                group.version,
                default_definition.clone(),
                Some(participant_bindings),
                cmd.auto_start_on_service_invocation,
            )
            .await?;

        Ok(ConfigureGroupRuntimeOutcome {
            group_id: cmd.group_id,
            default_definition,
            auto_start_on_service_invocation: cmd.auto_start_on_service_invocation,
            requires_human_input_channel,
        })
    }
}
