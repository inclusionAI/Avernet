use bcs_domain::ScopeLevel;
use bcs_service_api::application::v1::{ApplicationError, GroupContextRequestContext};

const INVALID_PARAM: &str = "invalid_param";
const PAYLOAD_TOO_LARGE: &str = "payload_too_large";
const MAX_SCOPE_CHARS: usize = 255;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedScope {
    pub scope_level: ScopeLevel,
    pub scope: String,
}

pub fn resolve_scope(
    request_context: &GroupContextRequestContext,
    scope_level: ScopeLevel,
) -> Result<ResolvedScope, ApplicationError> {
    require_non_empty("groupId", &request_context.group_id)?;
    let scope = match scope_level {
        ScopeLevel::Group => request_context.group_id.clone(),
        ScopeLevel::Session => request_context
            .session_id
            .as_deref()
            .ok_or_else(|| missing("sessionId"))
            .and_then(|value| non_empty_value("sessionId", value))?
            .to_string(),
        ScopeLevel::Round => request_context
            .round_id
            .as_deref()
            .ok_or_else(|| missing("roundId"))
            .and_then(|value| non_empty_value("roundId", value))?
            .to_string(),
    };
    Ok(ResolvedScope { scope_level, scope })
}

pub fn resolve_scopes(
    request_context: &GroupContextRequestContext,
    scope_levels: &[ScopeLevel],
) -> Result<Vec<ResolvedScope>, ApplicationError> {
    scope_levels
        .iter()
        .copied()
        .map(|scope_level| resolve_scope(request_context, scope_level))
        .collect()
}

fn require_non_empty(field: &str, value: &str) -> Result<(), ApplicationError> {
    non_empty_value(field, value).map(|_| ())
}

fn non_empty_value<'a>(field: &str, value: &'a str) -> Result<&'a str, ApplicationError> {
    if value.trim().is_empty() {
        return Err(missing(field));
    }
    if value.chars().count() > MAX_SCOPE_CHARS {
        return Err(ApplicationError::payload_too_large(
            PAYLOAD_TOO_LARGE,
            format!("{field} exceeds {MAX_SCOPE_CHARS} characters"),
        ));
    }
    Ok(value)
}

fn missing(field: &str) -> ApplicationError {
    ApplicationError::invalid(INVALID_PARAM, format!("{field} is required"))
}
