//! One indexed, environment-scoped Group/Card read; no participant or cache reads.

use super::*;
use bcs_service_api::types::GroupCardSnapshot;

impl MySqlGroupStore {
    pub(crate) async fn read_card_snapshot_sql(&self, id: &str) -> ServiceResult<Option<GroupCardSnapshot>> {
        let rows = self.db.query_with(&self.logical_db,
            "SELECT g.group_id, g.group_kind, g.group_strategy, g.visibility, g.record_status, \
             g.label, g.context, c.card_template FROM bcs_groups AS g \
             LEFT JOIN bcs_group_agent_cards AS c ON c.env = g.env AND c.group_id = g.group_id \
             WHERE g.env = ? AND g.group_id = ? LIMIT 1",
            vec![Value::from(self.env.as_str()), Value::from(id)],
        ).await.map_err(|error| ServiceError::InternalError(format!("read Group Card snapshot: {error}")))?;
        let Some(row) = rows.first() else { return Ok(None); };
        let required = |key: &str| db_get_column::<String>(row, key)
            .map_err(|error| ServiceError::InternalError(format!("decode Group Card {key}: {error}")));
        let optional = |key: &str| db_get_column_opt::<String>(row, key)
            .map_err(|error| ServiceError::InternalError(format!("decode Group Card {key}: {error}")));
        let group_kind = serde_json::from_value(serde_json::Value::String(required("group_kind")?))
            .map_err(|error| ServiceError::InternalError(format!("decode Group Card kind: {error}")))?;
        let group_strategy = serde_json::from_value(serde_json::Value::String(required("group_strategy")?))
            .map_err(|error| ServiceError::InternalError(format!("decode Group Card strategy: {error}")))?;
        let label = optional("label")?;
        let context = optional("context")?;
        let card_template = optional("card_template")?.map(|raw| {
            let card = serde_json::from_str(&raw)
                .map_err(|error| ServiceError::InternalError(format!("decode Group Card template: {error}")))?;
            card_template::project_stored_template(card, id, label.as_deref(), context.as_deref())
        }).transpose()?;
        Ok(Some(GroupCardSnapshot {
            group_id: required("group_id")?, group_kind, group_strategy,
            visibility: required("visibility")?, record_status: required("record_status")?, card_template,
        }))
    }
}
