//! Bot queries and discovery orchestration.
use super::*;

#[async_trait]
impl BotQueryService for Bot {
    async fn list_bots(&self, command: BotListCommand) -> Result<BotListResult, BotUseCaseError> {
        let filtered: Vec<RegisteredBot> = self
            .registry
            .list_active()
            .await
            .into_iter()
            .filter(|bot| is_in_list_bots_scope(bot, command.onboarded))
            .collect();
        let total = filtered.len() as u64;
        let offset = to_usize(command.offset);
        let limit = to_usize(command.limit);
        let bots = filtered
            .into_iter()
            .skip(offset)
            .take(limit)
            .map(bot_to_list_entry)
            .collect();

        Ok(BotListResult {
            bots,
            offset: command.offset,
            limit: command.limit,
            total,
        })
    }

    async fn get_bot(&self, command: BotDetailCommand) -> Result<BotDetailResult, BotUseCaseError> {
        let bot = self
            .registry
            .get(&command.bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(command.bot_id.clone()))?;
        authorize_visibility_read(
            command.caller_actor_id.as_deref(),
            &bot,
            self.bot_authority.as_deref(),
        )
        .await?;

        let mut detail = self.bot_to_detail(bot).await;
        detail.agent_card = self.cards_for_ids(&[detail.bot_uuid.clone()]).await?.remove(&detail.bot_uuid);
        Ok(detail)
    }

    async fn list_bots_by_creator(
        &self,
        staff_no: &str,
    ) -> Result<Vec<BotListEntry>, BotUseCaseError> {
        let bots: Vec<BotListEntry> = self
            .registry
            .list_bots_by_creator(staff_no)
            .await
            .into_iter()
            .map(bot_to_list_entry)
            .collect();
        Ok(bots)
    }

    async fn get_visibility(
        &self,
        command: BotVisibilityQueryCommand,
    ) -> Result<BotVisibilityQueryResult, BotUseCaseError> {
        let bot = self
            .registry
            .get(&command.bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(command.bot_id.clone()))?;

        authorize_visibility_read(
            command.caller_actor_id.as_deref(),
            &bot,
            self.bot_authority.as_deref(),
        )
        .await?;

        Ok(BotVisibilityQueryResult {
            bot_uuid: command.bot_id,
            visibility: normalized_visibility(&bot.capabilities.visibility).to_string(),
        })
    }

    async fn list_bots_paged(
        &self,
        command: BotPagedListCommand,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        let filtered: Vec<RegisteredBot> = self
            .registry
            .list_active()
            .await
            .into_iter()
            .filter(|bot| match &command.user_id {
                Some(user_id) => bot
                    .bot_uuid
                    .rsplit_once(':')
                    .map(|(_, suffix)| suffix == user_id.as_str())
                    .unwrap_or(false),
                None => true,
            })
            .collect();
        self.bot_page_from_registered(filtered, command.offset, command.limit)
            .await
    }

    async fn list_my_bots(
        &self,
        command: bcs_service_api::MyBotsCommand,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        // mine / legacy `/bots/my` (spec §8
        // 首行): the owner/manager UNION from the
        // control plane's controllable read —
        // the literal `list_bots_by_creator` is
        // history and no longer feeds this
        // entry (bot-authority plan Task 6.3,
        // ruling T6c-1). Ruling T6d-3: the
        // authenticated Human's SELF entry keeps
        // its self semantics on this page (the
        // trusted onboarded Human actor row is
        // appended below).
        if let Some(control_plane) = self.control_plane.as_ref() {
            let candidates = control_plane
                .list_controllable(
                    bcs_service_api::ControllableCandidateQuery {
                        user_id: command.staff_no.clone(),
                        env: bcs_config::resolve_env_str(),
                        kind: None,
                        name: None,
                        status: None,
                        include_legacy_creator: self
                            .legacy_created_by_fallback,
                    },
                )
                .await
                .map_err(BotUseCaseError::Service)?;
            // Per-item relation sampling (spec §8
            // mine row: 逐项
            // `access_relation=owner|manager`; the
            // Human self entry keeps `self`).
            let mut relations: std::collections::HashMap<
                String,
                String,
            > = std::collections::HashMap::new();
            // Hydrate the Bot-kind candidates with ONE strict
            // batch read (legacy-mine ruling): a store failure
            // is an Err to the caller — never a page quietly
            // missing entries.
            let mut hydrate_ids: Vec<String> = Vec::new();
            for candidate in &candidates {
                if candidate.kind != ActorKind::Bot {
                    continue;
                }
                relations.insert(
                    candidate.id.clone(),
                    match candidate.access_relation {
                        bcs_service_api::types::bot_authority::BotAccessRelation::Owner => {
                            "owner"
                        }
                        bcs_service_api::types::bot_authority::BotAccessRelation::Manager => {
                            "manager"
                        }
                    }
                    .to_string(),
                );
                hydrate_ids.push(candidate.id.clone());
            }
            let mut bots = self
                .registry
                .try_get_many(&hydrate_ids)
                .await
                .map_err(BotUseCaseError::Service)?;
            // Human SELF entry (spec §8 first row):
            // the trusted onboarded Human actor row
            // lives in the SAME registry; the legacy
            // page kept it alongside the union. It is
            // self-visible by construction — the page
            // only lists this caller's union and this
            // caller's own row; no Human→Human edges
            // exist.
            let human_actor_id =
                format!("human_{}", command.staff_no);
            if !bots
                .iter()
                .any(|bot| bot.bot_uuid == human_actor_id)
            {
                if let Some(human) =
                    self.registry.get(&human_actor_id).await
                {
                    if human.actor_kind == ActorKind::Human
                        && human
                            .created_by
                            .as_deref()
                            == Some(command.staff_no.as_str())
                    {
                        bots.push(human);
                        // Spec §8 mine row: "Human
                        // 自身条目保持 self 语义".
                        relations.insert(
                            human_actor_id.clone(),
                            "self".to_string(),
                        );
                    }
                }
            }
            return self
                .my_bot_page_from_registered(
                    bots,
                    command.offset,
                    command.limit,
                    command.active_only,
                    relations,
                )
                .await;
        }
        // Legacy fallback for unwired test
        // harnesses: without the control plane
        // the union read is impossible, so the
        // historical behavior stands (no
        // sampled relations exist to expose).
        let bots = self.registry.list_bots_by_creator(&command.staff_no).await;
        self.my_bot_page_from_registered(
            bots,
            command.offset,
            command.limit,
            command.active_only,
            std::collections::HashMap::new(),
        )
            .await
    }

    async fn query_bots_by_ids(
        &self,
        command: BotQueryByIdsCommand,
    ) -> Result<BotQueryByIdsResult, BotUseCaseError> {
        let started = std::time::Instant::now();
        let input_count = command.bot_ids.len();
        let unique_count = command.bot_ids.iter().collect::<std::collections::HashSet<_>>().len();
        let bots = bcs_observability::observe_value("bots.query.batch_load", self
            .registry
            .get_by_ids(&command.bot_ids)).await
            .into_iter()
            .filter(|bot| bot.capabilities.name.is_some())
            .collect::<Vec<_>>();
        let load_ms = started.elapsed().as_secs_f64() * 1000.0;
        let enrich_started = std::time::Instant::now();
        let mut entries = Vec::with_capacity(bots.len());
        for bot in bots {
            entries.push(bcs_observability::observe_value("bots.query.status_enrich", self.bot_to_query_entry(bot)).await);
        }
        self.attach_query_cards(&mut entries).await?;
        tracing::info!(target: "bcs_observation", request_id = %bcs_observability::current_request_id(), input_count, unique_count, returned_count = entries.len(), load_ms, enrich_ms = enrich_started.elapsed().as_secs_f64() * 1000.0, duration_ms = started.elapsed().as_secs_f64() * 1000.0, "bots.query.summary");
        Ok(BotQueryByIdsResult { bots: entries })
    }

    async fn search_bots(
        &self,
        command: SearchBotsCommand,
    ) -> Result<BotSearchResult, BotUseCaseError> {
        let control_plane = self.control_plane.as_ref().ok_or_else(|| {
            BotUseCaseError::Service(ServiceError::InvalidOperation {
                message: "Bot is missing BotControlPlaneCoreService wiring; search_bots requires .with_control_plane(...)".to_string(),
                request_id: None,
            })
        })?;
        self.search_bots_via_control_plane(control_plane.as_ref(), command).await
    }
}

impl Bot {
    async fn search_bots_via_control_plane(
        &self,
        control_plane: &dyn BotControlPlaneCoreService,
        command: SearchBotsCommand,
    ) -> Result<BotSearchResult, BotUseCaseError> {
        let viewer_actor_id = command.viewer_actor_id.as_deref();
        let friend_ids = if let Some(viewer_actor_id) = viewer_actor_id {
            if let Some(edge_grants) = self.edge_grants.as_ref() {
                edge_grants
                    .list_friends(viewer_actor_id, &bcs_config::resolve_env_str())
                    .await
                    .into_iter()
                    .collect::<HashSet<_>>()
            } else {
                self.friend
                    .list_friends(viewer_actor_id)
                    .await
                    .into_iter()
                    .collect::<HashSet<_>>()
            }
        } else {
            HashSet::new()
        };
        let friendship = command.friendship.unwrap_or(BotSearchFriendshipFilter::All);
        if !matches!(friendship, BotSearchFriendshipFilter::All) && viewer_actor_id.is_none() {
            return Err(ServiceError::InvalidOperation {
                message: "friendship filter requires viewer_actor_id".to_string(),
                request_id: None,
            }
            .into());
        }

        let query = BotSearchCandidateQuery {
            acting_bot_id: command.requester_actor_id.clone().unwrap_or_default(),
            env: bcs_config::resolve_env_str(),
            visibility: bcs_service_api::BotCandidateVisibility::Discovery,
            friend_ids,
            bot_uuids: command.bot_uuids.clone(),
            name: command.q.clone(),
            q: command.q.clone(),
            visibility_filter: command.visibility.clone(),
            user_visibility: command.user_visibility.clone(),
            status: command.status,
            friendship: Some(friendship),
            tc_bot: command.tc_bot,
            offset: command.offset,
            limit: command.limit,
        };
        let (candidates, total) = control_plane.search_candidates(query).await?;
        let candidate_bot_ids = candidates
            .iter()
            .map(|candidate| candidate.bot.record.bot_id.clone())
            .collect::<Vec<_>>();
        let active_bot_ids = self
            .registry
            .list_runtime_active_bot_ids(&candidate_bot_ids)
            .await
            .into_iter()
            .collect::<HashSet<_>>();

        let mut items = Vec::with_capacity(candidates.len());
        for candidate in candidates {
            let record = candidate.bot.record;
            let capabilities = bot_capabilities_from_record(&record);
            let bot_uuid = record.bot_id;
            let dynamic_status = if record.status == ActorStatus::Online
                && active_bot_ids.contains(&bot_uuid)
            {
                DynamicStatusResponse {
                    status: "active".to_string(),
                }
            } else {
                DynamicStatusResponse {
                    status: "offline".to_string(),
                }
            };
            items.push(BotQueryEntry {
                agent_card: None,
                bot_uuid,
                capabilities,
                visibility: record.visibility,
                status: record.status,
                actor_kind: record.kind,
                env: Some(record.env),
                dynamic_status,
                created_by: record.created_by,
                user_visibility: user_visibility_to_wire(record.user_visibility).to_string(),
                friend_ext: record.friend_ext,
                friend_check_in_strategy: friend_check_in_strategy_to_wire(
                    record.friend_check_in_strategy,
                )
                .to_string(),
                is_friend: viewer_actor_id.map(|_| candidate.is_friend),
                access_relation: None,
            });
        }

        self.attach_query_cards(&mut items).await?;
        Ok(BotSearchResult { items, total })
    }


}

#[async_trait]
impl BotDiscoveryService for Bot {
    async fn discover_bots(
        &self,
        command: BotDiscoveryCommand,
    ) -> Result<BotDiscoveryResult, BotUseCaseError> {
        if command.role.is_some() && command.organization_code.is_none() {
            return Err(ServiceError::InvalidOperation {
                message: "role_requires_organization_code".to_string(),
                request_id: None,
            }
            .into());
        }
        if command.organization_code.is_some() {
            let code = command.organization_code.clone().unwrap_or_default();
            return self.discover_organization_bots(&code, command).await;
        }

        let bots = self.discover_candidates(&command).await;

        if let Some(collaborate_bot) = command.collaborate_bot.as_deref() {
            let collaborate_bot_is_private = self
                .registry
                .get(collaborate_bot)
                .await
                .map(|bot| !is_discover_visible(&bot.capabilities.visibility))
                .unwrap_or(false);
            if collaborate_bot_is_private {
                return Ok(BotDiscoveryResult {
                    bots: Vec::new(),
                    count: 0,
                });
            }
        }

        let friend_uuids = if let Some(collaborate_bot) = command.collaborate_bot.as_deref() {
            Some(self.friend.list_friends(collaborate_bot).await)
        } else {
            None
        };

        let entries = bots
            .into_iter()
            .filter(|candidate| {
                command.requester_bot_id.as_deref() != Some(candidate.bot.bot_uuid.as_str())
            })
            .filter_map(|candidate| discover_entry(candidate, &command, friend_uuids.as_ref()))
            .collect::<Vec<_>>();
        let entries = self.filter_discovery_cards(entries, &command).await?;
        let mut entries_with_agent_code = Vec::with_capacity(entries.len());
        for mut entry in entries {
            entry.agent_code = self
                .registry
                .get_agent_credentials(&entry.bot_uuid)
                .await
                .and_then(|credentials| credentials.agent_code);
            entries_with_agent_code.push(entry);
        }
        Ok(BotDiscoveryResult {
            count: entries_with_agent_code.len(),
            bots: entries_with_agent_code,
        })
    }
}
