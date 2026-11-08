//! Canonical Provider Bot identity parsing, shared by registration entries.
use crate::ProviderAuthMode;

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct ProviderBotIdentity {
    pub agent_code: Option<String>,
    pub provider_bot_ref: String,
    pub bot_slug: Option<String>,
}

pub fn normalize(
    mode: ProviderAuthMode,
    provider_bot_ref: &str,
) -> Result<ProviderBotIdentity, &'static str> {
    if provider_bot_ref.is_empty() || provider_bot_ref != provider_bot_ref.trim() || provider_bot_ref.len() > 128 {
        return Err("provider_bot_ref must be 1-128 bytes without surrounding whitespace");
    }
    if mode != ProviderAuthMode::AgentPassSubbots {
        return Ok(ProviderBotIdentity {
            agent_code: (mode == ProviderAuthMode::AgentPass).then(|| provider_bot_ref.to_string()),
            provider_bot_ref: provider_bot_ref.to_string(),
            bot_slug: None,
        });
    }
    // Parent codes can contain colons; only the final segment is the Bot slug.
    let (parent, slug) = provider_bot_ref.rsplit_once(':')
        .ok_or("agentpass-subbots provider_bot_ref must be <parent>:<slug>")?;
    if parent.is_empty() || !parent.bytes().all(|c| c.is_ascii_alphanumeric() || b"_.:-".contains(&c)) {
        return Err("provider_bot_ref parent must match [A-Za-z0-9_.:-]+");
    }
    let bytes = slug.as_bytes();
    if bytes.is_empty() || bytes.len() > 32
        || !bytes[0].is_ascii_lowercase() && !bytes[0].is_ascii_digit()
        || !bytes.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-' || *c == b'_')
    {
        return Err("provider_bot_ref slug must match [a-z0-9][a-z0-9_-]{0,31}");
    }
    Ok(ProviderBotIdentity {
        agent_code: Some(parent.to_string()),
        provider_bot_ref: provider_bot_ref.to_string(),
        bot_slug: Some(slug.to_string()),
    })
}
