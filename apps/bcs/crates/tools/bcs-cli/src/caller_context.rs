//! Process-local Bot selector. This does not authenticate a caller.
use anyhow::{bail, Result};

#[derive(Clone, Debug, Default)]
pub struct CallerContext {
    actor: Option<String>,
    parent_auth: bool,
}

impl CallerContext {
    pub fn new(mode: Option<&str>, actor: Option<&str>) -> Result<Self> {
        if actor.is_some_and(|id| id.is_empty() || id.len() > 256
            || id.bytes().any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())) {
            bail!("Invalid BCN_ACTOR_BOT_ID");
        }
        let parent_auth = mode == Some("agentpass-subbots");
        if parent_auth && actor.is_none() { bail!("agentpass-subbots requires BCN_ACTOR_BOT_ID"); }
        Ok(Self { actor: actor.map(str::to_owned), parent_auth })
    }

    pub fn from_env() -> Result<Self> {
        Self::new(std::env::var("BCN_AUTH_MODE").ok().as_deref(),
            std::env::var("BCN_ACTOR_BOT_ID").ok().as_deref())
    }

    pub fn from_env_with_token(explicit: Option<&str>) -> Result<Self> {
        if explicit.is_some_and(|token| !token.is_empty()) {
            Self::new(None, std::env::var("BCN_ACTOR_BOT_ID").ok().as_deref())
        } else { Self::from_env() }
    }

    pub fn actor_id(&self) -> Option<&str> { self.actor.as_deref() }

    /// Explicit Static Tokens remain supported. Parent contexts must not
    /// accidentally adopt a different Bot's shared session file or environment.
    pub fn select_token<'a>(&self, explicit: Option<&'a str>, ambient: Option<&'a str>,
        session: Option<&'a str>) -> Option<&'a str> {
        explicit.filter(|value| !value.is_empty()).or_else(|| {
            if self.parent_auth { None }
            else { ambient.filter(|value| !value.is_empty()).or(session.filter(|value| !value.is_empty())) }
        })
    }
}
