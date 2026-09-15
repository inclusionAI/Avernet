use super::*;

#[async_trait]
impl BotControlPlaneRepoPort for MemoryBotRepo {
    async fn get_control_plane(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return Ok(None);
        }
        let audit = self
            .control_plane_audit
            .read()
            .await
            .get(bot_id)
            .copied()
            .unwrap_or((0, 0));
        let bots = self.bots.read().await;
        let Some(bot) = bots.get(bot_id) else {
            return Ok(None);
        };
        let record_env = bot.env.clone().unwrap_or_else(resolve_env);
        if record_env != env {
            return Ok(None);
        }
        let Some(name) = bot
            .capabilities
            .name
            .as_deref()
            .map(str::trim)
            .filter(|name| !name.is_empty())
        else {
            return Ok(None);
        };
        let (task_claim_mode, task_dream_mode) = self
            .task_modes
            .read()
            .await
            .get(bot_id)
            .copied()
            .unwrap_or((false, false));
        Ok(Some(BotControlPlaneRecord {
            bot_id: bot.bot_id.clone(),
            kind: bot.actor_kind,
            name: name.to_string(),
            visibility: if bot.capabilities.visibility.is_empty() {
                "protected".to_string()
            } else {
                bot.capabilities.visibility.clone()
            },
            status: bot.status,
            env: record_env,
            created_by: bot.created_by.clone(),
            descriptor: BotControlPlaneDescriptor {
                summary: bot.capabilities.summary.clone().unwrap_or_default(),
                domains: bot.capabilities.domains.clone(),
                skills: bot.capabilities.skills.clone(),
                scopes: bot.capabilities.scopes.clone(),
            },
            agent_code: bot.capabilities.agent_code.clone(),
            task_claim_mode,
            task_dream_mode,
            created_at: audit.0,
            updated_at: audit.1,
            user_visibility: bot.user_visibility,
            friend_ext: bot.friend_ext.clone(),
            friend_check_in_strategy: bot.friend_check_in_strategy,
        }))
    }

    async fn list_control_plane_candidates(
        &self,
        query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotCandidateReadRecord>, u64)> {
        let ids = self.bots.read().await.keys().cloned().collect::<Vec<_>>();
        let name = query
            .name
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_lowercase);
        let mut records = Vec::new();
        for bot_id in ids {
            let Some(bot) = self.get_control_plane(&bot_id, &query.env).await? else {
                continue;
            };
            if bot.bot_id == query.acting_bot_id || bot.kind != bcs_service_api::ActorKind::Bot {
                continue;
            }
            if name
                .as_ref()
                .is_some_and(|name| !bot.name.to_lowercase().contains(name))
            {
                continue;
            }
            let is_friend = query.friend_ids.contains(&bot.bot_id);
            let visible = match query.visibility {
                BotCandidateVisibility::Discovery => {
                    matches!(bot.visibility.as_str(), "public" | "protected")
                }
                BotCandidateVisibility::Collaboration => bot.visibility == "public" || is_friend,
            };
            if visible {
                records.push(BotCandidateReadRecord { bot, is_friend });
            }
        }
        records.sort_by(|left, right| {
            right
                .bot
                .created_at
                .cmp(&left.bot.created_at)
                .then_with(|| left.bot.bot_id.cmp(&right.bot.bot_id))
        });
        let total = records.len() as u64;
        let page = records
            .into_iter()
            .skip(query.offset as usize)
            .take(query.limit as usize)
            .collect();
        Ok((page, total))
    }

    async fn search_control_plane_candidates(
        &self,
        query: BotSearchCandidateQuery,
    ) -> ServiceResult<(Vec<BotCandidateReadRecord>, u64)> {
        let search_text = query
            .q
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .or_else(|| {
                query
                    .name
                    .as_deref()
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
            })
            .map(str::to_lowercase);
        let bot_uuid_filter = match query.bot_uuids.as_ref() {
            Some(values) if values.is_empty() => return Ok((Vec::new(), 0)),
            Some(values) => Some(values.iter().map(String::as_str).collect::<HashSet<_>>()),
            None => None,
        };
        let visibility_filter = match query.visibility_filter.as_ref() {
            Some(values) if values.is_empty() => return Ok((Vec::new(), 0)),
            Some(values) => Some(
                values
                    .iter()
                    .map(|value| value.trim().to_lowercase())
                    .collect::<Vec<_>>(),
            ),
            None => None,
        };
        let user_visibility_filter = match query.user_visibility.as_ref() {
            Some(values) if values.is_empty() => return Ok((Vec::new(), 0)),
            Some(values) => Some(
                values
                    .iter()
                    .map(|value| value.trim().to_lowercase())
                    .collect::<Vec<_>>(),
            ),
            None => None,
        };
        let mut records = Vec::new();
        for bot_id in self.bots.read().await.keys().cloned().collect::<Vec<_>>() {
            let Some(bot) = self.get_control_plane(&bot_id, &query.env).await? else {
                continue;
            };
            if bot.bot_id == query.acting_bot_id || bot.kind != bcs_service_api::ActorKind::Bot {
                continue;
            }
            if bot_uuid_filter
                .as_ref()
                .is_some_and(|values| !values.contains(bot.bot_id.as_str()))
            {
                continue;
            }
            if search_text.as_ref().is_some_and(|needle| {
                !bot.name.to_lowercase().contains(needle)
                    && !bot.descriptor.summary.to_lowercase().contains(needle)
                    && !bot.bot_id.to_lowercase().contains(needle)
            }) {
                continue;
            }
            if let Some(values) = visibility_filter.as_ref() {
                if !values.iter().any(|value| value == &bot.visibility) {
                    continue;
                }
            } else {
                match query.visibility {
                    BotCandidateVisibility::Discovery => {
                        if !matches!(bot.visibility.as_str(), "public" | "protected") {
                            continue;
                        }
                    }
                    BotCandidateVisibility::Collaboration => {
                        let is_friend = query.friend_ids.contains(&bot.bot_id);
                        if bot.visibility != "public" && !is_friend {
                            continue;
                        }
                    }
                }
            }
            if let Some(values) = user_visibility_filter.as_ref() {
                let user_visibility = match bot.user_visibility {
                    UserVisibility::Public => "public",
                    UserVisibility::Protected => "protected",
                    UserVisibility::Private => "private",
                };
                if !values.iter().any(|value| value == user_visibility) {
                    continue;
                }
            }
            let is_friend = query.friend_ids.contains(&bot.bot_id);
            match query.friendship.unwrap_or_default() {
                BotSearchFriendshipFilter::All => {}
                BotSearchFriendshipFilter::Friends => {
                    if !is_friend {
                        continue;
                    }
                }
                BotSearchFriendshipFilter::NonFriends => {
                    if is_friend {
                        continue;
                    }
                }
            }
            if let Some(want_tc) = query.tc_bot {
                let is_tc = bot
                    .created_by
                    .as_deref()
                    .is_some_and(|owner| bot.bot_id.rsplit_once(':').is_some_and(|(_, suffix)| suffix == owner));
                if is_tc != want_tc {
                    continue;
                }
            }
            records.push(BotCandidateReadRecord { bot, is_friend });
        }
        records.sort_by(|left, right| {
            right
                .bot
                .created_at
                .cmp(&left.bot.created_at)
                .then_with(|| left.bot.bot_id.cmp(&right.bot.bot_id))
        });
        let total = records.len() as u64;
        let page = records
            .into_iter()
            .skip(query.offset as usize)
            .take(query.limit as usize)
            .collect();
        Ok((page, total))
    }

    async fn list_control_plane_by_creator(

        &self,
        query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        let ids = self.bots.read().await.keys().cloned().collect::<Vec<_>>();
        let name = query
            .name
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_lowercase);
        let mut records = Vec::new();
        for bot_id in ids {
            let Some(bot) = self.get_control_plane(&bot_id, &query.env).await? else {
                continue;
            };
            if bot.created_by.as_deref() != Some(query.created_by.as_str())
                || query.kind.is_some_and(|kind| bot.kind != kind)
                || query.status.is_some_and(|status| bot.status != status)
                || name
                    .as_ref()
                    .is_some_and(|name| !bot.name.to_lowercase().contains(name))
            {
                continue;
            }
            records.push(bot);
        }
        records.sort_by(|left, right| {
            right
                .created_at
                .cmp(&left.created_at)
                .then_with(|| left.bot_id.cmp(&right.bot_id))
        });
        Ok(records)
    }

    async fn list_control_plane_by_task_modes(
        &self,
        query: BotTaskModesQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        let ids = self.bots.read().await.keys().cloned().collect::<Vec<_>>();
        let mut records = Vec::new();
        for bot_id in ids {
            let Some(bot) = self.get_control_plane(&bot_id, &query.env).await? else {
                continue;
            };
            if bot.kind != bcs_service_api::ActorKind::Bot {
                continue;
            }
            let passes = match (query.task_claim_mode, query.task_dream_mode, query.match_mode) {
                (Some(claim), Some(dream), TaskModeMatch::All) => {
                    bot.task_claim_mode == claim && bot.task_dream_mode == dream
                }
                (Some(claim), Some(dream), TaskModeMatch::Any) => {
                    bot.task_claim_mode == claim || bot.task_dream_mode == dream
                }
                (Some(claim), None, _) => bot.task_claim_mode == claim,
                (None, Some(dream), _) => bot.task_dream_mode == dream,
                (None, None, _) => true,
            };
            if passes
                && query
                    .visibility
                    .as_deref()
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .is_none_or(|visibility| bot.visibility == visibility)
                && query.status.is_none_or(|status| bot.status == status)
                && query
                    .user_visibility
                    .is_none_or(|visibility| bot.user_visibility == visibility)
            {
                records.push(bot);
            }
        }
        records.sort_by(|left, right| {
            right
                .created_at
                .cmp(&left.created_at)
                .then_with(|| left.bot_id.cmp(&right.bot_id))
        });
        Ok(records)
    }

    async fn patch_control_plane(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        let _identity = self.identity_locks.lock(bot_id).await;
        let _patch_guard = self.control_plane_patch_lock.lock().await;
        if patch.user_visibility.is_some()
            || patch.friend_ext.is_some()
            || patch.friend_check_in_strategy.is_some()
        {
            debug!(
                bot_id = %bot_id,
                has_user_visibility = patch.user_visibility.is_some(),
                has_friend_ext = patch.friend_ext.is_some(),
                has_friend_check_in_strategy = patch.friend_check_in_strategy.is_some(),
                "Patching internal Bot attributes in memory"
            );
        }
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return Ok(None);
        }
        let (
            mut capabilities,
            token,
            created_by,
            record_env,
            mut user_visibility,
            mut friend_ext,
            mut friend_check_in_strategy,
        ) = {
            let bots = self.bots.read().await;
            let Some(bot) = bots.get(bot_id) else {
                return Ok(None);
            };
            let record_env = bot.env.clone().unwrap_or_else(resolve_env);
            if record_env != env {
                return Ok(None);
            }
            (
                bot.capabilities.clone(),
                bot.session_token.clone(),
                bot.created_by.clone(),
                record_env,
                bot.user_visibility,
                bot.friend_ext.clone(),
                bot.friend_check_in_strategy,
            )
        };

        if let Some(name) = patch.name.as_ref() {
            capabilities.name = Some(name.clone());
        }
        if let Some(visibility) = patch.visibility.as_ref() {
            capabilities.visibility = visibility.clone();
        }
        if let Some(descriptor) = patch.descriptor.as_ref() {
            if let Some(summary) = descriptor.summary.as_ref() {
                capabilities.summary = Some(summary.clone());
            }
            if let Some(domains) = descriptor.domains.as_ref() {
                capabilities.domains = domains.clone();
            }
            if let Some(skills) = descriptor.skills.as_ref() {
                capabilities.skills = skills.clone();
            }
            if let Some(scopes) = descriptor.scopes.as_ref() {
                capabilities.scopes = scopes.clone();
            }
        }
        if let Some(value) = patch.user_visibility {
            user_visibility = value;
        }
        if let Some(value) = patch.friend_ext.as_ref() {
            friend_ext = value.clone();
        }
        if let Some(value) = patch.friend_check_in_strategy {
            friend_check_in_strategy = value;
        }

        let now = unix_millis();
        let created_at = self
            .control_plane_audit
            .read()
            .await
            .get(bot_id)
            .map(|audit| audit.0)
            .unwrap_or(now);
        let persisted = PersistedCapabilities {
            bot_id: bot_id.to_string(),
            name: capabilities.name.clone(),
            summary: capabilities.summary.clone(),
            domains: capabilities.domains.clone(),
            skills: capabilities.skills.clone(),
            scopes: capabilities.scopes.clone(),
            binding_channels: capabilities.binding_channels.clone(),
            token,
            registered_at: created_at,
            hidden: false,
            created_by: created_by.clone(),
            visibility: Some(capabilities.visibility.clone()),
            agent_code: capabilities.agent_code.clone(),
            agent_token: capabilities.agent_token.clone(),
            user_visibility,
            friend_ext: friend_ext.clone(),
            friend_check_in_strategy,
        };
        let path = self.bot_info_path(bot_id);
        let directory = path.parent().ok_or_else(|| {
            ServiceError::InternalError(format!("Invalid path for bot: {bot_id}"))
        })?;
        fs::create_dir_all(directory).await?;
        fs::write(&path, serde_json::to_string_pretty(&persisted)?).await?;

        {
            let mut bots = self.bots.write().await;
            let Some(bot) = bots.get_mut(bot_id) else {
                return Ok(None);
            };
            bot.capabilities = capabilities;
            bot.created_by = created_by;
            bot.env = Some(record_env);
            bot.user_visibility = user_visibility;
            bot.friend_ext = friend_ext;
            bot.friend_check_in_strategy = friend_check_in_strategy;
            if let Some(status) = patch.status {
                bot.status = status;
            }
        }
        {
            let mut task_modes = self.task_modes.write().await;
            let entry = task_modes.entry(bot_id.to_string()).or_insert((false, false));
            if let Some(claim) = patch.task_claim_mode {
                entry.0 = claim;
            }
            if let Some(dream) = patch.task_dream_mode {
                entry.1 = dream;
            }
        }
        self.control_plane_audit
            .write()
            .await
            .insert(bot_id.to_string(), (created_at, now));
        self.get_control_plane(bot_id, env).await
    }
}
