use super::*;

impl SqlGroupContextStore {
    pub(super) async fn has_slot(&self, group_id: &str, name: &str) -> StoreResult<bool> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT 1 FROM bcs_group_context_slots WHERE group_id = ? AND name = ?",
                vec![group_id.into(), name.into()],
            ))
            .await
            .map_err(GroupContextStoreError::backend)?;
        Ok(!rows.is_empty())
    }

    pub(super) async fn load_slots(&self, group_id: &str) -> StoreResult<Vec<GroupContextSlot>> {
        let sql = format!(
            "SELECT name, description, creator, modifier, {}, {} FROM bcs_group_context_slots WHERE group_id = ? ORDER BY name",
            self.timestamp_ms_expr("gmt_create", "gmt_create_ms"),
            self.timestamp_ms_expr("gmt_modified", "gmt_modified_ms"),
        );
        let rows = self.db.query(DbStatement::with_params(
            sql,
            vec![group_id.into()],
        )).await.map_err(GroupContextStoreError::backend)?;
        rows.iter()
            .map(|row| {
                let field =
                    |name| db_get_column(row, name).map_err(GroupContextStoreError::backend);
                Ok(GroupContextSlot {
                    name: field("name")?,
                    description: field("description")?,
                    creator: field("creator")?,
                    modifier: field("modifier")?,
                    gmt_create: millis_column(row, "gmt_create_ms")?,
                    gmt_modified: millis_column(row, "gmt_modified_ms")?,
                })
            })
            .collect()
    }

    pub(super) async fn write_slot(
        &self,
        group_id: &str,
        actor: &str,
        change: GroupContextSlotChange,
    ) -> StoreResult<()> {
        let now = self.timestamp_param(Utc::now());
        let is_edit = matches!(&change, GroupContextSlotChange::Edit { .. });
        let (statement, name) = match change {
            GroupContextSlotChange::Add { name, description } => (
                DbStatement::with_params(
                    self.sql_with_timestamp_params(
                        "INSERT INTO bcs_group_context_slots (group_id, name, description, creator, modifier, gmt_create, gmt_modified) \
                         VALUES (?, ?, ?, ?, ?, __bcs_group_context_timestamp_ms__, __bcs_group_context_timestamp_ms__)",
                    ),
                    vec![
                        group_id.into(),
                        name.as_str().into(),
                        description.into(),
                        actor.into(),
                        actor.into(),
                        now.clone(),
                        now,
                    ],
                ),
                name,
            ),
            GroupContextSlotChange::Edit {
                name,
                new_name,
                description,
            } => (
                DbStatement::with_params(
                    self.sql_with_timestamp_params(
                        "UPDATE bcs_group_context_slots SET name = COALESCE(?, name), description = COALESCE(?, description), \
                         modifier = ?, gmt_modified = __bcs_group_context_timestamp_ms__ WHERE group_id = ? AND name = ?",
                    ),
                    vec![
                        new_name.as_deref().into(),
                        description.as_deref().into(),
                        actor.into(),
                        now,
                        group_id.into(),
                        name.as_str().into(),
                    ],
                ),
                name,
            ),
            GroupContextSlotChange::Delete { name } => (
                DbStatement::with_params(
                    "DELETE FROM bcs_group_context_slots WHERE group_id = ? AND name = ?",
                    vec![group_id.into(), name.as_str().into()],
                ),
                name,
            ),
        };
        let result = self.db.execute(statement).await.map_err(|error| {
            if error.is_duplicate_key() {
                GroupContextStoreError::DuplicateActive {
                    name: name.clone(),
                    scope: group_id.into(),
                }
            } else {
                GroupContextStoreError::backend(error)
            }
        })?;
        // MySQL reports zero changed rows for a repeated identical edit.
        if result.affected_rows == 0 && !(is_edit && self.has_slot(group_id, &name).await?) {
            return Err(GroupContextStoreError::NotFound {
                name,
                scope: group_id.into(),
            });
        }
        Ok(())
    }
}
