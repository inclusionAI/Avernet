//! Canonical Provider Bot identity composition, shared by registration entries.
use crate::ProviderAuthMode;

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct ProviderBotIdentity {
    pub agent_code: Option<String>,
    pub provider_bot_ref: String,
    pub bot_slug: Option<String>,
}

pub fn normalize(
    mode: ProviderAuthMode,
    parent_ref: &str,
    bot_slug: Option<&str>,
) -> Result<ProviderBotIdentity, &'static str> {
    if parent_ref.is_empty() || parent_ref != parent_ref.trim() || parent_ref.len() > 128 {
        return Err("provider_bot_ref must be 1-128 bytes without surrounding whitespace");
    }
    if mode != ProviderAuthMode::AgentPassSubbots {
        return Ok(ProviderBotIdentity {
            agent_code: (mode == ProviderAuthMode::AgentPass).then(|| parent_ref.to_string()),
            provider_bot_ref: parent_ref.to_string(),
            bot_slug: None,
        });
    }
    let slug = bot_slug.ok_or("bot_slug is required for agentpass-subbots")?;
    let bytes = slug.as_bytes();
    if bytes.is_empty() || bytes.len() > 32
        || !bytes[0].is_ascii_lowercase() && !bytes[0].is_ascii_digit()
        || !bytes.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-' || *c == b'_')
    {
        return Err("bot_slug must match [a-z0-9][a-z0-9_-]{0,31}");
    }
    let provider_bot_ref = format!("{parent_ref}:{slug}");
    if provider_bot_ref.len() > 128 {
        return Err("composed provider_bot_ref exceeds 128 bytes");
    }
    Ok(ProviderBotIdentity {
        agent_code: Some(parent_ref.to_string()),
        provider_bot_ref,
        bot_slug: Some(slug.to_string()),
    })
}
