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
        /// Delivery override for the driver bot's `[GROUP CONTEXT]` message.
        /// `None` keeps the default `DeliveryType::Send`; `Some(Inject)`
        /// delivers the context silently. Only applies to the driver;
        /// other participants always receive `DeliveryType::Inject`.
        #[serde(default)]
        driver_delivery: Option<DeliveryType>,
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

/// Persistence policy for a system group message.
///
/// Producers decide how each bot-facing message is recorded in the message
/// store; the dispatcher executes the policy mechanically (it never inspects
/// message content to decide ownership).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PersistMode {
    /// Persist one record per recipient with `owner_bot_id = recipient`
    /// (personalized per-bot context such as `[GROUP CONTEXT]`, only
    /// readable by that bot's history view).
    PerRecipient,
    /// Persist exactly one record with `owner_bot_id = None` so the notice
    /// joins the public history that human viewers read (their history
    /// filter is `owner_bot_id IS NULL`). Bot viewers see it via
    /// `PublicOrOwner`; use this for notices whose text is identical for
    /// all recipients to avoid storing N identical copies.
    Public,
    /// Do not persist this message.
    Skip,
}

pub struct SystemGroupMessage {
    pub recipients: Vec<String>,
    pub message: String,
    pub delivery_type: DeliveryType,
    pub persist: PersistMode,
}
