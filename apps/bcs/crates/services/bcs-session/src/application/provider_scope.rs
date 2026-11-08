//! Provider policy is checked before Session writes or Human input consumption.
use super::*;

impl SessionManagementServiceImpl {
    pub(super) async fn check_provider_scope(
        &self,
        group_id: &str,
        actors: &[String],
        session_members: &[Participant],
    ) -> Result<(), SessionUseCaseError> {
        let Some(registry) = &self.collaboration_registry else {
            return Ok(());
        };
        let group = self
            .group_repo
            .try_get(group_id)
            .await?
            .ok_or_else(|| ServiceError::GroupNotFound(group_id.into()))?;
        let mut ids = group
            .participants
            .iter()
            .map(|p| p.bot_uuid.clone())
            .collect::<Vec<_>>();
        ids.extend_from_slice(actors);
        // A historical snapshot cannot retain a group restriction after the last
        // restricted group Bot leaves. A newly proposed restricted actor still
        // activates the boundary before it can be persisted.
        if registry.collaboration_owner(&ids).await?.is_none() {
            return Ok(());
        }
        ids.extend(session_members.iter().map(|p| p.bot_uuid.clone()));
        registry.collaboration_owner(&ids).await?;
        Ok(())
    }
}
