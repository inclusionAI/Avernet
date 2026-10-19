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
    let (field, raw_scope) = match scope_level {
        ScopeLevel::Group => ("groupId", request_context.group_id.as_deref()),
        ScopeLevel::Session => ("sessionId", request_context.session_id.as_deref()),
        ScopeLevel::Round => ("roundId", request_context.round_id.as_deref()),
    };
    let scope = non_empty_scope_value(field, raw_scope, scope_level)?.to_string();
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

fn non_empty_scope_value<'a>(
    field: &str,
    value: Option<&'a str>,
    scope_level: ScopeLevel,
) -> Result<&'a str, ApplicationError> {
    let Some(value) = value else {
        return Err(missing_scope_value(field, scope_level));
    };
    if value.trim().is_empty() {
        return Err(missing_scope_value(field, scope_level));
    }
    if value.chars().count() > MAX_SCOPE_CHARS {
        return Err(ApplicationError::payload_too_large(
            PAYLOAD_TOO_LARGE,
            format!(
                "param {field} exceeds {MAX_SCOPE_CHARS} characters when scope_level={}",
                scope_level_name(scope_level)
            ),
        ));
    }
    Ok(value)
}

fn missing_scope_value(field: &str, scope_level: ScopeLevel) -> ApplicationError {
    ApplicationError::invalid(
        INVALID_PARAM,
        format!(
            "param {field} is required when scope_level={}",
            scope_level_name(scope_level)
        ),
    )
}

fn scope_level_name(scope_level: ScopeLevel) -> &'static str {
    match scope_level {
        ScopeLevel::Group => "group",
        ScopeLevel::Session => "session",
        ScopeLevel::Round => "round",
    }
}
