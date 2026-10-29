use std::collections::HashMap;

use anyhow::{Result, anyhow};
use serde_json::json;

use super::Commands;
use super::authentication::create_client;
use super::environment::get_token;
use super::resource_args::GroupContextCommands;

fn parse_group_context_policy(raw: &str, flag: &str) -> Result<serde_json::Value> {
    let value: serde_json::Value =
        serde_json::from_str(raw).map_err(|error| anyhow!("Invalid --{} JSON: {}", flag, error))?;
    if !value.is_object() {
        return Err(anyhow!("--{} must be a JSON object", flag));
    }
    Ok(value)
}

fn parse_group_context_owners(raw: &str) -> Result<serde_json::Value> {
    let value = parse_group_context_policy(raw, "owners")?;
    let invalid_shape =
        || anyhow!("--owners must be a JSON object containing a non-empty userIds array");
    let user_ids = value
        .get("userIds")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(invalid_shape)?;
    if user_ids.is_empty() {
        return Err(anyhow!("--owners.userIds must not be empty"));
    }
    if user_ids
        .iter()
        .any(|user_id| user_id.as_str().is_none_or(|value| value.trim().is_empty()))
    {
        return Err(anyhow!(
            "--owners.userIds must contain non-empty string actor ids"
        ));
    }
    Ok(value)
}

pub(super) fn group_context_query(
    group_id: Option<&str>,
    session_id: Option<&str>,
    round_id: Option<&str>,
) -> Vec<(String, String)> {
    let mut query = Vec::new();
    if let Some(value) = group_id {
        if !value.is_empty() {
            query.push(("groupId".to_string(), value.to_string()));
        }
    }
    if let Some(value) = session_id {
        query.push(("sessionId".to_string(), value.to_string()));
    }
    if let Some(value) = round_id {
        query.push(("roundId".to_string(), value.to_string()));
    }
    query
}

fn group_context_insert_optional_string(
    body: &mut serde_json::Map<String, serde_json::Value>,
    key: &str,
    value: Option<String>,
) {
    if let Some(value) = value {
        body.insert(key.to_string(), serde_json::Value::String(value));
    }
}

pub(super) fn group_context_insert_optional_timestamp(
    body: &mut serde_json::Map<String, serde_json::Value>,
    key: &str,
    value: Option<String>,
) {
    if let Some(value) = value {
        if let Ok(n) = value.parse::<i64>() {
            body.insert(
                key.to_string(),
                serde_json::Value::Number(serde_json::value::Number::from(n)),
            );
        } else {
            body.insert(key.to_string(), serde_json::Value::String(value));
        }
    }
}

pub(super) fn resolve_group_context_scope_levels(
    scope_level: Vec<String>,
    scope_levels: Vec<String>,
) -> Result<Vec<String>> {
    if !scope_level.is_empty() && !scope_levels.is_empty() {
        return Err(anyhow!(
            "--scope-level and --scope-levels cannot be used together"
        ));
    }
    if scope_levels.is_empty() {
        Ok(scope_level)
    } else {
        Ok(scope_levels)
    }
}

pub(super) fn group_context_add_body(
    name: String,
    scope_level: String,
    content: String,
    visible_to: String,
    collect_from: Option<String>,
    valid_from: Option<String>,
    valid_to: Option<String>,
) -> Result<serde_json::Value> {
    let mut body = serde_json::Map::new();
    body.insert("name".to_string(), serde_json::Value::String(name));
    body.insert(
        "scope_level".to_string(),
        serde_json::Value::String(scope_level),
    );
    body.insert("content".to_string(), serde_json::Value::String(content));
    body.insert(
        "visible_to".to_string(),
        parse_group_context_policy(&visible_to, "visible-to")?,
    );
    if let Some(collect_from) = collect_from {
        body.insert(
            "collect_from".to_string(),
            parse_group_context_policy(&collect_from, "collect-from")?,
        );
    }
    group_context_insert_optional_timestamp(&mut body, "valid_from", valid_from);
    group_context_insert_optional_timestamp(&mut body, "valid_to", valid_to);
    Ok(serde_json::Value::Object(body))
}

pub(super) fn group_context_update_body(
    name: String,
    scope_level: String,
    content: Option<String>,
    visible_to: Option<String>,
    collect_from: Option<String>,
    owners: Option<String>,
    valid_from: Option<String>,
    valid_to: Option<String>,
    change_reason: Option<String>,
) -> Result<serde_json::Value> {
    let mut body = serde_json::Map::new();
    body.insert("name".to_string(), serde_json::Value::String(name));
    body.insert(
        "scope_level".to_string(),
        serde_json::Value::String(scope_level),
    );
    group_context_insert_optional_string(&mut body, "content", content);
    if let Some(visible_to) = visible_to {
        body.insert(
            "visible_to".to_string(),
            parse_group_context_policy(&visible_to, "visible-to")?,
        );
    }
    if let Some(collect_from) = collect_from {
        body.insert(
            "collect_from".to_string(),
            parse_group_context_policy(&collect_from, "collect-from")?,
        );
    }
    if let Some(owners) = owners {
        body.insert("owners".to_string(), parse_group_context_owners(&owners)?);
    }
    group_context_insert_optional_timestamp(&mut body, "valid_from", valid_from);
    group_context_insert_optional_timestamp(&mut body, "valid_to", valid_to);
    group_context_insert_optional_string(&mut body, "change_reason", change_reason);
    Ok(serde_json::Value::Object(body))
}

/// Execute a group-context command against the context API.
pub(super) async fn execute(
    command: Commands,
    bcs_url: String,
    bcs_cookie: Option<String>,
    oauth_headers: Option<HashMap<String, String>>,
) -> Result<()> {
    match command {
        Commands::GroupContext { auth, command } => {
            let auth_value = get_token(auth.as_deref())?;
            let client = create_client(
                &bcs_url,
                &auth_value,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );
            let (operation, group_id, session_id, round_id, body) = match command {
                GroupContextCommands::Add {
                    group_id,
                    session_id,
                    round_id,
                    name,
                    scope_level,
                    content,
                    visible_to,
                    collect_from,
                    valid_from,
                    valid_to,
                } => (
                    "add",
                    group_id,
                    session_id,
                    round_id,
                    group_context_add_body(
                        name,
                        scope_level,
                        content,
                        visible_to,
                        collect_from,
                        valid_from,
                        valid_to,
                    )?,
                ),
                GroupContextCommands::Update {
                    group_id,
                    session_id,
                    round_id,
                    name,
                    scope_level,
                    content,
                    visible_to,
                    collect_from,
                    owners,
                    valid_from,
                    valid_to,
                    change_reason,
                } => (
                    "update",
                    group_id,
                    session_id,
                    round_id,
                    group_context_update_body(
                        name,
                        scope_level,
                        content,
                        visible_to,
                        collect_from,
                        owners,
                        valid_from,
                        valid_to,
                        Some(change_reason),
                    )?,
                ),
                GroupContextCommands::List {
                    group_id,
                    session_id,
                    round_id,
                    scope_level,
                    scope_levels,
                } => {
                    let resolved_scope_levels =
                        resolve_group_context_scope_levels(scope_level, scope_levels)?;
                    (
                        "list",
                        group_id,
                        session_id,
                        round_id,
                        json!({"scope_levels": if resolved_scope_levels.is_empty() { json!(null) } else { json!(resolved_scope_levels) }}),
                    )
                }
                GroupContextCommands::Get {
                    group_id,
                    session_id,
                    round_id,
                    name,
                    scope_level,
                    scope_levels,
                    limit,
                } => {
                    let resolved_scope_levels =
                        resolve_group_context_scope_levels(scope_level, scope_levels)?;
                    (
                        "get",
                        group_id,
                        session_id,
                        round_id,
                        json!({"name": name, "scope_levels": if resolved_scope_levels.is_empty() { json!(null) } else { json!(resolved_scope_levels) }, "limit": limit}),
                    )
                }
            };
            let query = group_context_query(
                group_id.as_deref(),
                session_id.as_deref(),
                round_id.as_deref(),
            );
            let query_refs = query
                .iter()
                .map(|(key, value)| (key.as_str(), value.as_str()))
                .collect::<Vec<_>>();
            let result = client
                .group_context_request(operation, &query_refs, body)
                .await?;
            println!("{}", serde_json::to_string(&result)?);
        }

        _ => unreachable!("wrong command family"),
    }

    Ok(())
}
