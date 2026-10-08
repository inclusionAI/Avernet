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

/// Gateway-authenticated Human identity consumed by BCN.
///
/// BCN derives its legacy `human_<subject.id>` Actor ID at the application
/// boundary; that storage convention is not part of this identity payload.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HumanPrincipal {
    pub subject: AuthenticatedUser,
    /// Optional Gateway tenant metadata; current Human authorization is based
    /// on the authenticated identity and ownership relationships.
    pub tenant: Option<String>,
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
        subject: AuthenticatedUser,
        tenant: Option<String>,
        scopes: BTreeSet<String>,
    ) -> Self {
        Self::Human(HumanPrincipal {
            subject,
            tenant,
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

    pub fn actor_id(&self) -> String {
        match self {
            Self::Human(principal) => format!("human_{}", principal.subject.id),
            Self::Bot(principal) => principal.bot_uuid.clone(),
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

    pub fn tenant(&self) -> Option<&str> {
        match self {
            Self::Human(principal) => principal.tenant.as_deref(),
            Self::Bot(principal) => Some(&principal.tenant),
        }
    }

    pub fn scopes(&self) -> &BTreeSet<String> {
        match self {
            Self::Human(principal) => &principal.scopes,
            Self::Bot(principal) => &principal.scopes,
        }
    }
}
