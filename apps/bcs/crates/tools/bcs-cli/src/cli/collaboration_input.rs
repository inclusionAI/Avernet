use std::collections::{BTreeMap, BTreeSet};

use anyhow::{Result, anyhow};
use serde_json::json;

use super::{collaboration_output, input::parse_json_arg};

pub(super) fn build_panel_opening_message(
    component: Option<String>,
    params: Option<String>,
    tab_id: Option<String>,
    tab_title: Option<String>,
    tab_closable: Option<bool>,
) -> Result<Option<serde_json::Value>> {
    let Some(component) = component else {
        if params.is_some() || tab_id.is_some() || tab_title.is_some() || tab_closable.is_some() {
            return Err(anyhow!(
                "--panel-params and --panel-tab-* require --panel-component"
            ));
        }
        return Ok(None);
    };

    let mut opening_message = serde_json::Map::from_iter([
        ("type".to_string(), json!("panel")),
        ("component".to_string(), json!(component)),
    ]);
    if let Some(params) = params {
        let params = parse_json_arg(&params)?;
        if !params.is_object() {
            return Err(anyhow!("--panel-params must be a JSON object"));
        }
        opening_message.insert("params".to_string(), params);
    }

    let mut tab = serde_json::Map::new();
    if let Some(id) = tab_id {
        tab.insert("id".to_string(), json!(id));
    }
    if let Some(title) = tab_title {
        tab.insert("title".to_string(), json!(title));
    }
    if let Some(closable) = tab_closable {
        tab.insert("closable".to_string(), json!(closable));
    }
    if !tab.is_empty() {
        opening_message.insert("tab".to_string(), serde_json::Value::Object(tab));
    }

    Ok(Some(serde_json::Value::Object(opening_message)))
}

pub(super) fn parse_custom_group_bindings(
    values: &[String],
) -> Result<BTreeMap<String, bcs_protocol::ParticipantBindingInfo>> {
    let mut bindings = BTreeMap::new();
    for value in values {
        let (raw_binding, raw_bot_id) = value
            .split_once('=')
            .ok_or_else(|| anyhow!("Invalid --binding '{}'; expected ROLE=BOT_UUID", value))?;
        let binding = raw_binding.trim();
        let bot_id = raw_bot_id.trim();
        if binding.is_empty() || bot_id.is_empty() {
            return Err(anyhow!(
                "Invalid --binding '{}'; role and bot UUID must not be empty",
                value
            ));
        }
        if bindings.contains_key(binding) {
            return Err(anyhow!("Duplicate --binding role: {binding}"));
        }
        bindings.insert(
            binding.to_string(),
            bcs_protocol::ParticipantBindingInfo {
                source: "manual".to_string(),
                bot_ids: vec![bot_id.to_string()],
            },
        );
    }
    Ok(bindings)
}

pub(super) fn apply_group_participant_tags(
    participants: &mut Vec<bcs_protocol::ParticipantInfo>,
    lead_bot: &str,
    lead_role: Option<&str>,
    values: &[String],
) -> Result<()> {
    for value in values {
        let (raw_bot_id, raw_tag) = value.split_once('=').ok_or_else(|| {
            anyhow!(
                "Invalid --participant-tag '{}'; expected BOT_UUID=TAG",
                value
            )
        })?;
        let bot_id = raw_bot_id.trim();
        let tag = raw_tag.trim();
        if bot_id.is_empty() || tag.is_empty() {
            return Err(anyhow!(
                "Invalid --participant-tag '{}'; bot UUID and tag must not be empty",
                value
            ));
        }
        if bot_id == lead_bot
            && !participants
                .iter()
                .any(|participant| participant.bot_uuid == lead_bot)
        {
            participants.insert(
                0,
                bcs_protocol::ParticipantInfo {
                    bot_uuid: lead_bot.to_string(),
                    role: lead_role.map(str::to_string),
                    tags: Vec::new(),
                    message_view_scope: None,
                },
            );
        }
        let participant = participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == bot_id)
            .ok_or_else(|| {
                anyhow!(
                    "Invalid --participant-tag '{}'; bot '{}' is not a group participant",
                    value,
                    bot_id
                )
            })?;
        participant.tags.push(tag.to_string());
    }
    Ok(())
}

pub(super) fn validate_custom_group_bindings(
    bindings: &BTreeMap<String, bcs_protocol::ParticipantBindingInfo>,
    validation: &serde_json::Value,
    driver: &str,
) -> Result<()> {
    let slots = validation
        .get("participants")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| anyhow!("Validation response is missing participants"))?;
    let declared = slots
        .iter()
        .filter_map(|slot| slot.get("binding").and_then(serde_json::Value::as_str))
        .collect::<BTreeSet<_>>();
    for binding in bindings.keys() {
        if !declared.contains(binding.as_str()) {
            return Err(anyhow!(
                "--binding references undeclared participant role: {binding}"
            ));
        }
    }
    for slot in slots {
        let Some(binding) = slot.get("binding").and_then(serde_json::Value::as_str) else {
            continue;
        };
        let needs_binding = slot
            .get("required")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false)
            || slot
                .get("assigned")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false);
        if needs_binding && !bindings.contains_key(binding) {
            return Err(anyhow!("Missing --binding for participant role: {binding}"));
        }
    }
    if !bindings
        .values()
        .flat_map(|binding| &binding.bot_ids)
        .any(|bot_id| bot_id == driver)
    {
        return Err(anyhow!(
            "Driver bot must appear in at least one --binding: {driver}"
        ));
    }
    Ok(())
}

pub(super) fn emit_collaboration_validation(validation: &serde_json::Value, structured_mode: bool) {
    if structured_mode {
        println!(
            "{}",
            serde_json::to_string(validation).unwrap_or_else(|_| "{}".to_string())
        );
        return;
    }
    if validation.get("valid").and_then(serde_json::Value::as_bool) == Some(true) {
        let summary = validation.get("summary").cloned().unwrap_or_default();
        println!("VALID");
        println!(
            "  Participants: {}",
            summary
                .get("participants")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(0)
        );
        println!(
            "  Nodes: {}",
            summary
                .get("nodes")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(0)
        );
        if let Some(graph) = validation.get("graph") {
            if graph.get("execution_graph_mode").is_some() {
                print!("{}", collaboration_output::render(graph));
            }
        }
    } else if let Some(errors) = validation
        .get("errors")
        .and_then(serde_json::Value::as_array)
    {
        for error in errors {
            println!(
                "{} {}: {}",
                error
                    .get("code")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("INVALID"),
                error
                    .get("path")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("$"),
                error
                    .get("message")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("validation failed")
            );
        }
    }
    if let Some(warnings) = validation
        .get("warnings")
        .and_then(serde_json::Value::as_array)
    {
        for warning in warnings {
            println!(
                "WARNING {} {}: {}",
                warning
                    .get("code")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("WARNING"),
                warning
                    .get("path")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("$"),
                warning
                    .get("message")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("")
            );
        }
    }
}
