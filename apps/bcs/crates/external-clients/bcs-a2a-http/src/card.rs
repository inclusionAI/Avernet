//! Declaration validation only: registration does not execute a probe task.
use a2a::{AgentCard, AgentInterface, SecurityScheme};
use serde_json::Value;

#[derive(Debug)]
pub struct SelectedCard {
    /// Original JSON is authoritative; SDK round-tripping may lose unknown fields.
    pub raw: Value,
    pub interface: AgentInterface,
}

#[derive(Debug, PartialEq, Eq)]
pub enum CardError {
    InvalidShape,
    StreamingRequired,
    TextRequired,
    RequiredExtensionUnsupported(String),
    AuthenticationUnsupported,
    InterfaceUnsupported,
    AddressRejected,
}

/// The caller supplies its supported extension URIs and existing outbound URL
/// policy. No credential values enter the Card, return value or errors.
pub fn select_card(
    raw: Value,
    extensions: &[&str],
    bearer_configured: bool,
    allow_url: impl Fn(&str) -> bool,
) -> Result<SelectedCard, CardError> {
    select_card_with_bindings(raw, extensions, bearer_configured, &["HTTP+JSON"],
        |interface| allow_url(&interface.url))
}

/// Only composition-enabled bindings participate; selecting gRPC never silently
/// falls back to a direct channel or a different binding after submission.
pub fn select_card_with_bindings(
    raw: Value, extensions: &[&str], bearer_configured: bool, bindings: &[&str],
    allow_interface: impl Fn(&AgentInterface) -> bool,
) -> Result<SelectedCard, CardError> {
    let mut card: AgentCard = serde_json::from_value(raw.clone()).map_err(|_| CardError::InvalidShape)?;
    // The pinned SDK strips http:// from gRPC interface URLs. BCS freezes the
    // declaration's exact endpoint identity, and mesh allowlisting compares that
    // identity, so use the original string after validating the complete shape.
    let originals=raw.get("supportedInterfaces").and_then(Value::as_array).ok_or(CardError::InvalidShape)?;
    if originals.len()!=card.supported_interfaces.len() {return Err(CardError::InvalidShape);}
    for (interface,original) in card.supported_interfaces.iter_mut().zip(originals) {
        interface.url=original.get("url").and_then(Value::as_str).ok_or(CardError::InvalidShape)?.to_owned();
    }
    if card.capabilities.streaming != Some(true) { return Err(CardError::StreamingRequired); }
    if !card.default_input_modes.iter().any(|m| m == "text/plain")
        || !card.default_output_modes.iter().any(|m| m == "text/plain") {
        return Err(CardError::TextRequired);
    }
    for extension in card.capabilities.extensions.iter().flatten() {
        if extension.required == Some(true) && !extensions.contains(&extension.uri.as_str()) {
            return Err(CardError::RequiredExtensionUnsupported(extension.uri.clone()));
        }
    }
    // URI recognition alone is insufficient for this experimental extension:
    // the draft and business profile are part of its compatibility declaration.
    for extension in raw.pointer("/capabilities/extensions").and_then(Value::as_array).into_iter().flatten() {
        if extension.get("uri").and_then(Value::as_str)==Some(crate::structured_exec::EXTENSION)
            && extension.get("required").and_then(Value::as_bool)==Some(true)
            && !crate::structured_exec::compatible_params(extension.get("params").unwrap_or(&Value::Null)) {
            return Err(CardError::RequiredExtensionUnsupported(crate::structured_exec::EXTENSION.into()));
        }
    }
    // Requirements are OR alternatives; entries within one requirement are AND.
    // Phase one supports anonymous declarations or one static Bearer scheme.
    if let Some(requirements) = &card.security_requirements {
        if !requirements.is_empty() && !requirements.iter().any(|requirement| {
            requirement.is_empty() || (bearer_configured && requirement.len() == 1
                && requirement.iter().all(|(name, scopes)| {
                    scopes.is_empty() && matches!(
                        card.security_schemes.as_ref().and_then(|schemes| schemes.get(name)),
                        Some(SecurityScheme::HttpAuth(auth)) if auth.scheme.eq_ignore_ascii_case("bearer")
                    )
                }))
        }) { return Err(CardError::AuthenticationUnsupported); }
    }
    let mut compatible = card.supported_interfaces.into_iter().filter(|i| {
        bindings.contains(&i.protocol_binding.as_str()) && i.protocol_version == "1.0"
    });
    let mut saw_rejected = false;
    for interface in &mut compatible {
        if allow_interface(&interface) { return Ok(SelectedCard { raw, interface }); }
        saw_rejected = true;
    }
    Err(if saw_rejected { CardError::AddressRejected } else { CardError::InterfaceUnsupported })
}
