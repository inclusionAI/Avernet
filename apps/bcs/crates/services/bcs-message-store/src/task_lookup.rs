//! Bounded indexed task-handle resolution. No canonical bodies are loaded.
use bcs_db_api::DbStatement;
use bcs_domain::message_delivery::PersistedMessageDelivery;
use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError as Error;
use crate::MySqlMessageStore;

pub(crate) fn valid_prefix(prefix: &str) -> bool {
    (8..=32).contains(&prefix.len()) && prefix.bytes().all(|b| b.is_ascii_hexdigit())
}

pub(crate) async fn lookup(repo: &MySqlMessageStore, prefix: &str, cutoff: i64, legacy_intents: &[String]) -> Result<Vec<PersistedMessageDelivery>, Error> {
    lookup_batch(repo, &[prefix.to_owned()], cutoff, legacy_intents).await
}

pub(crate) async fn lookup_batch(repo: &MySqlMessageStore, prefixes: &[String], cutoff: i64, legacy_intents: &[String]) -> Result<Vec<PersistedMessageDelivery>, Error> {
    if prefixes.is_empty() || prefixes.len() > 20 || prefixes.iter().any(|p| !valid_prefix(p)) {
        return Err(Error::Invalid("task lookup requires 1..20 hex prefixes".into()));
    }
    validate_legacy(legacy_intents)?;
    let identity = expression(repo, "task_lookup_id");
    let retained = "((JSON_EXTRACT(semantic_projection_json, '$.task_rejected_id') IS NOT NULL AND created_at_ms >= ?) OR (JSON_EXTRACT(semantic_projection_json, '$.task_rejected_id') IS NULL AND (terminal_at_ms IS NULL OR terminal_at_ms >= ? OR (status = 'expired' AND may_have_been_sent = 1))))";
    let mut arms = Vec::new();
    let mut params: Vec<bcs_db_api::DbValue> = legacy_intents.iter().map(|id| id.as_str().into()).collect();
    let legacy = if legacy_intents.is_empty() { "SELECT NULL AS intent WHERE 0=1".to_string() }
        else { std::iter::repeat_n("SELECT ? AS intent", legacy_intents.len()).collect::<Vec<_>>().join(" UNION ALL ") };
    // COSEC: SQL expressions are fixed internal constants; all selectors are bound.
    // At most 2 identities per index/selector; dedup BEFORE limiting deliveries.
    for prefix in prefixes.iter().collect::<std::collections::BTreeSet<_>>() {
        let lower = prefix.to_ascii_lowercase();
        let upper = format!("{lower}g");
        for column in ["task_lookup_intent", "task_lookup_id"] {
            let column = expression(repo, column);
            arms.push(format!("SELECT * FROM bcs_message_deliveries WHERE env = ? AND delivery_id IN (SELECT (SELECT delivery_id FROM bcs_message_deliveries WHERE env = ? AND {identity} = candidates.task_identity AND {retained} ORDER BY state_version DESC, updated_at_ms DESC, delivery_id LIMIT 1) FROM (SELECT {identity} AS task_identity FROM bcs_message_deliveries WHERE env = ? AND {column} >= ? AND {column} < ? AND {identity} IS NOT NULL AND {retained} GROUP BY task_identity LIMIT 2) AS candidates)"));
            params.extend([repo.env.as_str().into(), repo.env.as_str().into(), cutoff.into(), cutoff.into(), repo.env.as_str().into(), lower.as_str().into(), upper.as_str().into(), cutoff.into(), cutoff.into()]);
        }
    }
    let uncertain = expression(repo, "task_lookup_uncertain");
    let outer_uncertain = if repo.flavor == bcs_db_api::DbSqlFlavor::Mysql { "pending.task_lookup_uncertain".to_string() }
        else { uncertain.replace("semantic_projection_json", "pending.semantic_projection_json") };
    let known_intent = if repo.flavor == bcs_db_api::DbSqlFlavor::Mysql { "known.task_lookup_intent".to_string() }
        else { expression(repo, "task_lookup_intent").replace("semantic_projection_json", "known.semantic_projection_json").replace("flow_kind", "known.flow_kind") };
    let known_task = if repo.flavor == bcs_db_api::DbSqlFlavor::Mysql { "known.task_lookup_id".to_string() }
        else { identity.replace("semantic_projection_json", "known.semantic_projection_json").replace("flow_kind", "known.flow_kind") };
    for prefix in prefixes.iter().collect::<std::collections::BTreeSet<_>>() {
        let lower = prefix.to_ascii_lowercase(); let upper = format!("{lower}g");
        arms.push(format!("SELECT * FROM bcs_message_deliveries WHERE env = ? AND delivery_id IN (SELECT delivery_id FROM (SELECT MIN(delivery_id) AS delivery_id FROM bcs_message_deliveries AS pending WHERE env = ? AND {uncertain} >= ? AND {uncertain} < ? AND NOT EXISTS (SELECT 1 FROM bcs_message_deliveries AS known WHERE known.env = pending.env AND {known_intent} = {outer_uncertain}) AND NOT EXISTS (SELECT 1 FROM bcs_message_deliveries AS known WHERE known.env = pending.env AND {known_task} = {outer_uncertain}) AND NOT EXISTS (SELECT 1 FROM legacy_intents AS legacy WHERE legacy.intent = {outer_uncertain}) GROUP BY {uncertain} LIMIT 2) AS uncertain_candidates)"));
        params.extend([repo.env.as_str().into(), repo.env.as_str().into(), lower.as_str().into(), upper.as_str().into()]);
    }
    let rows = repo.db.query(DbStatement::with_params(format!("WITH legacy_intents AS ({legacy}) {}", arms.join(" UNION ")), params)).await.map_err(|e| Error::Storage(e.to_string()))?;
    rows.into_iter().map(crate::delivery::row_to_delivery).collect()
}

pub(crate) fn memory(rows: &[PersistedMessageDelivery], prefix: &str, cutoff: i64, legacy_intents: &[String]) -> Result<Vec<PersistedMessageDelivery>, Error> {
    if !valid_prefix(prefix) { return Err(Error::Invalid("invalid task prefix".into())); }
    validate_legacy(legacy_intents)?;
    let prefix = prefix.to_ascii_lowercase();
    let mut tasks = std::collections::BTreeMap::<String, PersistedMessageDelivery>::new();
    let mut known_intents = std::collections::BTreeSet::new();
    for row in rows {
        let task = &row.semantic_projection_json["task"];
        if let Some(id) = row.semantic_projection_json["task_rejected_id"].as_str().or_else(||
            task["assignment_intent_id"].as_str().and_then(|id| id.strip_prefix("bcs_intent_"))) {
            known_intents.insert(id.to_owned());
        }
        if row.flow_kind == bcs_domain::message_delivery::DeliveryFlowKind::Task && task["leg"] == "dispatch" {
            if let Some(id) = task["task_id"].as_str() { known_intents.insert(id.replace('-', "").to_ascii_lowercase()); }
        }
    }
    for row in rows {
        if let Some(id) = row.semantic_projection_json["task_uncertain_id"].as_str() {
            if known_intents.contains(id) || legacy_intents.iter().any(|known| known == id) { continue; }
            if id.starts_with(&prefix) { tasks.insert(format!("unknown:{id}"), row.clone()); }
            continue;
        }
        if row.terminal_at_ms.is_some_and(|t| t < cutoff) && !(row.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Expired && row.state.may_have_been_sent) { continue; }
        if let Some(id) = row.semantic_projection_json["task_rejected_id"].as_str() {
            if row.created_at_ms >= cutoff && id.starts_with(&prefix) { tasks.insert(id.into(), row.clone()); }
            continue;
        }
        if row.flow_kind != bcs_domain::message_delivery::DeliveryFlowKind::Task { continue; }
        let task = &row.semantic_projection_json["task"];
        if task["leg"] != "dispatch" { continue; }
        let Some(id) = task["task_id"].as_str() else { continue; };
        let id = id.replace('-', "").to_ascii_lowercase();
        let intent = task["assignment_intent_id"].as_str().unwrap_or("").trim_start_matches("bcs_intent_").to_ascii_lowercase();
        if !id.starts_with(&prefix) && !intent.starts_with(&prefix) { continue; }
        if tasks.get(&id).is_none_or(|old| (row.state.state_version, row.updated_at_ms) > (old.state.state_version, old.updated_at_ms)) {
            tasks.insert(id, row.clone());
        }
    }
    let mut known = 0; let mut unknown = 0;
    Ok(tasks.into_iter().filter_map(|(id, row)| {
        let count = if id.starts_with("unknown:") { &mut unknown } else { &mut known };
        *count += 1; (*count <= 2).then_some(row)
    }).collect())
}

fn expression(repo: &MySqlMessageStore, name: &'static str) -> &'static str {
    if repo.flavor == bcs_db_api::DbSqlFlavor::Mysql { return name; }
    match name {
        "task_lookup_id" => "CASE WHEN flow_kind = 'task' AND json_extract(semantic_projection_json, '$.task.leg') = 'dispatch' THEN lower(replace(json_extract(semantic_projection_json, '$.task.task_id'), '-', '')) ELSE json_extract(semantic_projection_json, '$.task_rejected_id') END",
        "task_lookup_intent" => "CASE WHEN flow_kind = 'task' AND json_extract(semantic_projection_json, '$.task.leg') IN ('dispatch', 'result') THEN lower(replace(json_extract(semantic_projection_json, '$.task.assignment_intent_id'), 'bcs_intent_', '')) ELSE json_extract(semantic_projection_json, '$.task_rejected_id') END",
        "task_lookup_uncertain" => "json_extract(semantic_projection_json, '$.task_uncertain_id')",
        _ => unreachable!(),
    }
}

fn validate_legacy(intents: &[String]) -> Result<(), Error> {
    if intents.len() > 256 || intents.iter().any(|id| id.len() != 32 || !valid_prefix(id)) {
        return Err(Error::Invalid("legacy correlation budget exceeded or invalid".into()));
    }
    Ok(())
}
