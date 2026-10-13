//! Serialize public Group writes against Provider policy tightening in the DB.
use super::*;

impl MySqlGroupStore {
    /// Legacy public writes share the versioned transaction so policy checks,
    /// participant membership and visibility commit under the same locks.
    pub(crate) async fn guarded_legacy_mutation(
        &self,
        id: &str,
        mutation: GroupEventfulMutation,
    ) -> ServiceResult<()> {
        let group = self
            .try_get(id)
            .await?
            .ok_or_else(|| ServiceError::GroupNotFound(id.into()))?;
        self.commit_eventful_mutation_sql(CommitGroupEventfulMutation {
            group_id: id.into(),
            expected_version: group.version,
            mutated_at_ms: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(|error| ServiceError::InternalError(error.to_string()))?
                .as_millis() as u64,
            mutation,
            event: None,
        })
        .await?;
        Ok(())
    }
    pub(crate) fn public_provider_lock(&self, group: &Group) -> ServiceResult<Option<DbStatement>> {
        if !self.provider_scope_guard || group.visibility != "public" {
            return Ok(None);
        }
        let mut ids = group
            .participants
            .iter()
            .map(|p| p.bot_uuid.clone())
            .collect::<Vec<_>>();
        ids.sort();
        ids.dedup();
        if ids.len() > 256 {
            return Err(ServiceError::InvalidOperation {
                message: "public group authorization exceeds 256 actors".into(),
                request_id: None,
            });
        }
        let placeholders = vec!["?"; ids.len().max(1)].join(",");
        if ids.is_empty() {
            ids.push(String::new());
        }
        let mut params = vec![Value::from(self.env.as_str())];
        params.extend(ids.into_iter().map(Value::from));
        let locking = if self.flavor == DbSqlFlavor::Mysql {
            " LOCK IN SHARE MODE"
        } else {
            ""
        };
        Ok(Some(DbStatement::with_params(
            format!(
                "SELECT p.provider_id FROM bcs_providers p JOIN bcs_bots b ON b.provider_id = p.provider_id AND b.env = p.env WHERE b.env = ? AND b.bot_uuid IN ({placeholders}) AND COALESCE(b.is_deleted, 0) = 0 ORDER BY p.provider_id{locking}"
            ),
            params,
        )))
    }

    pub(crate) fn public_scope_assertion(
        &self,
        group: &Group,
        cas_step: Option<usize>,
    ) -> DbStatement {
        let mut ids = group
            .participants
            .iter()
            .map(|p| p.bot_uuid.clone())
            .collect::<Vec<_>>();
        ids.sort();
        ids.dedup();
        if ids.is_empty() {
            ids.push(String::new());
        }
        let placeholders = vec!["?"; ids.len()].join(",");
        let policy_denied = match self.flavor {
            DbSqlFlavor::Mysql => {
                "p.provider_id IS NULL OR JSON_TYPE(p.config) <> 'OBJECT' OR COALESCE(JSON_UNQUOTE(JSON_EXTRACT(p.config, '$.collaboration_scope')), 'standard') <> 'standard'"
            }
            DbSqlFlavor::Sqlite => {
                "p.provider_id IS NULL OR json_type(p.config) <> 'object' OR json_type(p.config, '$.collaboration_scope') = 'null' OR COALESCE(json_extract(p.config, '$.collaboration_scope'), 'standard') <> 'standard'"
            }
        };
        let mut params = vec![
            match cas_step {
                Some(step) => DbTransactionParam::query_result(step, 0, "group_id"),
                None => DbTransactionParam::value(group.id.as_str()),
            },
            DbTransactionParam::value(self.env.as_str()),
        ];
        params.extend(ids.into_iter().map(DbTransactionParam::value));
        DbStatement::with_transaction_params(
            format!(
                "SELECT ? AS group_id WHERE NOT EXISTS (SELECT 1 FROM bcs_bots b LEFT JOIN bcs_providers p ON p.provider_id = b.provider_id AND p.env = b.env WHERE b.env = ? AND b.bot_uuid IN ({placeholders}) AND b.provider_id IS NOT NULL AND COALESCE(b.is_deleted, 0) = 0 AND ({policy_denied}))"
            ),
            params,
        )
    }
}

pub(crate) fn public_scope_denied(error: &DbError, guard_step: Option<usize>) -> bool {
    match (error, guard_step) {
        (DbError::InvalidInput(message), Some(step)) => {
            message.contains(&format!("references missing row 0 from step {step}"))
        }
        _ => false,
    }
}
