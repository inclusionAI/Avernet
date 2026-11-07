use super::*;
#[async_trait]
impl GroupRuntimeBindingRepoPort for MemoryCollaborationStore {
    async fn upsert(&self, binding: GroupRuntimeBinding) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        inner.bindings.insert(binding.group_id.clone(), binding);
        Ok(())
    }

    async fn get(&self, group_id: &str) -> ServiceResult<Option<GroupRuntimeBinding>> {
        let inner = self.inner.read().await;
        Ok(inner.bindings.get(group_id).cloned())
    }

    async fn delete(&self, group_id: &str) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        Ok(inner.bindings.remove(group_id).is_some())
    }

    async fn bind_default_definition(
        &self,
        group_id: &str,
        group_version: i32,
        definition: Option<CollaborationDefinitionRef>,
        participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
        auto_start_on_service_invocation: bool,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        inner.bindings.insert(
            group_id.to_string(),
            GroupRuntimeBinding {
                group_id: group_id.to_string(),
                group_version,
                default_definition: definition,
                participant_bindings: participant_bindings.unwrap_or_default(),
                auto_start_on_service_invocation,
            },
        );
        Ok(())
    }

    async fn bind_default_definition_if_current(
        &self,
        group_id: &str,
        group_version: i32,
        expected_definition: Option<CollaborationDefinitionRef>,
        definition: Option<CollaborationDefinitionRef>,
        participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
        auto_start_on_service_invocation: bool,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let current_definition = inner
            .bindings
            .get(group_id)
            .and_then(|binding| binding.default_definition.clone());
        if current_definition != expected_definition {
            return Ok(false);
        }
        inner.bindings.insert(
            group_id.to_string(),
            GroupRuntimeBinding {
                group_id: group_id.to_string(),
                group_version,
                default_definition: definition,
                participant_bindings: participant_bindings.unwrap_or_default(),
                auto_start_on_service_invocation,
            },
        );
        Ok(true)
    }
}
