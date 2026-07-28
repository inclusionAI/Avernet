use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

/// Neutral user identity authenticated by Gateway.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuthenticatedUser {
    pub id: String,
    pub username: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub full_name: Option<String>,
}

/// Human Actor projection consumed by BCN.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HumanPrincipal {
    pub actor_id: String,
    pub subject: AuthenticatedUser,
    pub tenant: String,
    #[serde(default)]
    pub scopes: BTreeSet<String>,
}

/// Bot Actor projection consumed by BCN.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotPrincipal {
    pub bot_uuid: String,
    pub tenant: String,
    #[serde(default)]
    pub scopes: BTreeSet<String>,
}

/// Closed first-phase Principal union.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Principal {
    Human(HumanPrincipal),
    Bot(BotPrincipal),
}

impl Principal {
    pub fn human(
        actor_id: impl Into<String>,
        subject: AuthenticatedUser,
        tenant: impl Into<String>,
        scopes: BTreeSet<String>,
    ) -> Self {
        Self::Human(HumanPrincipal {
            actor_id: actor_id.into(),
            subject,
            tenant: tenant.into(),
            scopes,
        })
    }

    pub fn bot(
        bot_uuid: impl Into<String>,
        tenant: impl Into<String>,
        scopes: BTreeSet<String>,
    ) -> Self {
        Self::Bot(BotPrincipal {
            bot_uuid: bot_uuid.into(),
            tenant: tenant.into(),
            scopes,
        })
    }

    pub fn actor_id(&self) -> &str {
        match self {
            Self::Human(principal) => &principal.actor_id,
            Self::Bot(principal) => &principal.bot_uuid,
        }
    }

    pub fn bot_uuid(&self) -> Option<&str> {
        match self {
            Self::Human(_) => None,
            Self::Bot(principal) => Some(&principal.bot_uuid),
        }
    }

    pub fn authenticated_user(&self) -> Option<&AuthenticatedUser> {
        match self {
            Self::Human(principal) => Some(&principal.subject),
            Self::Bot(_) => None,
        }
    }

    pub fn tenant(&self) -> &str {
        match self {
            Self::Human(principal) => &principal.tenant,
            Self::Bot(principal) => &principal.tenant,
        }
    }

    pub fn scopes(&self) -> &BTreeSet<String> {
        match self {
            Self::Human(principal) => &principal.scopes,
            Self::Bot(principal) => &principal.scopes,
        }
    }
}
