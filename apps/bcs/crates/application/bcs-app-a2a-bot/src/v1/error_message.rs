use bcs_service_api::types::a2a_bot::a2a_management::{ManagementCode as Code, ManagementError};

pub(super) fn public_message(error: &ManagementError) -> String {
    let summary = match error.code {
        Code::InvalidRequest => "Invalid AgentCard management request",
        Code::Unauthenticated => "Provider-admin authentication is required",
        Code::PermissionDenied => "Provider access denied",
        Code::BotNotFound => "Agent not found or unavailable to this Provider",
        Code::MethodNotAllowed => "HTTP method is not supported for this AgentCard operation",
        Code::NotAcceptable => "Accept must allow application/json",
        Code::ProviderBotRefConflict => {
            "provider_bot_ref is already registered; find the existing Agent before updating it"
        }
        Code::BotNameConflict => "Agent name conflicts with an existing registration",
        Code::AgentIdentityConflict => { "AgentId conflicts with the stored identity" }
        Code::SourceAgentConflict => "The source Agent is already bound to another registration",
        Code::SourceAgentChangeNotAllowed => {
            "The source Agent identity cannot be changed by updating its Card"
        }
        Code::TargetChanged => {
            "The Agent or Provider binding changed concurrently; read the current Agent before retrying"
        }
        Code::CardUrlRequired => {
            "Refresh requires an Agent registered with agent_card_url; inline Cards must be updated instead"
        }
        Code::RefreshSuperseded => {
            "A newer Card update superseded this refresh; read the current Card before retrying"
        }
        Code::RequestTooLarge => "AgentCard management request exceeds the 2 MiB size limit",
        Code::UnsupportedMediaType => "Content-Type must be application/json",
        Code::InvalidOwner => {
            "Invalid Agent owner configuration; specify one valid owner or configure the Provider default owner"
        }
        Code::InvalidCard => "Invalid AgentCard",
        Code::CardSourceInvalid => {
            "Invalid Card source; supply exactly one inline agent_card or an absolute HTTP(S) agent_card_url without credentials or a fragment"
        }
        Code::CardTooLarge => "AgentCard exceeds the 1 MiB size limit",
        Code::CardAddressRejected => {
            "AgentCard source or interface address is not permitted by the server address policy"
        }
        Code::CardInterfaceUnsupported => {
            "AgentCard has no interface supported by this server; check protocolBinding and protocolVersion"
        }
        Code::CardContentUnsupported => "AgentCard input and output modes must support text/plain",
        Code::CardAuthUnsupported => {
            "AgentCard authentication requirements are not supported by the server admission policy"
        }
        Code::CardExtensionUnsupported => {
            "AgentCard declares a required extension that this server does not support"
        }
        Code::RateLimited => "AgentCard management rate limit exceeded; wait before retrying",
        Code::CardFetchFailed => {
            "Unable to retrieve a complete AgentCard; check source availability and its HTTP response"
        }
        Code::CardFetchTimeout => {
            "AgentCard retrieval or validation timed out; check source availability before retrying"
        }
        Code::ServiceUnavailable => {
            "AgentCard management is temporarily unavailable; check Provider configuration or contact the administrator"
        }
        Code::InternalError => {
            "Unable to complete AgentCard management; contact support with request_id"
        }
    };
    let Some(reason) = public_reason(error.reason) else {
        return summary.into();
    };
    match public_field(&error.field) {
        Some(field) => format!("{summary}: {field}: {reason}"),
        None => format!("{summary}: {reason}"),
    }
}

fn public_reason(reason: &str) -> Option<&'static str> {
    Some(match reason {
        "NONEMPTY_STRING_REQUIRED" => "a non-empty string is required",
        "STRING_REQUIRED" => "a string is required",
        "STRING_ARRAY_REQUIRED" => "an array of non-empty strings is required",
        "ABSOLUTE_URL_REQUIRED" => "a valid absolute URL is required",
        "OBJECT_REQUIRED" => "a JSON object is required",
        "ARRAY_REQUIRED" => "an array is required",
        "NONEMPTY_ARRAY_REQUIRED" => "a non-empty array is required",
        "BOOLEAN_REQUIRED" => "a boolean is required",
        "INVALID_JSON" => "the Card must be valid JSON",
        "INVALID_NAME" => {
            "name must contain 2 to 64 characters, without control characters or surrounding whitespace"
        }
        "INVALID_DECLARATION" => {
            "the Card declaration is malformed or cannot be parsed; check its required fields and JSON types"
        }
        "TEXT_PLAIN_REQUIRED" => "text/plain must be included in the supported modes",
        "DUPLICATE_EXTENSION" => "extension URI must be unique",
        "DUPLICATE_SKILL_ID" => "skill id must be unique",
        "SCHEMES_OBJECT_REQUIRED" => "each security requirement must contain a schemes object",
        "UNDECLARED_SECURITY_SCHEME" => {
            "a security requirement references an undeclared security scheme"
        }
        "ANONYMOUS_PATH_REQUIRED" => {
            "the Card must provide an anonymous authentication alternative"
        }
        "SOURCE_EXTENSION_V1_RETIRED" => "source-agent extension v1 is retired; use v2",
        "DUPLICATE_SOURCE_EXTENSION" => "only one source-agent extension is allowed",
        "SOURCE_EXTENSION_MUST_BE_OPTIONAL" => "source-agent extension must set required to false",
        "USE_PROVIDER_BOT_REF" => {
            "remove agentId from source-agent extension params and use provider_bot_ref instead"
        }
        "INVALID_PARTY" => "party must be first_party, second_party or third_party",
        "INVALID_PROVIDER_BOT_REF" => {
            "use 1 to 128 ASCII letters, digits, underscores, hyphens, periods or colons"
        }
        "LENGTH_LIMIT_EXCEEDED" => "summary must not exceed 4096 characters",
        "INVALID_URL" => {
            "use an absolute HTTP(S) URL, at most 2048 characters, without credentials, surrounding whitespace or a fragment"
        }
        "INVALID_BOT_ID" => {
            "bot_id must be non-empty and contain no control characters or surrounding whitespace"
        }
        "INVALID_REGISTRATION_TIME" => "registration time must not be negative",
        "UPDATE_REQUIRED" => "provide a Card source, visibility or AgentId to update",
        "IDENTITY_CONFLICT" => "the stored AgentId cannot be replaced",
        "METADATA_UPDATE_REQUIRED" => "provide status, catalog_labels, visibility or user_visibility to update",
        "METADATA_UPDATE_UNAVAILABLE" => "AgentCard metadata update is temporarily unavailable",
        "ONE_OWNER_REQUIRED" => "exactly one valid owner is required",
        "DEFAULT_OWNER_NOT_CONFIGURED" => {
            "supply an owner or ask the administrator to configure the Provider default owner"
        }
        "DEFAULT_OWNER_CONFIG_INVALID" => {
            "ask the administrator to correct the Provider default owner configuration"
        }
        "IDENTITY_AWARE_ADMISSION_REQUIRED" => {
            "use the identity-aware registration entry for this Provider authentication mode"
        }
        "PROVIDER_DISABLED" => "the Provider is disabled; contact its administrator",
        "PROVIDER_NAME_NOT_CONFIGURED" => "the Provider must have a valid configured name",
        "REGISTRATION_NOT_CONFIGURED" => "Agent registration is not configured on this server",
        "PROVIDER_UNAVAILABLE" => "the Provider configuration is unavailable",
        "CLIENT_UNAVAILABLE" => "the server Card retrieval client is unavailable",
        "UNKNOWN_KEY" => "a label key is not configured in the catalog label dictionary",
        "UNKNOWN_VALUE" => "a label value is not allowed by the catalog label dictionary",
        "MULTIPLE_VALUES_NOT_ALLOWED" => "this catalog label allows only one value",
        "INVALID_DICTIONARY" => {
            "ask the administrator to correct the catalog label dictionary configuration"
        }
        _ => return None,
    })
}

fn public_field(field: &str) -> Option<&str> {
    if field.len() > 160 {
        return None;
    }
    if field == "catalog_labels" || field.starts_with("catalog_labels.") {
        return Some("catalog_labels");
    }
    let mut normalized = String::with_capacity(field.len());
    let mut chars = field.chars();
    while let Some(ch) = chars.next() {
        if ch == '[' {
            let mut digits = 0;
            loop {
                match chars.next() {
                    Some(']') if digits > 0 => break,
                    Some(ch) if ch.is_ascii_digit() && digits < 6 => digits += 1,
                    _ => return None,
                }
            }
            normalized.push_str("[]");
        } else {
            normalized.push(ch);
        }
    }
    let known = matches!(
        normalized.as_str(),
        "provider_bot_ref"
            | "bot_id"
            | "owners"
            | "summary"
            | "agent_card_url"
            | "agent_card"
            | "agent_card.name"
            | "agent_card.description"
            | "agent_card.version"
            | "agent_card.defaultInputModes"
            | "agent_card.defaultInputModes[]"
            | "agent_card.defaultOutputModes"
            | "agent_card.defaultOutputModes[]"
            | "agent_card.supportedInterfaces"
            | "agent_card.supportedInterfaces[].url"
            | "agent_card.supportedInterfaces[].protocolBinding"
            | "agent_card.supportedInterfaces[].protocolVersion"
            | "agent_card.supportedInterfaces[].tenant"
            | "agent_card.capabilities"
            | "agent_card.capabilities.streaming"
            | "agent_card.capabilities.pushNotifications"
            | "agent_card.capabilities.extendedAgentCard"
            | "agent_card.capabilities.extensions"
            | "agent_card.capabilities.extensions[].uri"
            | "agent_card.capabilities.extensions[].description"
            | "agent_card.capabilities.extensions[].required"
            | "agent_card.capabilities.extensions[].params"
            | "agent_card.securitySchemes"
            | "agent_card.securityRequirements"
            | "agent_card.securityRequirements[]"
            | "agent_card.skills"
            | "agent_card.skills[].id"
            | "agent_card.skills[].name"
            | "agent_card.skills[].description"
            | "agent_card.skills[].tags"
            | "agent_card.skills[].tags[]"
            | "agent_card.skills[].examples"
            | "agent_card.skills[].examples[]"
            | "agent_card.skills[].inputModes"
            | "agent_card.skills[].inputModes[]"
            | "agent_card.skills[].outputModes"
            | "agent_card.skills[].outputModes[]"
            | "agent_card.skills[].securityRequirements"
            | "agent_card.skills[].securityRequirements[]"
            | "agent_card.provider"
            | "agent_card.provider.organization"
            | "agent_card.provider.url"
            | "agent_card.signatures"
            | "agent_card.documentationUrl"
            | "agent_card.iconUrl"
    );
    known.then_some(field)
}

#[cfg(test)]
mod tests;
