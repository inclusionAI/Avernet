use super::*;

impl MemoryBotRepo {
    pub(super) async fn repo_get(&self, bot_id: &str) -> Option<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.get(bot_id)
            .filter(|b| !b.is_expired())
            .map(|b| b.to_registered_bot())
    }

    pub(super) async fn repo_get_agent_credentials(
        &self,
        bot_id: &str,
    ) -> Option<bcs_service_api::AgentCredentials> {
        let bots = self.bots.read().await;
        bots.get(bot_id)
            .filter(|b| !b.is_expired())
            .map(|b| bcs_service_api::AgentCredentials {
                agent_code: b.capabilities.agent_code.clone(),
                agent_token: b.capabilities.agent_token.clone(),
            })
    }

    pub(super) async fn repo_add_bot_info(&self, bot_id: &str, key: &str, value: String) {
        let bots = self.bots.read().await;
        if !bots.contains_key(bot_id) {
            return;
        }
        drop(bots);

        if key == "agent_token" {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                bot.capabilities.agent_token = Some(value);
            }
            return;
        }

        if key == "client_kind" {
            self.bot_info_overrides
                .write()
                .await
                .insert((bot_id.to_string(), key.to_string()), value);
        }
    }

    pub(super) async fn repo_get_bot_info(&self, bot_id: &str, key: &str) -> Option<String> {
        if key == "agent_token" {
            let bots = self.bots.read().await;
            return bots
                .get(bot_id)
                .and_then(|bot| bot.capabilities.agent_token.clone());
        }

        self.bot_info_overrides
            .read()
            .await
            .get(&(bot_id.to_string(), key.to_string()))
            .cloned()
    }

    pub(super) async fn repo_list_active(&self) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .map(|b| b.to_registered_bot())
            .collect()
    }

    pub(super) async fn repo_list_all_bots(&self) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values().map(|b| b.to_registered_bot()).collect()
    }

    pub(super) async fn repo_list_bots_by_name_and_cooperatable_with(
        &self,
        name: &str,
        bot_uuid: &str,
        cooperatable_only: bool,
        friend_uuids: &HashSet<String>,
        offset: usize,
        limit: usize,
    ) -> (Vec<(RegisteredBot, bool)>, usize) {
        let bots = self.bots.read().await;
        let name_lower = name.to_lowercase();

        let filtered: Vec<(RegisteredBot, bool)> = bots
            .values()
            .map(|b| b.to_registered_bot())
            .filter(|b| b.bot_uuid != bot_uuid)
            .filter(|b| b.actor_kind != bcs_service_api::ActorKind::Human)
            .filter(|b| {
                if !name.is_empty() {
                    b.capabilities
                        .name
                        .as_ref()
                        .map(|n| n.to_lowercase().contains(&name_lower))
                        .unwrap_or(false)
                } else {
                    true
                }
            })
            .filter_map(|b| {
                let is_friend = friend_uuids.contains(&b.bot_uuid);
                let vis = b.capabilities.visibility.as_str();
                if cooperatable_only {
                    if vis == "public" || is_friend {
                        Some((b, is_friend))
                    } else {
                        None
                    }
                } else {
                    if vis == "public" || vis == "protected" {
                        Some((b, is_friend))
                    } else {
                        None
                    }
                }
            })
            .collect();

        let total = filtered.len();
        let page: Vec<(RegisteredBot, bool)> =
            filtered.into_iter().skip(offset).take(limit).collect();

        (page, total)
    }

    pub(super) async fn repo_list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        let current_env = resolve_env();
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| {
                b.created_by.as_deref() == Some(created_by)
                    && b.env.as_deref() == Some(current_env.as_str())
            })
            .map(|b| b.to_registered_bot())
            .collect()
    }

    pub(super) async fn repo_get_by_ids(&self, bot_ids: &[String]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        let mut seen = HashSet::new();
        bot_ids
            .iter()
            .filter(|id| seen.insert(id.as_str()))
            .filter_map(|id| bots.get(id.as_str()))
            .filter(|b| !b.is_expired())
            .map(|b| b.to_registered_bot())
            .collect()
    }

    pub(super) async fn repo_discover(&self, query: &str) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        let query_lower = query.to_lowercase();

        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| {
                // Check name
                b.capabilities.name.as_ref()
                    .map(|n| n.to_lowercase().contains(&query_lower))
                    .unwrap_or(false)
                // Check summary
                || b.capabilities.summary.as_ref()
                    .map(|s| s.to_lowercase().contains(&query_lower))
                    .unwrap_or(false)
                // Check domains
                || b.capabilities.domains.iter()
                    .any(|d| d.to_lowercase().contains(&query_lower))
                // Check skills
                || b.capabilities.skills.iter()
                    .any(|s| s.name.to_lowercase().contains(&query_lower))
                // Check scopes
                || b.capabilities.scopes.iter()
                    .any(|s| s.to_lowercase().contains(&query_lower))
                // Check bot_id
                || b.bot_id.to_lowercase().contains(&query_lower)
            })
            .map(|b| b.to_registered_bot())
            .collect()
    }

    pub(super) async fn repo_find_by_skills(&self, skills: &[&str]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| skills.iter().all(|s| b.has_skill(s)))
            .map(|b| b.to_registered_bot())
            .collect()
    }

    pub(super) async fn repo_find_by_domains(&self, domains: &[&str]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| domains.iter().all(|d| b.has_domain(d)))
            .map(|b| b.to_registered_bot())
            .collect()
    }

    pub(super) async fn repo_find_by_scopes(&self, scopes: &[&str]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| scopes.iter().all(|s| b.has_scope(s)))
            .map(|b| b.to_registered_bot())
            .collect()
    }
}
