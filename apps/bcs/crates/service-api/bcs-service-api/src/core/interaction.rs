use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct InteractionKey {
    pub bcs_run_id: String,
    pub interaction_id: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InteractionKind {
    Exec,
    AskUser,
    ModeSwitch,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InteractionStatus {
    Pending,
    Accepted,
    Resolved,
    Invalidated,
}

impl InteractionStatus {
    pub fn accept(self) -> Result<Self, InteractionTransitionError> {
        match self {
            Self::Pending => Ok(Self::Accepted),
            Self::Accepted => Ok(Self::Accepted),
            Self::Resolved | Self::Invalidated => Err(InteractionTransitionError::AlreadyTerminal),
        }
    }

    pub fn resolve(self) -> Result<Self, InteractionTransitionError> {
        match self {
            Self::Pending | Self::Accepted => Ok(Self::Resolved),
            Self::Resolved => Ok(Self::Resolved),
            Self::Invalidated => Err(InteractionTransitionError::AlreadyTerminal),
        }
    }

    pub fn invalidate(self) -> Result<Self, InteractionTransitionError> {
        match self {
            Self::Pending | Self::Accepted => Ok(Self::Invalidated),
            Self::Resolved | Self::Invalidated => Err(InteractionTransitionError::AlreadyTerminal),
        }
    }

    pub fn is_active(self) -> bool {
        matches!(self, Self::Pending | Self::Accepted)
    }

    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Resolved | Self::Invalidated)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Error)]
pub enum InteractionTransitionError {
    #[error("interaction is already terminal")]
    AlreadyTerminal,
}

#[cfg(test)]
mod tests {
    use super::{InteractionStatus, InteractionTransitionError};

    #[test]
    fn supports_acknowledged_and_ack_lost_completion_paths() {
        assert_eq!(
            InteractionStatus::Pending.accept().unwrap(),
            InteractionStatus::Accepted
        );
        assert_eq!(
            InteractionStatus::Accepted.resolve().unwrap(),
            InteractionStatus::Resolved
        );
        assert_eq!(
            InteractionStatus::Pending.resolve().unwrap(),
            InteractionStatus::Resolved
        );
    }

    #[test]
    fn invalidates_only_active_interactions() {
        assert_eq!(
            InteractionStatus::Pending.invalidate().unwrap(),
            InteractionStatus::Invalidated
        );
        assert_eq!(
            InteractionStatus::Accepted.invalidate().unwrap(),
            InteractionStatus::Invalidated
        );
        assert_eq!(
            InteractionStatus::Resolved.invalidate(),
            Err(InteractionTransitionError::AlreadyTerminal)
        );
    }

    #[test]
    fn terminal_interactions_cannot_reopen() {
        assert_eq!(
            InteractionStatus::Resolved.accept(),
            Err(InteractionTransitionError::AlreadyTerminal)
        );
        assert_eq!(
            InteractionStatus::Invalidated.resolve(),
            Err(InteractionTransitionError::AlreadyTerminal)
        );
    }
}
