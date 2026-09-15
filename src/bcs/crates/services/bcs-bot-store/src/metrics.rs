use super::*;

#[async_trait]
impl BotMetricsSnapshotPort for PersistentBotRepo {
    async fn bot_counts(&self) -> ServiceResult<Vec<BotMetricCount>> {
        let env = resolve_env();
        let sql = "SELECT actor_kind, status, visibility, COUNT(*) AS bot_count \
                   FROM ( \
                       SELECT \
                           CASE WHEN actor_kind = 'human' THEN 'human' ELSE 'bot' END AS actor_kind, \
                           CASE WHEN status = 'hidden' THEN 'hidden' ELSE 'online' END AS status, \
                           CASE \
                               WHEN visibility IS NULL OR TRIM(visibility) = '' THEN 'private' \
                               WHEN visibility IN ('public', 'protected', 'private') THEN visibility \
                               ELSE 'other' \
                           END AS visibility \
                       FROM bcs_bots \
                       WHERE env = ? \
                         AND COALESCE(is_deleted, 0) = 0 \
                   ) metric_bots \
                   GROUP BY actor_kind, status, visibility";
        let rows = self
            .db_query(sql, vec![Value::from(env.as_str())])
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, env = %env, error = %e, "bot metrics snapshot query failed");
                ServiceError::InternalError(format!("bot metrics snapshot query failed: {}", e))
            })?;

        let mut counts = Vec::with_capacity(rows.len());
        for row in rows {
            let actor_kind_raw: String = db_get_column(&row, "actor_kind").map_err(|e| {
                ServiceError::InternalError(format!(
                    "bot metrics actor_kind conversion failed: {}",
                    e
                ))
            })?;
            let status_raw: String = db_get_column(&row, "status").map_err(|e| {
                ServiceError::InternalError(format!("bot metrics status conversion failed: {}", e))
            })?;
            let visibility: String = db_get_column(&row, "visibility").map_err(|e| {
                ServiceError::InternalError(format!(
                    "bot metrics visibility conversion failed: {}",
                    e
                ))
            })?;
            let bot_count: i64 = db_get_column(&row, "bot_count").map_err(|e| {
                ServiceError::InternalError(format!("bot metrics count conversion failed: {}", e))
            })?;
            let count = u64::try_from(bot_count).map_err(|e| {
                ServiceError::InternalError(format!("bot metrics count is invalid: {}", e))
            })?;
            if count == 0 {
                continue;
            }

            counts.push(BotMetricCount {
                actor_kind: sql_metric_actor_kind(&actor_kind_raw),
                status: sql_metric_actor_status(&status_raw),
                visibility: Some(visibility),
                count,
            });
        }
        Ok(counts)
    }
}
