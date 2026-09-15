use super::*;

impl PersistentBotRepo {
    /// Query bots from the configured database by name with pagination.
    /// Uses a CTE to resolve friendships and applies cooperatability filtering in SQL.
    ///
    /// Returns `(Vec<(RegisteredBot, bool)>, usize)` where bool = is_friend.
    pub(super) async fn list_bots_by_name_and_cooperatable_with_impl(
        &self,
        name: &str,
        bot_uuid: &str,
        cooperatable_only: bool,
        offset: usize,
        limit: usize,
    ) -> (Vec<(RegisteredBot, bool)>, usize) {
        let env = resolve_env();
        let like = if name.is_empty() {
            "%".to_string()
        } else {
            format!("%{}%", name.replace('%', r"\%").replace('_', r"\_"))
        };

        let coop_flag: i64 = if cooperatable_only { 1 } else { 0 };

        let visibility_filter = self.flavor.iif(
            "?",
            "b.visibility = 'public' OR f.bot_uuid IS NOT NULL",
            "b.visibility IN ('public', 'protected')",
        );

        let page_sql = format!(
            "WITH friend_uuids AS (\
                SELECT right_bot AS bot_uuid FROM bcs_friendships \
                WHERE left_bot = ? AND env = ? \
                UNION \
                SELECT left_bot AS bot_uuid FROM bcs_friendships \
                WHERE right_bot = ? AND env = ? \
            ) \
            SELECT b.bot_uuid, b.name, b.bot_info, b.visibility, b.env, b.created_by, \
                   b.actor_kind, b.status, \
                   f.bot_uuid IS NOT NULL as is_friend \
            FROM bcs_bots b \
            LEFT JOIN friend_uuids f ON b.bot_uuid = f.bot_uuid \
            WHERE b.env = ? \
              AND COALESCE(b.is_deleted, 0) = 0 \
              AND b.name LIKE ? \
              AND b.bot_uuid != ? \
              AND (b.actor_kind IS NULL OR b.actor_kind != 'human') \
              AND {} \
            ORDER BY b.id DESC \
            LIMIT ? OFFSET ?",
            visibility_filter
        );

        let page_rows = match self
            .db_query(
                &page_sql,
                vec![
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                    Value::from(env.as_str()),
                    Value::from(like.as_str()),
                    Value::from(bot_uuid),
                    Value::from(coop_flag),
                    Value::from(limit as i64),
                    Value::from(offset as i64),
                ],
            )
            .await
        {
            Ok(r) => r,
            Err(e) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, "list_bots_by_name_and_cooperatable_with_impl (page): failed");
                return (Vec::new(), 0);
            }
        };

        let count_sql = format!(
            "WITH friend_uuids AS (\
                SELECT right_bot AS bot_uuid FROM bcs_friendships \
                WHERE left_bot = ? AND env = ? \
                UNION \
                SELECT left_bot AS bot_uuid FROM bcs_friendships \
                WHERE right_bot = ? AND env = ? \
            ) \
            SELECT count(*) AS total \
            FROM bcs_bots b \
            LEFT JOIN friend_uuids f ON b.bot_uuid = f.bot_uuid \
            WHERE b.env = ? \
              AND COALESCE(b.is_deleted, 0) = 0 \
              AND b.name LIKE ? \
              AND b.bot_uuid != ? \
              AND (b.actor_kind IS NULL OR b.actor_kind != 'human') \
              AND {}",
            visibility_filter
        );

        let total: usize = match self
            .db_query(
                &count_sql,
                vec![
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                    Value::from(env.as_str()),
                    Value::from(like.as_str()),
                    Value::from(bot_uuid),
                    Value::from(coop_flag),
                ],
            )
            .await
        {
            Ok(rows) => rows
                .first()
                .and_then(|row| db_get_column_opt::<i64>(row, "total").ok().flatten())
                .map(|v| v as usize)
                .unwrap_or(0),
            Err(e) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, "list_bots_by_name_and_cooperatable_with_impl (count): failed");
                0
            }
        };

        let mut results = Vec::new();
        for row in &page_rows {
            let bot_uuid: String = match db_get_column_opt::<String>(row, "bot_uuid").ok().flatten()
            {
                Some(v) => v,
                None => continue,
            };
            let name: Option<String> = db_get_column_opt(row, "name").ok().flatten();
            let env: Option<String> = db_get_column_opt(row, "env").ok().flatten();
            let visibility: String = db_get_column_opt(row, "visibility")
                .ok()
                .flatten()
                .unwrap_or_else(|| "protected".to_string());
            let created_by: Option<String> = db_get_column_opt(row, "created_by").ok().flatten();
            let bot_info: BotInfo = db_get_column_opt::<String>(row, "bot_info")
                .ok()
                .flatten()
                .and_then(|s| serde_json::from_str(&s).ok())
                .unwrap_or_default();
            let is_friend: bool = db_get_column_opt::<i64>(row, "is_friend")
                .ok()
                .flatten()
                .map(|v| v != 0)
                .unwrap_or(false);

            // Human Actor V1: propagate persisted actor_kind / status so this
            // batch-by-ids path matches the single-row load (`load_bot_capabilities`
            // / `to_registered_bot`); otherwise downstream `O.5` / `P.3` / `F.3`
            // checks misclassify every actor as `Bot` / `Online`.
            let actor_kind_str: String = db_get_column_opt(row, "actor_kind")
                .ok()
                .flatten()
                .filter(|v: &String| !v.is_empty())
                .unwrap_or_else(|| "bot".to_string());
            let actor_kind = match actor_kind_str.as_str() {
                "human" => bcs_service_api::ActorKind::Human,
                _ => bcs_service_api::ActorKind::Bot,
            };
            let status_str: String = db_get_column_opt(row, "status")
                .ok()
                .flatten()
                .filter(|v: &String| !v.is_empty())
                .unwrap_or_else(|| "online".to_string());
            let status = match status_str.as_str() {
                "hidden" => bcs_service_api::ActorStatus::Hidden,
                _ => bcs_service_api::ActorStatus::Online,
            };

            let bot = RegisteredBot {
                bot_uuid,
                capabilities: BotCapabilities {
                    name,
                    summary: bot_info.summary,
                    domains: bot_info.domains,
                    skills: bot_info.skills,
                    scopes: bot_info.scopes,
                    binding_channels: bot_info.binding_channels,
                    hidden: bot_info.hidden,
                    visibility,
                    // SECURITY: 敏感字段置空，防止通过常规接口泄露
                    agent_code: None,
                    agent_token: None,
                },
                env,
                created_by,
                actor_kind,
                status,
            };
            results.push((bot, is_friend));
        }

        (results, total)
    }
}
