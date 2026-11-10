//! BCN management admission policy, independent of HTTP, SDKs and storage.
//! Unknown Card data is preserved: validation never reconstructs the declaration.
pub use crate::a2a_json::parse as parse_card_json;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashSet;

pub const SOURCE_AGENT_EXTENSION: &str =
    "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/source-agent/v1.md";
pub const MAX_CARD_BYTES: usize = 1_048_576;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ManagementCode {
    InvalidRequest,
    Unauthenticated,
    PermissionDenied,
    BotNotFound,
    MethodNotAllowed,
    NotAcceptable,
    BotNameConflict,
    SourceAgentConflict,
    ProviderBotRefConflict,
    SourceAgentChangeNotAllowed,
    TargetChanged,
    CardUrlRequired,
    RefreshSuperseded,
    RequestTooLarge,
    UnsupportedMediaType,
    InvalidOwner,
    InvalidCard,
    CardSourceInvalid,
    CardTooLarge,
    CardAddressRejected,
    CardInterfaceUnsupported,
    CardContentUnsupported,
    CardAuthUnsupported,
    CardExtensionUnsupported,
    RateLimited,
    CardFetchFailed,
    CardFetchTimeout,
    ServiceUnavailable,
    InternalError,
}

#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
#[error("{code:?}: {reason}")]
pub struct ManagementError {
    pub code: ManagementCode,
    pub field: String,
    pub reason: &'static str,
}
impl ManagementError {
    pub fn new(code: ManagementCode, field: impl Into<String>, reason: &'static str) -> Self {
        Self {
            code,
            field: field.into(),
            reason,
        }
    }
}
type Result<T> = std::result::Result<T, ManagementError>;
fn invalid(field: impl Into<String>, reason: &'static str) -> ManagementError {
    ManagementError::new(ManagementCode::InvalidCard, field, reason)
}
fn text<'a>(value: &'a Value, field: &str) -> Result<&'a str> {
    value
        .as_str()
        .filter(|v| !v.trim().is_empty())
        .ok_or_else(|| invalid(field, "NONEMPTY_STRING_REQUIRED"))
}
fn strings<'a>(value: &'a Value, field: &str) -> Result<&'a Vec<Value>> {
    let values = value
        .as_array()
        .ok_or_else(|| invalid(field, "STRING_ARRAY_REQUIRED"))?;
    for (index, item) in values.iter().enumerate() {
        text(item, &format!("{field}[{index}]"))?;
    }
    Ok(values)
}
fn optional_strings(object: &Value, key: &str, prefix: &str) -> Result<()> {
    if let Some(value) = object.get(key) {
        strings(value, &format!("{prefix}.{key}"))?;
    }
    Ok(())
}
fn absolute_url(value: &Value, field: &str, http_only: bool) -> Result<()> {
    let raw = text(value, field)?;
    let url = url::Url::parse(raw).map_err(|_| invalid(field, "ABSOLUTE_URL_REQUIRED"))?;
    if raw != raw.trim()
        || (http_only && (!matches!(url.scheme(), "http" | "https") || url.host_str().is_none()))
    {
        return Err(invalid(field, "ABSOLUTE_URL_REQUIRED"));
    }
    Ok(())
}

/// Validate the anonymous-only public admission contract. Legacy ID APIs may
/// continue to use their existing authentication policy independently.
pub fn validate_management_card(card: &Value) -> Result<()> {
    if !card.is_object() {
        return Err(invalid("agent_card", "OBJECT_REQUIRED"));
    }
    if serde_json::to_vec(card)
        .map_err(|_| invalid("agent_card", "INVALID_JSON"))?
        .len()
        > MAX_CARD_BYTES
    {
        return Err(ManagementError::new(
            ManagementCode::CardTooLarge,
            "agent_card",
            "SIZE_LIMIT_EXCEEDED",
        ));
    }
    crate::a2a_names::card_bot_name(card)
        .map_err(|_| invalid("agent_card.name", "INVALID_NAME"))?;
    text(&card["description"], "agent_card.description")?;
    text(&card["version"], "agent_card.version")?;
    for key in ["defaultInputModes", "defaultOutputModes"] {
        let values = strings(&card[key], &format!("agent_card.{key}"))?;
        if !values.iter().any(|v| v.as_str() == Some("text/plain")) {
            return Err(ManagementError::new(
                ManagementCode::CardContentUnsupported,
                format!("agent_card.{key}"),
                "TEXT_PLAIN_REQUIRED",
            ));
        }
    }
    let interfaces = card["supportedInterfaces"]
        .as_array()
        .filter(|v| !v.is_empty())
        .ok_or_else(|| invalid("agent_card.supportedInterfaces", "NONEMPTY_ARRAY_REQUIRED"))?;
    for (index, interface) in interfaces.iter().enumerate() {
        let prefix = format!("agent_card.supportedInterfaces[{index}]");
        // Standard bindings require URLs. Deployment-defined bindings validate
        // their address fields at the outbound admission boundary.
        if matches!(interface["protocolBinding"].as_str(), Some("HTTP+JSON" | "JSONRPC" | "GRPC")) {
            absolute_url(&interface["url"], &format!("{prefix}.url"), false)?;
        }
        text(
            &interface["protocolBinding"],
            &format!("{prefix}.protocolBinding"),
        )?;
        text(
            &interface["protocolVersion"],
            &format!("{prefix}.protocolVersion"),
        )?;
        if let Some(tenant) = interface.get("tenant") {
            text(tenant, &format!("{prefix}.tenant"))?;
        }
    }
    let capabilities = card["capabilities"]
        .as_object()
        .ok_or_else(|| invalid("agent_card.capabilities", "OBJECT_REQUIRED"))?;
    for key in ["streaming", "pushNotifications", "extendedAgentCard"] {
        if capabilities.get(key).is_some_and(|v| !v.is_boolean()) {
            return Err(invalid(
                format!("agent_card.capabilities.{key}"),
                "BOOLEAN_REQUIRED",
            ));
        }
    }
    if let Some(extensions) = capabilities.get("extensions") {
        let extensions = extensions
            .as_array()
            .ok_or_else(|| invalid("agent_card.capabilities.extensions", "ARRAY_REQUIRED"))?;
        let mut uris = HashSet::new();
        for (index, extension) in extensions.iter().enumerate() {
            let prefix = format!("agent_card.capabilities.extensions[{index}]");
            absolute_url(&extension["uri"], &format!("{prefix}.uri"), false)?;
            if !uris.insert(extension["uri"].as_str()) {
                return Err(invalid(format!("{prefix}.uri"), "DUPLICATE_EXTENSION"));
            }
            if extension.get("description").is_some_and(|v| !v.is_string()) {
                return Err(invalid(format!("{prefix}.description"), "STRING_REQUIRED"));
            }
            if extension.get("required").is_some_and(|v| !v.is_boolean()) {
                return Err(invalid(format!("{prefix}.required"), "BOOLEAN_REQUIRED"));
            }
            if extension.get("params").is_some_and(|v| !v.is_object()) {
                return Err(invalid(format!("{prefix}.params"), "OBJECT_REQUIRED"));
            }
        }
    }
    source_agent(card)?;
    validate_anonymous_security(
        card,
        card.get("securityRequirements"),
        "agent_card.securityRequirements",
    )?;
    let skills = card["skills"]
        .as_array()
        .ok_or_else(|| invalid("agent_card.skills", "ARRAY_REQUIRED"))?;
    let mut ids = HashSet::new();
    for (index, skill) in skills.iter().enumerate() {
        let prefix = format!("agent_card.skills[{index}]");
        let id = text(&skill["id"], &format!("{prefix}.id"))?;
        if !ids.insert(id) {
            return Err(invalid(format!("{prefix}.id"), "DUPLICATE_SKILL_ID"));
        }
        text(&skill["name"], &format!("{prefix}.name"))?;
        text(&skill["description"], &format!("{prefix}.description"))?;
        strings(&skill["tags"], &format!("{prefix}.tags"))?;
        for key in ["examples", "inputModes", "outputModes"] {
            optional_strings(skill, key, &prefix)?;
        }
        validate_anonymous_security(
            card,
            skill.get("securityRequirements"),
            &format!("{prefix}.securityRequirements"),
        )?;
    }
    if let Some(provider) = card.get("provider") {
        if !provider.is_object() {
            return Err(invalid("agent_card.provider", "OBJECT_REQUIRED"));
        }
        text(&provider["organization"], "agent_card.provider.organization")?;
        absolute_url(&provider["url"], "agent_card.provider.url", true)?;
    }
    if let Some(signatures) = card.get("signatures") {
        let signatures = signatures
            .as_array()
            .ok_or_else(|| invalid("agent_card.signatures", "ARRAY_REQUIRED"))?;
        if signatures.iter().any(|v| !v.is_object()) {
            return Err(invalid("agent_card.signatures", "OBJECT_REQUIRED"));
        }
    }
    for key in ["documentationUrl", "iconUrl"] {
        if let Some(value) = card.get(key) {
            absolute_url(value, &format!("agent_card.{key}"), true)?;
        }
    }
    Ok(())
}

fn validate_anonymous_security(
    card: &Value,
    requirements: Option<&Value>,
    field: &str,
) -> Result<()> {
    if card.get("securitySchemes").is_some_and(|v| !v.is_object()) {
        return Err(invalid("agent_card.securitySchemes", "OBJECT_REQUIRED"));
    }
    let Some(requirements) = requirements else {
        return Ok(());
    };
    let alternatives = requirements
        .as_array()
        .ok_or_else(|| invalid(field, "ARRAY_REQUIRED"))?;
    let mut anonymous = alternatives.is_empty();
    for requirement in alternatives {
        let requirement = requirement
            .as_object()
            .ok_or_else(|| invalid(field, "OBJECT_REQUIRED"))?;
        let schemes = match requirement.get("schemes") {
            None if requirement.is_empty() => {
                anonymous = true;
                continue;
            }
            None => return Err(invalid(field, "SCHEMES_OBJECT_REQUIRED")),
            Some(value) => value
                .as_object()
                .ok_or_else(|| invalid(field, "OBJECT_REQUIRED"))?,
        };
        if schemes.is_empty() {
            anonymous = true;
        }
        for (scheme, scopes) in schemes {
            if card
                .get("securitySchemes")
                .and_then(|v| v.get(scheme))
                .is_none()
            {
                return Err(invalid(field, "UNDECLARED_SECURITY_SCHEME"));
            }
            if !scopes.is_object() {
                return Err(invalid(field, "OBJECT_REQUIRED"));
            }
            if let Some(values) = scopes.get("list") {
                strings(values, field)?;
            }
        }
    }
    if !anonymous {
        return Err(ManagementError::new(
            ManagementCode::CardAuthUnsupported,
            field,
            "ANONYMOUS_PATH_REQUIRED",
        ));
    }
    Ok(())
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SourceAgent<'a> {
    pub agent_id: &'a str,
    pub party: Option<&'a str>,
}

pub fn source_agent(card: &Value) -> Result<Option<SourceAgent<'_>>> {
    let Some(extensions) = card
        .pointer("/capabilities/extensions")
        .and_then(Value::as_array)
    else {
        return Ok(None);
    };
    let mut found = None;
    for extension in extensions
        .iter()
        .filter(|v| v.get("uri").and_then(Value::as_str) == Some(SOURCE_AGENT_EXTENSION))
    {
        let field = "agent_card.capabilities.extensions";
        if found.is_some() {
            return Err(invalid(field, "DUPLICATE_SOURCE_EXTENSION"));
        }
        if extension.get("required").and_then(Value::as_bool) != Some(false) {
            return Err(invalid(field, "SOURCE_EXTENSION_MUST_BE_OPTIONAL"));
        }
        let params = extension
            .get("params")
            .filter(|v| v.is_object())
            .ok_or_else(|| invalid(field, "OBJECT_REQUIRED"))?;
        let id = text(&params["agentId"], field)?;
        if id != id.trim() || id.chars().count() > 256 || id.chars().any(char::is_control) {
            return Err(invalid(field, "INVALID_SOURCE_AGENT_ID"));
        }
        let party = match params.get("party") {
            None => None,
            Some(value) => Some(
                value
                    .as_str()
                    .filter(|v| matches!(*v, "first_party" | "second_party" | "third_party"))
                    .ok_or_else(|| invalid(field, "INVALID_PARTY"))?,
            ),
        };
        found = Some(SourceAgent {
            agent_id: id,
            party,
        });
    }
    Ok(found)
}

/// Prefix reserved for references BCS generates when a Provider supplies no
/// call identifier. Provider-supplied references never use it.
pub const GENERATED_PROVIDER_BOT_REF_PREFIX: &str = "bcs-generated:";
/// Shared Provider identity normalization caps every reference at 128 bytes.
pub const MAX_PROVIDER_BOT_REF_BYTES: usize = 128;

pub fn is_generated_provider_bot_ref(reference: &str) -> bool {
    reference.starts_with(GENERATED_PROVIDER_BOT_REF_PREFIX)
}

/// `provider_bot_ref` is the identifier callers pass to the Provider to reach
/// this Agent (a hosted Agent's own ID, not necessarily the platform Agent ID in
/// the Card source extension). It only needs basic hygiene; storage enforces
/// Provider-scoped uniqueness.
pub fn valid_provider_bot_ref(reference: &str) -> bool {
    !reference.is_empty()
        && reference == reference.trim()
        && reference.len() <= MAX_PROVIDER_BOT_REF_BYTES
        && !reference.chars().any(char::is_control)
        && !is_generated_provider_bot_ref(reference)
}

pub fn validate_provider_bot_ref(reference: &str) -> Result<()> {
    if !valid_provider_bot_ref(reference) {
        return Err(ManagementError::new(
            ManagementCode::InvalidRequest,
            "provider_bot_ref",
            "INVALID_PROVIDER_BOT_REF",
        ));
    }
    Ok(())
}

pub fn validate_source_transition(previous: &Value, next: &Value) -> Result<()> {
    let old = source_agent(previous)?;
    let new = source_agent(next)?;
    if old.is_some_and(|old| new.as_ref().is_none_or(|new| old.agent_id != new.agent_id)) {
        return Err(ManagementError::new(
            ManagementCode::SourceAgentChangeNotAllowed,
            "agent_card.capabilities.extensions",
            "SOURCE_AGENT_IMMUTABLE",
        ));
    }
    Ok(())
}

pub fn validate_summary(summary: Option<&str>, card: &Value) -> Result<()> {
    if summary
        .or_else(|| card.get("description").and_then(Value::as_str))
        .is_some_and(|v| v.chars().count() > 4096)
    {
        return Err(ManagementError::new(
            ManagementCode::InvalidRequest,
            "summary",
            "LENGTH_LIMIT_EXCEEDED",
        ));
    }
    Ok(())
}

/// Card retrieval sources are public HTTP(S) resources, never credential URLs.
pub fn validate_card_url(raw: &str) -> Result<()> {
    let fail = || {
        ManagementError::new(
            ManagementCode::CardSourceInvalid,
            "agent_card_url",
            "INVALID_URL",
        )
    };
    let url = url::Url::parse(raw).map_err(|_| fail())?;
    if raw != raw.trim()
        || raw.chars().count() > 2048
        || !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err(fail());
    }
    Ok(())
}
