//! HTTP projections for legacy Bot views and full optional A2A Cards.
use super::*;

pub(super) fn list_bot_entry_to_json(bot: BotListEntry) -> Value {
    let wire_capabilities = to_wire_capabilities(bot.capabilities);
    let skill_names: Vec<String> = wire_capabilities
        .skills
        .iter()
        .map(|s| s.name.clone())
        .collect();
    let mut caps =
        serde_json::to_value(&wire_capabilities).unwrap_or_else(|_| serde_json::json!({}));
    if let Some(obj) = caps.as_object_mut() {
        obj.insert("skills".to_string(), serde_json::json!(skill_names));
    }

    serde_json::json!({
        "bot_uuid": bot.bot_uuid,
        "capabilities": caps,
        "created_by": bot.created_by
    })
}

pub(super) fn bot_detail_to_json(bot: BotDetailResult) -> Value {
    let agent_card = bot.agent_card;
    with_agent_card(serde_json::json!({
        "bot_uuid": bot.bot_uuid,
        "capabilities": to_wire_capabilities(bot.capabilities),
        "status": actor_status_to_wire(bot.status),
        "created_by": bot.created_by,
        "actor_kind": bot.actor_kind,
        "env": bot.env,
        "dynamic_status": to_wire_dynamic_status_response(bot.dynamic_status),
    }), agent_card)
}

pub(super) fn bot_query_entry_to_paged_json(bot: BotQueryEntry) -> Value {
    let agent_card = bot.agent_card;
    with_agent_card(serde_json::json!({
        "bot_uuid": bot.bot_uuid,
        "capabilities": to_wire_capabilities(bot.capabilities),
        "created_by": bot.created_by
    }), agent_card)
}

pub(super) fn bot_query_entry_to_my_json(bot: BotQueryEntry) -> Value {
    let agent_card = bot.agent_card;
    let wire_capabilities = to_wire_capabilities(bot.capabilities);
    let skill_names: Vec<String> = wire_capabilities
        .skills
        .iter()
        .map(|s| s.name.clone())
        .collect();
    let mut caps =
        serde_json::to_value(&wire_capabilities).unwrap_or_else(|_| serde_json::json!({}));
    if let Some(obj) = caps.as_object_mut() {
        obj.insert("skills".to_string(), serde_json::json!(skill_names));
    }

    // Spec §8 mine row: the sampled per-item
    // relation rides the legacy page additively
    // (absent on pages without a sampled
    // relation).
    let access_relation =
        bot.access_relation.map(Value::String);

    with_agent_card(serde_json::json!({
        "bot_uuid": bot.bot_uuid,
        "capabilities": caps,
        "visibility": bot.visibility,
        "created_by": bot.created_by,
        "actor_kind": bot.actor_kind,
        "env": bot.env,
        "status": actor_status_to_wire(bot.status),
        "dynamic_status": to_wire_dynamic_status_response(bot.dynamic_status),
        "access_relation": access_relation,
    }), agent_card)
}

pub(super) fn bot_query_entry_to_query_json(bot: BotQueryEntry) -> Value {
    let agent_card = bot.agent_card;
    with_agent_card(serde_json::json!({
        "bot_uuid": bot.bot_uuid,
        "capabilities": to_wire_capabilities(bot.capabilities),
        "visibility": bot.visibility,
        "status": actor_status_to_wire(bot.status),
        "actor_kind": bot.actor_kind,
        "dynamic_status": to_wire_dynamic_status_response(bot.dynamic_status),
    }), agent_card)
}

pub(super) fn bot_visibility_to_json(result: BotVisibilityQueryResult) -> Value {
    serde_json::json!({
        "success": true,
        "data": {
            "bot_uuid": result.bot_uuid,
            "visibility": result.visibility
        }
    })
}


fn with_agent_card(mut value: Value, card: Option<Value>) -> Value {
    if let Some(card) = card { value["agent_card"] = card; }
    value
}
