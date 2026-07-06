//! System-message domain types.

use serde::{Deserialize, Serialize};

use crate::{ActorKind, DeliveryType, Participant, ParticipantMode};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SystemMessageEvent {
    BotJoined {
        group_id: String,
        actor: Participant,
    },
    BotLeft {
        group_id: String,
        actor: Participant,
    },
    ParticipantModeChanged {
        group_id: String,
        actor_id: String,
        actor_name: String,
        actor_kind: ActorKind,
        from: Option<ParticipantMode>,
        to: ParticipantMode,
    },
    SessionContext {
        group_id: String,
        session_id: String,
        reason: String,
        session_input: Option<serde_json::Value>,
        #[serde(default)]
        task_ledger: Option<crate::LedgerSummary>,
    },
    HumanJoined {
        group_id: String,
        actor: Participant,
    },
    GenericNotification {
        group_id: String,
        message: String,
        /// When non-empty, only these participants receive the notification;
        /// when empty, all bot participants in the group receive it (original behavior).
        receivers: Vec<Participant>,
    },
    BotHiddenNotice {
        group_id: String,
        mentioner_bot_id: String,
        hidden_bot_name: String,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SystemMessageEventKind {
    BotJoined,
    HumanJoined,
    BotLeft,
    ParticipantModeChanged,
    SessionContext,
    GenericNotification,
    BotHiddenNotice,
}

impl SystemMessageEvent {
    pub fn kind(&self) -> SystemMessageEventKind {
        match self {
            Self::BotJoined { .. } => SystemMessageEventKind::BotJoined,
            Self::HumanJoined { .. } => SystemMessageEventKind::HumanJoined,
            Self::BotLeft { .. } => SystemMessageEventKind::BotLeft,
            Self::ParticipantModeChanged { .. } => SystemMessageEventKind::ParticipantModeChanged,
            Self::SessionContext { .. } => SystemMessageEventKind::SessionContext,
            Self::GenericNotification { .. } => SystemMessageEventKind::GenericNotification,
            Self::BotHiddenNotice { .. } => SystemMessageEventKind::BotHiddenNotice,
        }
    }
}

pub struct SystemGroupMessage {
    pub recipients: Vec<String>,
    pub message: String,
    pub delivery_type: DeliveryType,
}
