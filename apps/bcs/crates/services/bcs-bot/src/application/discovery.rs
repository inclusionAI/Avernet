//! Bot discovery + search lane of the `Bot` application service
//! (plan Task 12 fix round, behavior-preserving move out of the former
//! over-limit `bot.rs`): the `BotDiscoveryService` projection, the merged
//! registry/provider candidate machinery, and the control-plane-backed
//! `search_bots` implementation with its selector helpers.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Arc;
use std::time::Instant;

use async_trait::async_trait;

use bcs_service_api::{
    ActorKind, ActorStatus, BotControlPlaneCoreService, BotDiscoveryCommand,
    BotDiscoveryEntry, BotDiscoveryProviderInfo, BotDiscoveryResult, BotDiscoveryService,
    BotQueryEntry, BotQueryService, BotSearchCandidateQuery, BotSearchFriendshipFilter,
    BotSearchResult, BotUseCaseError, DynamicStatusResponse, FriendCoreService,
    OrganizationCoreService, OrganizationMemberSummary, ProviderBotDiscoverySelector,
    RegisteredBot, ServiceError,
    SearchBotsCommand,
    FriendCheckInStrategy, UserVisibility,
};

use crate::application::bot::{
    bot_capabilities_from_record, contains_ignore_case, friend_check_in_strategy_to_wire,
    owner_actor_id, user_visibility_to_wire, Bot,
};


#[async_trait]
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
            .filter(|candidate| matches_discovery_selector(&candidate.bot, &command))
            .filter_map(|candidate| discover_entry(candidate, &command, friend_uuids.as_ref()))
            .collect::<Vec<_>>();
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

impl Bot {
    async fn discover_organization_bots(
        &self,
        organization_code: &str,
        command: BotDiscoveryCommand,
    ) -> Result<BotDiscoveryResult, BotUseCaseError> {
        let requester = command.requester_bot_id.as_deref().ok_or_else(|| {
            BotUseCaseError::Forbidden("organization discovery requires a bot caller".to_string())
        })?;
        let organization = self.organization.as_ref().ok_or_else(|| {
            ServiceError::InvalidOperation {
                message: "organization service is not configured".to_string(),
                request_id: None,
            }
        })?;
        organization
            .require_runtime_member(organization_code, requester)
            .await?;
        let (member_by_bot, bots) = match organization
            .list_runtime_discovery_bots(organization_code, command.role.as_deref())
            .await?
        {
            Some(discovery_bots) => {
                let member_by_bot = discovery_bots
                    .iter()
                    .map(|bot| (bot.bot_uuid.clone(), bot.role.clone()))
                    .collect::<BTreeMap<_, _>>();
                let bots = discovery_bots
                    .into_iter()
                    .map(|bot| RegisteredBot {
                        bot_uuid: bot.bot_uuid,
                        capabilities: bot.capabilities,
                        env: None,
                        created_by: None,
                        actor_kind: bot.actor_kind,
                        status: ActorStatus::Online,
                    })
                    .collect();
                (member_by_bot, bots)
            }
            None => {
                let members = organization
                    .list_runtime_members(organization_code, command.role.as_deref())
                    .await?;
                let member_by_bot = members
                    .iter()
                    .map(|member| (member.bot_uuid.clone(), member.role.clone()))
                    .collect::<BTreeMap<_, _>>();
                let bot_ids = members.into_iter().map(|member| member.bot_uuid).collect::<Vec<_>>();
                (member_by_bot, self.registry.get_by_ids(&bot_ids).await)
            }
        };
        let friend_ids = self.friend.list_friends(requester).await;
        let friend_ids = friend_ids.into_iter().collect::<std::collections::HashSet<_>>();
        let mut entries = Vec::new();
        for bot in bots {
            if bot.actor_kind != ActorKind::Bot || bot.capabilities.name.is_none() {
                continue;
            }
            if bot.bot_uuid.as_str() == requester {
                continue;
            }
            if !matches_discovery_selector(&bot, &command) {
                continue;
            }
            let visibility = bot.capabilities.visibility.clone();
            if let Some(visibility_filter) = command.visibility.as_deref() {
                if visibility != visibility_filter {
                    continue;
                }
            }
            let is_friend = friend_ids.contains(&bot.bot_uuid);
            if !is_organization_discover_visible(&visibility) && !is_friend {
                continue;
            }
            let Some(role) = member_by_bot.get(&bot.bot_uuid) else {
                continue;
            };
            let agent_code = bot.capabilities.agent_code.clone();
            entries.push(BotDiscoveryEntry {
                bot_uuid: bot.bot_uuid,
                capabilities: bot.capabilities,
                visibility,
                is_friend: Some(is_friend),
                agent_code,
                provider_info: None,
                organization_member: Some(OrganizationMemberSummary {
                    organization_code: organization_code.to_string(),
                    role: role.clone(),
                }),
            });
        }
        Ok(BotDiscoveryResult {
            count: entries.len(),
            bots: entries,
        })
    }

    async fn discover_candidates(&self, command: &BotDiscoveryCommand) -> Vec<DiscoveryCandidate> {
        let mut merged = BTreeMap::new();
        for bot in self.registry.list_active().await {
            merged.insert(
                bot.bot_uuid.clone(),
                DiscoveryCandidate {
                    bot,
                    provider_info: None,
                },
            );
        }

        for candidate in self.discover_provider_bots(command).await {
            merged.insert(candidate.bot.bot_uuid.clone(), candidate);
        }

        merged.into_values().collect()
    }

    async fn discover_provider_bots(&self, command: &BotDiscoveryCommand) -> Vec<DiscoveryCandidate> {
        let Some(bot_core) = self.bot_core.as_ref() else {
            return Vec::new();
        };
        let Some(provider_bindings) = bot_core.provider_bindings_repo() else {
            return Vec::new();
        };
        let selector = provider_discovery_selector(command);
        let query_started_at = std::time::Instant::now();
        let records_result = provider_bindings
            .list_discoverable_provider_bot_records(&selector)
            .await;
        let elapsed_ms = query_started_at.elapsed().as_millis();
        let records = match records_result {
            Ok(records) => {
                tracing::info!(
                    elapsed_ms = %elapsed_ms,
                    record_count = records.len(),
                    selector = ?selector,
                    "discover_provider_bots: listed provider bot records"
                );
                records
            }
            Err(error) => {
                tracing::warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    elapsed_ms = %elapsed_ms,
                    selector = ?selector,
                    error = %error,
                    "discover_provider_bots: failed to list provider bot records"
                );
                return Vec::new();
            }
        };
        if records.is_empty() {
            return Vec::new();
        }

        let bot_ids = records
            .iter()
            .map(|record| record.bot_uuid.clone())
            .collect::<Vec<_>>();
        let mut provider_info_by_bot = records
            .into_iter()
            .map(|record| {
                (
                    record.bot_uuid,
                    BotDiscoveryProviderInfo {
                        provider_id: record.provider_id,
                        provider_name: record.provider_name,
                    },
                )
            })
            .collect::<BTreeMap<_, _>>();

        self.registry
            .get_by_ids(&bot_ids)
            .await
            .into_iter()
            .filter(|bot| bot.actor_kind == ActorKind::Bot)
            .filter_map(|bot| {
                provider_info_by_bot
                    .remove(&bot.bot_uuid)
                    .map(|provider_info| DiscoveryCandidate {
                        bot,
                        provider_info: Some(provider_info),
                    })
            })
            .collect()
    }

    pub(crate) async fn search_bots_via_control_plane(
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

        Ok(BotSearchResult { items, total })
    }
}

struct DiscoveryCandidate {
    bot: RegisteredBot,
    provider_info: Option<BotDiscoveryProviderInfo>,
}
fn discover_entry(
    candidate: DiscoveryCandidate,
    command: &BotDiscoveryCommand,
    friend_uuids: Option<&Vec<String>>,
) -> Option<BotDiscoveryEntry> {
    let DiscoveryCandidate { bot, provider_info } = candidate;
    let visibility = bot.capabilities.visibility.clone();
    if !is_discover_visible(&visibility) {
        return None;
    }

    if let Some(visibility_filter) = command.visibility.as_deref() {
        if visibility != visibility_filter {
            return None;
        }
    }

    let is_friend = if let Some(friends) = friend_uuids {
        let is_friend = friends.contains(&bot.bot_uuid);
        if command.visibility.is_none() && visibility != "public" && !is_friend {
            return None;
        }
        Some(is_friend)
    } else {
        None
    };

    Some(BotDiscoveryEntry {
        bot_uuid: bot.bot_uuid,
        capabilities: bot.capabilities,
        visibility,
        is_friend,
        agent_code: None,
        provider_info,
        organization_member: None,
    })
}
fn is_discover_visible(visibility: &str) -> bool {
    matches!(visibility, "public" | "protected")
}
fn is_organization_discover_visible(visibility: &str) -> bool {
    matches!(visibility, "public" | "protected")
}
fn provider_discovery_selector(command: &BotDiscoveryCommand) -> ProviderBotDiscoverySelector {
    if let Some(q) = command.q.as_deref() {
        ProviderBotDiscoverySelector::Query(q.to_string())
    } else if !command.skills.is_empty() {
        ProviderBotDiscoverySelector::RequiredSkills(command.skills.clone())
    } else {
        ProviderBotDiscoverySelector::All
    }
}
fn matches_discovery_selector(bot: &RegisteredBot, command: &BotDiscoveryCommand) -> bool {
    let matches_q = command
        .q
        .as_deref()
        .is_none_or(|query| matches_query(bot, query));
    let matches_skills = command.skills.iter().all(|skill| {
        bot
            .capabilities
            .skills
            .iter()
            .any(|candidate| candidate.name.eq_ignore_ascii_case(skill))
    });

    matches_q && matches_skills
}
fn matches_query(bot: &RegisteredBot, query: &str) -> bool {
    bot.capabilities
        .name
        .as_deref()
        .is_some_and(|value| contains_ignore_case(value, query))
        || bot
            .capabilities
            .summary
            .as_deref()
            .is_some_and(|value| contains_ignore_case(value, query))
        || bot
            .capabilities
            .domains
            .iter()
            .any(|value| contains_ignore_case(value, query))
        || bot
            .capabilities
            .skills
            .iter()
            .any(|skill| contains_ignore_case(&skill.name, query))
        || bot
            .capabilities
            .scopes
            .iter()
            .any(|value| contains_ignore_case(value, query))
        || contains_ignore_case(&bot.bot_uuid, query)
}