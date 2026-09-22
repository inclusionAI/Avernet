//! Builder helpers for constructing in-memory test/dev Groups.
use super::*;

/// Helper functions for creating groups.
pub struct GroupBuilder {
    id: Option<String>,
    label: Option<String>,
    driver_bot: String,
    originator: Option<String>,
    participants: Vec<Participant>,
}

impl GroupBuilder {
    /// Create a new group builder.
    pub fn new(driver_bot: impl Into<String>) -> Self {
        Self {
            id: None,
            label: None,
            driver_bot: driver_bot.into(),
            originator: None,
            participants: Vec::new(),
        }
    }

    /// Set the group ID.
    pub fn id(mut self, id: impl Into<String>) -> Self {
        self.id = Some(id.into());
        self
    }

    /// Set the group label.
    pub fn label(mut self, label: impl Into<String>) -> Self {
        self.label = Some(label.into());
        self
    }

    /// Set the originator (defaults to driver_bot if not set).
    pub fn originator(mut self, originator: impl Into<String>) -> Self {
        self.originator = Some(originator.into());
        self
    }

    /// Add a participant.
    pub fn participant(mut self, participant: Participant) -> Self {
        self.participants.push(participant);
        self
    }

    /// Build the group.
    pub fn build(self) -> DomainGroup {
        let id = self
            .id
            .unwrap_or_else(|| generated_group_id(GroupKind::Normal));
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        DomainGroup {
            id,
            label: self.label,
            status: GroupStatus::Active,
            driver_bot: self.driver_bot,
            originator: self.originator,
            routing_policy: None,
            human_mention_notify_mode: HumanMentionNotifyMode::default(),
            context: None,
            opening_message: None,
            participants: self.participants,
            messages: Vec::new(),
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            created_at: now,
            updated_at: now,
            group_kind: GroupKind::default(),
            dm_pair_key: None,
            group_strategy: GroupStrategy::Chat,
            service_spec: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
        }
    }
}
