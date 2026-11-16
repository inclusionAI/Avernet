//! Store-owned projection of Group metadata. Not a complete discovery Card.
//! No member/definition reads or network access. Snapshot reads share these display rules.

use super::*;
use bcs_service_api::GroupKind;
use serde_json::{Value as JsonValue, json};

// Protocol identifier shared with the discovery spec, not a fetched endpoint.
const EXTENSION_URI: &str = "https://schemas.example.com/bcn/discovery-card/v1";

fn path_segment(id: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789ABCDEF";
    let mut encoded = String::new();
    for byte in id.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'~') {
            encoded.push(char::from(byte));
        } else {
            // Encode dots too, so IDs such as '..' cannot become path traversal.
            encoded.push('%');
            encoded.push(char::from(HEX[(byte >> 4) as usize]));
            encoded.push(char::from(HEX[(byte & 15) as usize]));
        }
    }
    encoded
}

fn template(id: &str, group_kind: GroupKind, strategy: GroupStrategy, label: Option<&str>, context: Option<&str>) -> Option<JsonValue> {
    if group_kind != GroupKind::Normal { return None; }
    let kind = match strategy {
        GroupStrategy::ManagerWorker => "manager_worker",
        GroupStrategy::StateMachine => "state_machine",
        GroupStrategy::Chat => return None,
    };
    let name = label.filter(|label| !label.is_empty()).unwrap_or(id);
    let description = context.unwrap_or_default();
    Some(json!({
        "name": name,
        "description": description,
        "supportedInterfaces": [{
            "url": format!("local://groups/{}", path_segment(id)),
            "protocolBinding": "LOCAL",
            "protocolVersion": "1.0"
        }],
        "capabilities": {"extensions": [{
            "uri": EXTENSION_URI,
            "description": "BCN discovery composition",
            "required": true,
            "params": {"groupKind": kind}
        }]},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{"id": "group.execute", "name": name,
            "description": description, "tags": [kind]}]
    }))
}

/// Refresh display fields on a stored template without losing opaque content.
/// This also projects pre-upgrade templates using the current Group context,
/// without writing a migration/backfill during a catalog read.
pub(crate) fn project_stored_template(mut card: JsonValue, id: &str, label: Option<&str>, context: Option<&str>) -> ServiceResult<JsonValue> {
    let name = label.filter(|value| !value.is_empty()).unwrap_or(id);
    let description = context.unwrap_or_default();
    let fields = card.as_object_mut().ok_or_else(|| ServiceError::InternalError("Group Card template must be an object".into()))?;
    let skill = fields.get_mut("skills").and_then(JsonValue::as_array_mut)
        .and_then(|skills| skills.first_mut()).and_then(JsonValue::as_object_mut)
        .ok_or_else(|| ServiceError::InternalError("Group Card template must contain its group skill".into()))?;
    skill.insert("name".into(), json!(name));
    skill.insert("description".into(), json!(description));
    fields.insert("name".into(), json!(name));
    fields.insert("description".into(), json!(description));
    Ok(card)
}

// The caller holds the groups write lock as well: groups and templates commit together.
pub(crate) fn update_memory_template(cards: &mut HashMap<String, JsonValue>, group: &Group) {
    if let Some(fresh) = template(&group.id, group.group_kind, group.group_strategy, group.label.as_deref(), group.context.as_deref()) {
        match cards.get_mut(&group.id) {
            Some(card) => {
                card["name"] = fresh["name"].clone();
                card["description"] = fresh["description"].clone();
                card["skills"][0]["name"] = fresh["skills"][0]["name"].clone();
                card["skills"][0]["description"] = fresh["skills"][0]["description"].clone();
            }
            None => { cards.insert(group.id.clone(), fresh); }
        }
    }
}

impl MySqlGroupStore {
    /// Run AFTER the Group write, in the SAME transaction. INSERT SELECT uses
    /// authoritative identity/label/strategy, not a cached Group snapshot.
    /// JSON_SET patches only display fields on conflict, preserving interfaces
    /// and extensions without an out-of-transaction read/overwrite race.
    pub(crate) fn card_template_upsert_step(&self, id: &str) -> ServiceResult<DbTransactionStep> {
        let name = "COALESCE(NULLIF(g.label, ''), g.group_id)";
        let description = "COALESCE(g.context, '')";
        let incoming = match self.flavor {
            DbSqlFlavor::Mysql => "VALUES(card_template)",
            DbSqlFlavor::Sqlite => "excluded.card_template",
        };
        let extract = |path: &str| match self.flavor {
            DbSqlFlavor::Mysql => format!("JSON_UNQUOTE(JSON_EXTRACT({incoming}, '{path}'))"),
            DbSqlFlavor::Sqlite => format!("JSON_EXTRACT({incoming}, '{path}')"),
        };
        let display_paths = ["$.name", "$.description", "$.skills[0].name", "$.skills[0].description"];
        let patches = display_paths.iter()
            .map(|path| format!("'{path}', {}", extract(path))).collect::<Vec<_>>().join(", ");
        let updated = format!("JSON_SET(bcs_group_agent_cards.card_template, {patches})");
        let conflict = self.flavor.on_conflict_update(
            &["env", "group_id"], &[],
            &[("card_template", updated.as_str()), ("gmt_modified", self.flavor.now())],
        );
        let sql = format!(
            "INSERT INTO bcs_group_agent_cards (env, group_id, card_template) \
             SELECT g.env, g.group_id, JSON_SET(\
                 CASE g.group_strategy WHEN 'manager_worker' THEN ? ELSE ? END, \
                 '$.name', {name}, '$.description', {description}, \
                 '$.skills[0].name', {name}, '$.skills[0].description', {description}) \
             FROM bcs_groups AS g WHERE g.env = ? AND g.group_id = ? \
             AND g.group_kind = 'normal' AND g.group_strategy IN ('manager_worker', 'state_machine') \
             {conflict}"
        );
        // Both fixed strategies produce Some(Card); serde serializes Some as the
        // Card itself. Share eligibility, type and display defaults with memory.
        let encode = |strategy| serde_json::to_string(&template(id, GroupKind::Normal, strategy, None, None))
            .map(Value::from).map_err(|error| ServiceError::InternalError(format!("serialize Group Card template: {error}")));
        Ok(DbTransactionStep::Execute(DbStatement::with_params(sql, vec![
            encode(GroupStrategy::ManagerWorker)?, encode(GroupStrategy::StateMachine)?,
            Value::from(self.env.as_str()), Value::from(id),
        ])))
    }

    pub(crate) fn card_template_delete_step(&self, id: DbTransactionParam) -> DbTransactionStep {
        DbTransactionStep::Execute(DbStatement::with_transaction_params(
            "DELETE FROM bcs_group_agent_cards WHERE env = ? AND group_id = ?",
            vec![DbTransactionParam::value(self.env.as_str()), id],
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn templates_match_fixtures_and_do_not_materialize_dependencies() {
        let fixtures = [
            (GroupStrategy::ManagerWorker, include_str!("../tests/fixtures/manager-worker-card-template.json")),
            (GroupStrategy::StateMachine, include_str!("../tests/fixtures/state-machine-card-template.json")),
        ];
        for (strategy, fixture) in fixtures {
            let expected: JsonValue = serde_json::from_str(fixture).unwrap();
            let url = expected["supportedInterfaces"][0]["url"].as_str().unwrap();
            let id = url.strip_prefix("local://groups/").unwrap();
            let mut group = Group::new(id, "manager", vec![]);
            group.group_strategy = strategy;
            group.label = Some(expected["name"].as_str().unwrap().into());
            group.context = Some(expected["description"].as_str().unwrap().into());
            assert_eq!(template(&group.id, group.group_kind, group.group_strategy, group.label.as_deref(), group.context.as_deref()).unwrap(), expected);
        }
    }

    #[test]
    fn empty_name_id_encoding_and_strategy_scope() {
        let mut group = Group::new("../群/?#%", "manager", vec![]);
        group.group_strategy = GroupStrategy::StateMachine;
        for label in [None, Some(String::new())] {
            group.label = label;
            let card = template(&group.id, group.group_kind, group.group_strategy, group.label.as_deref(), None).unwrap();
            assert_eq!(card["name"], group.id);
            assert_eq!(card["description"], "");
            assert_eq!(card["skills"][0]["description"], "");
            assert_eq!(card["supportedInterfaces"][0]["url"], "local://groups/%2E%2E%2F%E7%BE%A4%2F%3F%23%25");
        }
        group.group_strategy = GroupStrategy::Chat;
        assert!(template(&group.id, group.group_kind, group.group_strategy, group.label.as_deref(), None).is_none());
        group.group_strategy = GroupStrategy::ManagerWorker;
        group.group_kind = GroupKind::Dm;
        assert!(template(&group.id, group.group_kind, group.group_strategy, group.label.as_deref(), None).is_none());
    }
}
