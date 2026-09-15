use super::*;

impl MemoryBotRepo {
    /// Ensure a Human Actor entry exists for the given staff_no — Task O.3.
    ///
    /// In-memory implementation: idempotent insert into the in-process registry.
    /// `name` is preserved on subsequent calls (Requirement 3.1#4).
    pub(super) async fn repo_ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<bcs_service_api::EnsureHumanResult> {
        let bot_uuid = format!("human_{}", staff_no);

        let default_summary = "写点什么介绍自己";

        let mut bots = self.bots.write().await;
        if let Some(existing) = bots.get_mut(&bot_uuid) {
            // Backfill summary if it is missing or empty.
            let needs_summary = existing
                .capabilities
                .summary
                .as_deref()
                .map_or(true, |s| s.is_empty());
            if needs_summary {
                existing.capabilities.summary = Some(default_summary.to_string());
                debug!(
                    bot_uuid = %bot_uuid,
                    "ensure_human_actor (in-memory): backfilled empty summary"
                );
            } else {
                debug!(
                    bot_uuid = %bot_uuid,
                    "ensure_human_actor (in-memory): already exists, preserving existing fields"
                );
            }
            return Ok(bcs_service_api::EnsureHumanResult { created: false });
        }

        let session_token = uuid::Uuid::new_v4().to_string();
        let caps = BotCapabilities {
            name: Some(nick_name.to_string()),
            summary: Some(default_summary.to_string()),
            visibility: "protected".to_string(),
            ..Default::default()
        };

        bots.insert(
            bot_uuid.clone(),
            RegisteredBotInner {
                bot_id: bot_uuid.clone(),
                last_heartbeat: Instant::now(),
                capabilities: caps,
                ws_connection: None,
                session_token: Some(session_token),
                env: Some(resolve_env()),
                status: bcs_service_api::ActorStatus::Online,
                actor_kind: bcs_service_api::ActorKind::Human,
                created_by: Some(staff_no.to_string()),
                protocol_version: 1,
                user_visibility: UserVisibility::default(),
                friend_ext: serde_json::Map::new(),
                friend_check_in_strategy: FriendCheckInStrategy::default(),
            },
        );

        let now = unix_millis();
        self.control_plane_audit
            .write()
            .await
            .insert(bot_uuid.clone(), (now, now));

        info!(
            bot_uuid = %bot_uuid,
            staff_no = %staff_no,
            nick_name = %nick_name,
            "ensure_human_actor (in-memory): row inserted"
        );
        Ok(bcs_service_api::EnsureHumanResult { created: true })
    }

    pub(super) async fn repo_list_legacy_bots_for_owner(
        &self,
        staff_no: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        let bots = self.bots.read().await;
        let results: Vec<RegisteredBot> = bots
            .values()
            .filter(|b| {
                // Must be a Bot (not Human) and match env
                if b.actor_kind != bcs_service_api::ActorKind::Bot {
                    return false;
                }
                if b.env.as_deref() != Some(env) {
                    return false;
                }
                // Rule (a): created_by matches
                if b.created_by.as_deref() == Some(staff_no) {
                    return true;
                }
                // Rule (b): created_by is None and namespace is whitelisted
                if b.created_by.is_none() && is_legacy_namespace(&b.bot_id, staff_no) {
                    return true;
                }
                false
            })
            .map(|b| {
                // 清除敏感字段，防止通过常规接口泄露
                let mut capabilities = b.capabilities.clone();
                capabilities.agent_code = None;
                capabilities.agent_token = None;
                RegisteredBot {
                    bot_uuid: b.bot_id.clone(),
                    capabilities,
                    env: b.env.clone(),
                    created_by: b.created_by.clone(),
                    actor_kind: b.actor_kind.clone(),
                    status: b.status.clone(),
                }
            })
            .collect();

        Ok(results)
    }
}
