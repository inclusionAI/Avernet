use super::*;

#[test]
fn all_management_codes_have_useful_safe_fallback_messages() {
    let codes = [
        Code::InvalidRequest,
        Code::Unauthenticated,
        Code::PermissionDenied,
        Code::BotNotFound,
        Code::MethodNotAllowed,
        Code::NotAcceptable,
        Code::ProviderBotRefConflict,
        Code::BotNameConflict,
        Code::SourceAgentConflict,
        Code::SourceAgentChangeNotAllowed,
        Code::TargetChanged,
        Code::CardUrlRequired,
        Code::RefreshSuperseded,
        Code::RequestTooLarge,
        Code::UnsupportedMediaType,
        Code::InvalidOwner,
        Code::InvalidCard,
        Code::CardSourceInvalid,
        Code::CardTooLarge,
        Code::CardAddressRejected,
        Code::CardInterfaceUnsupported,
        Code::CardContentUnsupported,
        Code::CardAuthUnsupported,
        Code::CardExtensionUnsupported,
        Code::RateLimited,
        Code::CardFetchFailed,
        Code::CardFetchTimeout,
        Code::ServiceUnavailable,
        Code::InternalError,
    ];
    for code in codes {
        let message = public_message(&ManagementError::new(
            code,
            "secret-token-field",
            "SECRET_REASON",
        ));
        assert!(
            !message.contains("secret") && !message.contains("SECRET"),
            "{message}"
        );
        assert!(!message.contains("A2A management request failed"));
        assert!(message.len() >= 16, "{code:?}: {message}");
    }
}

#[test]
fn known_card_failures_identify_the_field_and_correction() {
    for (field, reason, text) in [
        ("agent_card.name", "INVALID_NAME", "2 to 64 characters"),
        (
            "agent_card.description",
            "NONEMPTY_STRING_REQUIRED",
            "non-empty string",
        ),
        (
            "agent_card.supportedInterfaces",
            "NONEMPTY_ARRAY_REQUIRED",
            "non-empty array",
        ),
        (
            "agent_card.supportedInterfaces[12].url",
            "ABSOLUTE_URL_REQUIRED",
            "absolute URL",
        ),
        (
            "agent_card.capabilities.streaming",
            "BOOLEAN_REQUIRED",
            "boolean",
        ),
        (
            "agent_card.skills[0].tags[42]",
            "NONEMPTY_STRING_REQUIRED",
            "non-empty string",
        ),
        (
            "agent_card.capabilities.extensions[3].uri",
            "DUPLICATE_EXTENSION",
            "unique",
        ),
        (
            "agent_card.securityRequirements",
            "UNDECLARED_SECURITY_SCHEME",
            "undeclared security scheme",
        ),
    ] {
        let message = public_message(&ManagementError::new(Code::InvalidCard, field, reason));
        assert!(
            message.contains(field) && message.contains(text),
            "{message}"
        );
    }
}

#[test]
fn unsafe_unknown_or_unbounded_paths_and_reasons_are_never_echoed() {
    for field in [
        "private-field".to_string(),
        "agent_card.name\nBearer secret".into(),
        "agent_card.securitySchemes.secret".into(),
        "agent_card.skills[secret].id".into(),
        "agent_card.skills[].id".into(),
        "agent_card.skills[1234567].id".into(),
        "agent_card.skills[0]secret.id".into(),
        "agent_card.name".repeat(100),
    ] {
        let message = public_message(&ManagementError::new(
            Code::InvalidCard,
            &field,
            "NONEMPTY_STRING_REQUIRED",
        ));
        assert_eq!(message, "Invalid AgentCard: a non-empty string is required");
    }
    let message = public_message(&ManagementError::new(
        Code::InvalidCard,
        "agent_card.name",
        "PRIVATE_REASON",
    ));
    assert_eq!(message, "Invalid AgentCard");
}

#[test]
fn operational_failures_offer_recovery_without_exposing_raw_details() {
    for (code, reason, expected) in [
        (
            Code::InvalidRequest,
            "UPDATE_REQUIRED",
            "provide a Card source, visibility or AgentId",
        ),
        (
            Code::CardUrlRequired,
            "URL_REQUIRED",
            "inline Cards must be updated instead",
        ),
        (
            Code::TargetChanged,
            "CONCURRENT_UPDATE",
            "read the current Agent before retrying",
        ),
        (Code::CardFetchTimeout, "TIMEOUT", "timed out"),
        (
            Code::CardSourceInvalid,
            "INVALID_URL",
            "without credentials",
        ),
        (
            Code::InvalidOwner,
            "ONE_OWNER_REQUIRED",
            "exactly one valid owner",
        ),
        (
            Code::PermissionDenied,
            "PROVIDER_DISABLED",
            "Provider is disabled",
        ),
        (
            Code::ServiceUnavailable,
            "REGISTRATION_NOT_CONFIGURED",
            "not configured",
        ),
    ] {
        let message = public_message(&ManagementError::new(code, "private-field", reason));
        assert!(
            message.contains(expected) && !message.contains("private-field"),
            "{message}"
        );
    }
}

#[test]
fn catalog_label_failures_explain_policy_without_echoing_submitted_keys_or_values() {
    for (reason, expected) in [
        ("UNKNOWN_KEY", "label key is not configured"),
        ("UNKNOWN_VALUE", "label value is not allowed"),
        ("MULTIPLE_VALUES_NOT_ALLOWED", "allows only one value"),
    ] {
        let message = public_message(&ManagementError::new(
            Code::InvalidRequest,
            "catalog_labels.private-credential[0]",
            reason,
        ));
        assert!(
            message.contains("catalog_labels:") && message.contains(expected),
            "{message}"
        );
        assert!(!message.contains("private-credential"));
    }
    let message = public_message(&ManagementError::new(
        Code::InvalidRequest,
        "catalog_labels.private-credential",
        "PRIVATE_REASON",
    ));
    assert_eq!(message, "Invalid AgentCard management request");
}
