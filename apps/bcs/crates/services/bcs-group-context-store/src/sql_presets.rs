use super::*;
use bcs_db_api::DbTransactionParam;

impl SqlGroupContextStore {
    pub(super) async fn has_preset(&self, group_id: &str, name: &str) -> StoreResult<bool> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT 1 FROM bcs_group_context_item_presets WHERE group_id = ? AND name = ?",
                vec![group_id.into(), name.into()],
            ))
            .await
            .map_err(GroupContextStoreError::backend)?;
        Ok(!rows.is_empty())
    }

    pub(super) async fn load_presets(&self, group_id: &str) -> StoreResult<Vec<GroupContextItemPreset>> {
        let sql = format!(
            "SELECT name, description, creator, modifier, {}, {} FROM bcs_group_context_item_presets WHERE group_id = ? ORDER BY name",
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
                Ok(GroupContextItemPreset {
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

    pub(super) async fn write_preset(
        &self,
        group_id: &str,
        actor: &str,
        change: GroupContextItemPresetChange,
    ) -> StoreResult<()> {
        let now = self.timestamp_param(Utc::now());
        let mysql_edit = self.flavor == DbSqlFlavor::Mysql
            && matches!(&change, GroupContextItemPresetChange::Edit { .. });
        let (statement, name) = match change {
            GroupContextItemPresetChange::Add { name, description } => (
                DbStatement::with_params(
                    self.sql_with_timestamp_params(
                        "INSERT INTO bcs_group_context_item_presets (group_id, name, description, creator, modifier, gmt_create, gmt_modified) \
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
            GroupContextItemPresetChange::Edit {
                name,
                new_name,
                description,
            } => (
                DbStatement::with_params(
                    self.sql_with_timestamp_params(
                        "UPDATE bcs_group_context_item_presets SET name = COALESCE(?, name), description = COALESCE(?, description), \
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
            GroupContextItemPresetChange::Delete { name } => (
                DbStatement::with_params(
                    "DELETE FROM bcs_group_context_item_presets WHERE group_id = ? AND name = ?",
                    vec![group_id.into(), name.as_str().into()],
                ),
                name,
            ),
        };
        let matched = if mysql_edit {
            self.execute_mysql_preset_edit(group_id, &name, statement).await
        } else {
            self.db.execute(statement).await.map(|result| result.affected_rows > 0)
        }
        .map_err(|error| {
            if error.is_duplicate_key() {
                GroupContextStoreError::DuplicateActive {
                    name: name.clone(),
                    scope: group_id.into(),
                }
            } else {
                GroupContextStoreError::backend(error)
            }
        })?;
        if !matched {
            return Err(GroupContextStoreError::NotFound {
                name,
                scope: group_id.into(),
            });
        }
        Ok(())
    }

    async fn execute_mysql_preset_edit(
        &self,
        group_id: &str,
        name: &str,
        statement: DbStatement,
    ) -> Result<bool, DbError> {
        // MySQL counts changed rows. Lock the matched row before updating and
        // bind its ID, so an absent match cannot update a later same-name insert.
        let lookup = DbStatement::with_params(
            "SELECT (SELECT id FROM bcs_group_context_item_presets \
             WHERE group_id = ? AND name = ? FOR UPDATE) AS preset_id",
            vec![group_id.into(), name.into()],
        );
        let update = DbStatement::with_transaction_params(
            format!("{} AND id = ?", statement.sql()),
            statement.params().iter().cloned().map(DbTransactionParam::from)
                .chain([DbTransactionParam::query_result(0, 0, "preset_id")])
                .collect(),
        );
        let results = self.db.transaction(vec![
            DbTransactionStep::Query(lookup),
            DbTransactionStep::Execute(update),
        ]).await?;
        match results.as_slice() {
            [DbTransactionStepResult::Rows(rows), DbTransactionStepResult::Executed(_)] => {
                let [row] = rows.as_slice() else {
                    return Err(DbError::InvalidInput("expected one preset lookup row".into()));
                };
                Ok(db_get_column_opt::<u64>(row, "preset_id")?.is_some())
            }
            _ => Err(DbError::InvalidInput("expected preset lookup and update results".into())),
        }
    }
}
