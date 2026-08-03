#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WorkbenchConnectionAuth {
    UserBound {
        actor_id: Option<String>,
    },
    SessionBound {
        tenant: String,
        actor_id: String,
        group_id: String,
        session_id: String,
    },
}

impl WorkbenchConnectionAuth {
    pub fn actor_id(&self) -> Option<&str> {
        match self {
            Self::UserBound { actor_id } => actor_id.as_deref(),
            Self::SessionBound { actor_id, .. } => Some(actor_id),
        }
    }
}
