//! Store-owned projection of Group metadata. Not a complete discovery Card.
//! No member/definition reads, network access, or public Card query API.

use super::*;
use bcs_service_api::GroupKind;
use serde_json::{Value as JsonValue, json};

// Protocol identifier shared with the discovery spec, not a fetched endpoint.
const EXTENSION_URI: &str = "https://schemas.example.com/bcn/discovery-card/v1";
const MW_DESCRIPTION: &str = "Manager–Worker 群：";
const SM_DESCRIPTION: &str = "State Machine 群：";

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

fn template(id: &str, name: &str, kind: &str, prefix: &str) -> JsonValue {
    let description = format!("{prefix}{name}");
    json!({
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
    })
}

pub(crate) fn group_template(group: &Group) -> Option<JsonValue> {
    if group.group_kind != GroupKind::Normal { return None; }
    let (kind, prefix) = match group.group_strategy {
        GroupStrategy::ManagerWorker => ("manager_worker", MW_DESCRIPTION),
        GroupStrategy::StateMachine => ("state_machine", SM_DESCRIPTION),
        GroupStrategy::Chat => return None,
    };
    let name = group.label.as_deref().filter(|label| !label.is_empty()).unwrap_or(&group.id);
    Some(template(&group.id, name, kind, prefix))
}

// The caller holds the groups write lock as well: groups and templates commit together.
pub(crate) fn update_memory_template(cards: &mut HashMap<String, JsonValue>, group: &Group) {
    if let Some(fresh) = group_template(group) {
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
        let prefix = "CASE g.group_strategy WHEN 'manager_worker' THEN ? ELSE ? END";
        let description = match self.flavor {
            DbSqlFlavor::Mysql => format!("CONCAT(({prefix}), {name})"),
            DbSqlFlavor::Sqlite => format!("(({prefix}) || {name})"),
        };
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
        let encode = |kind, prefix| serde_json::to_string(&template(id, id, kind, prefix))
            .map(Value::from).map_err(|error| ServiceError::InternalError(format!("serialize Group Card template: {error}")));
        Ok(DbTransactionStep::Execute(DbStatement::with_params(sql, vec![
            encode("manager_worker", MW_DESCRIPTION)?, encode("state_machine", SM_DESCRIPTION)?,
            Value::from(MW_DESCRIPTION), Value::from(SM_DESCRIPTION),
            Value::from(MW_DESCRIPTION), Value::from(SM_DESCRIPTION),
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
            assert_eq!(group_template(&group).unwrap(), expected);
        }
    }

    #[test]
    fn empty_name_id_encoding_and_strategy_scope() {
        let mut group = Group::new("../群/?#%", "manager", vec![]);
        group.group_strategy = GroupStrategy::StateMachine;
        for label in [None, Some(String::new())] {
            group.label = label;
            let card = group_template(&group).unwrap();
            assert_eq!(card["name"], group.id);
            assert_eq!(card["supportedInterfaces"][0]["url"], "local://groups/%2E%2E%2F%E7%BE%A4%2F%3F%23%25");
        }
        group.group_strategy = GroupStrategy::Chat;
        assert!(group_template(&group).is_none());
        group.group_strategy = GroupStrategy::ManagerWorker;
        group.group_kind = GroupKind::Dm;
        assert!(group_template(&group).is_none());
    }
}
