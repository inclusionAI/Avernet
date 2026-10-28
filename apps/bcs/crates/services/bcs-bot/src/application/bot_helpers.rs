use super::*;
use bcs_service_api::port::bot_authority::BotAuthorityPort;

impl Bot {
    pub(super) async fn discover_organization_bots(
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

    pub(super) async fn validate_connect_bot_id(&self, bot_id: &str) -> Result<(), BotUseCaseError> {
        if !bot_id.starts_with("human_") {
            return Ok(());
        }

        match self.registry.get(bot_id).await {
            Some(existing) if existing.actor_kind == ActorKind::Human => Ok(()),
            _ => Err(BotUseCaseError::InvalidBotId(
                "human_ 前缀仅用于 Human Actor".to_string(),
            )),
        }
    }

    pub(super) async fn is_provider_managed_bot(&self, bot_id: &str) -> Result<bool, BotUseCaseError> {
        let Some(bot_core) = self.bot_core.as_ref() else {
            return Ok(false);
        };
        let Some(bindings) = bot_core.provider_bindings_repo() else {
            return Ok(false);
        };
        Ok(bindings.get_binding_by_bot_uuid(bot_id).await?.is_some())
    }

    pub(super) async fn bot_to_detail(&self, bot: RegisteredBot) -> BotDetailResult {
        let visibility = bot.capabilities.visibility.clone();
        let created_by = bot.created_by.clone();
        let dynamic_status = effective_dynamic_status(self.registry.as_ref(), &bot).await;

        BotDetailResult {
            bot_uuid: bot.bot_uuid,
            capabilities: bot.capabilities,
            status: bot.status,
            visibility,
            owner_actor_id: owner_actor_id(created_by.clone()),
            created_by,
            actor_kind: bot.actor_kind,
            env: bot.env,
            dynamic_status,
        }
    }

    pub(super) async fn bot_page_from_registered(
        &self,
        bots: Vec<RegisteredBot>,
        offset: u64,
        limit: u64,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        let total = bots.len() as u64;
        let page = bots
            .into_iter()
            .skip(to_usize(offset))
            .take(to_usize(limit))
            .collect::<Vec<_>>();
        let mut items = Vec::with_capacity(page.len());
        for bot in page {
            items.push(self.bot_to_query_entry(bot).await);
        }
        Ok(BotPagedListResult {
            items,
            total,
            offset,
            limit,
        })
    }

    pub(super) async fn my_bot_page_from_registered(
        &self,
        bots: Vec<RegisteredBot>,
        offset: u64,
        limit: u64,
        active_only: bool,
        access_relations: std::collections::HashMap<String, String>,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        let mut entries = Vec::with_capacity(bots.len());
        let bot_uuids = bots
            .iter()
            .map(|bot| bot.bot_uuid.clone())
            .collect::<Vec<_>>();
        let active_bot_ids = self
            .registry
            .list_runtime_active_bot_ids(&bot_uuids)
            .await
            .into_iter()
            .collect::<std::collections::HashSet<_>>();
        for bot in bots {
            let is_active = active_bot_ids.contains(&bot.bot_uuid);
            if active_only && !is_active {
                continue;
            }
            let bot_uuid = bot.bot_uuid.clone();
            let access_relation = access_relations.get(&bot_uuid).cloned();
            entries.push((
                is_active,
                bot_uuid,
                Self::bot_to_my_query_entry(bot, is_active, access_relation),
            ));
        }
        entries.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.cmp(&b.1)));
        let total = entries.len() as u64;
        let items = entries
            .into_iter()
            .skip(to_usize(offset))
            .take(to_usize(limit))
            .map(|(_, _, entry)| entry)
            .collect();

        Ok(BotPagedListResult {
            items,
            total,
            offset,
            limit,
        })
    }

    pub(super) fn bot_to_my_query_entry(
        bot: RegisteredBot,
        is_active: bool,
        access_relation: Option<String>,
    ) -> BotQueryEntry {
        let visibility = bot.capabilities.visibility.clone();
        BotQueryEntry {
            bot_uuid: bot.bot_uuid,
            capabilities: bot.capabilities,
            visibility,
            status: bot.status,
            actor_kind: bot.actor_kind,
            env: bot.env,
            dynamic_status: DynamicStatusResponse {
                status: if is_active { "active" } else { "offline" }.to_string(),
            },
            created_by: bot.created_by,
            user_visibility: user_visibility_to_wire(UserVisibility::Protected).to_string(),
            friend_ext: serde_json::Map::new(),
            friend_check_in_strategy: friend_check_in_strategy_to_wire(
                FriendCheckInStrategy::Approval,
            )
            .to_string(),
            is_friend: None,
            access_relation,
        }
    }

    pub(super) async fn bot_to_query_entry(&self, bot: RegisteredBot) -> BotQueryEntry {
        let visibility = bot.capabilities.visibility.clone();
        let dynamic_status = effective_dynamic_status(self.registry.as_ref(), &bot).await;
        BotQueryEntry {
            bot_uuid: bot.bot_uuid,
            capabilities: bot.capabilities,
            visibility,
            status: bot.status,
            actor_kind: bot.actor_kind,
            env: bot.env,
            dynamic_status,
            created_by: bot.created_by,
            user_visibility: user_visibility_to_wire(UserVisibility::Protected).to_string(),
            friend_ext: serde_json::Map::new(),
            friend_check_in_strategy: friend_check_in_strategy_to_wire(
                FriendCheckInStrategy::Approval,
            )
            .to_string(),
            is_friend: None,
            access_relation: None,
        }
    }

    pub(super) async fn discover_candidates(&self, command: &BotDiscoveryCommand) -> Vec<DiscoveryCandidate> {
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

    pub(super) async fn discover_provider_bots(&self, command: &BotDiscoveryCommand) -> Vec<DiscoveryCandidate> {
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
}

pub(super) fn is_in_list_bots_scope(bot: &RegisteredBot, onboarded: Option<bool>) -> bool {
    match onboarded {
        Some(false) => bot.capabilities.name.is_none(),
        _ => {
            if bot.bot_uuid.contains("default") {
                bot.capabilities
                    .summary
                    .as_ref()
                    .is_some_and(|summary| !summary.is_empty())
            } else {
                bot.capabilities.name.is_some()
            }
        }
    }
}

pub(super) fn bot_to_list_entry(bot: RegisteredBot) -> BotListEntry {
    let name = bot.capabilities.name.clone();
    let summary = bot.capabilities.summary.clone();
    let visibility = bot.capabilities.visibility.clone();
    let created_by = bot.created_by.clone();

    BotListEntry {
        bot_uuid: bot.bot_uuid,
        name,
        summary,
        capabilities: bot.capabilities,
        status: bot.status,
        visibility,
        owner_actor_id: owner_actor_id(created_by.clone()),
        created_by,
    }
}

pub(super) struct DiscoveryCandidate {
    pub(super) bot: RegisteredBot,
    provider_info: Option<BotDiscoveryProviderInfo>,
}

pub(super) fn discover_entry(
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

pub(super) fn is_discover_visible(visibility: &str) -> bool {
    matches!(visibility, "public" | "protected")
}

pub(super) fn is_organization_discover_visible(visibility: &str) -> bool {
    matches!(visibility, "public" | "protected")
}

pub(super) fn provider_discovery_selector(command: &BotDiscoveryCommand) -> ProviderBotDiscoverySelector {
    if let Some(q) = command.q.as_deref() {
        ProviderBotDiscoverySelector::Query(q.to_string())
    } else if !command.skills.is_empty() {
        ProviderBotDiscoverySelector::RequiredSkills(command.skills.clone())
    } else {
        ProviderBotDiscoverySelector::All
    }
}

pub(super) fn matches_discovery_selector(bot: &RegisteredBot, command: &BotDiscoveryCommand) -> bool {
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

pub(super) fn matches_query(bot: &RegisteredBot, query: &str) -> bool {
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

pub(super) fn contains_ignore_case(value: &str, query: &str) -> bool {
    value.to_lowercase().contains(&query.to_lowercase())
}

pub(super) fn bot_capabilities_from_record(record: &BotControlPlaneRecord) -> BotCapabilities {
    BotCapabilities {
        name: non_empty_text(Some(record.name.as_str())),
        summary: non_empty_text(Some(record.descriptor.summary.as_str())),
        domains: record.descriptor.domains.clone(),
        skills: record.descriptor.skills.clone(),
        scopes: record.descriptor.scopes.clone(),
        visibility: record.visibility.clone(),
        ..Default::default()
    }
}

pub(super) fn user_visibility_to_wire(value: UserVisibility) -> &'static str {
    match value {
        UserVisibility::Public => "public",
        UserVisibility::Protected => "protected",
        UserVisibility::Private => "private",
    }
}

pub(super) fn friend_check_in_strategy_to_wire(value: FriendCheckInStrategy) -> &'static str {
    match value {
        FriendCheckInStrategy::Open => "OPEN",
        FriendCheckInStrategy::Approval => "APPROVAL",
        FriendCheckInStrategy::DeptFree => "DEPT_FREE",
    }
}

pub(super) fn non_empty_text(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

pub(super) fn created_by_from_provider_bot_ref(provider_bot_ref: &str) -> Option<String> {
    provider_bot_ref
        .rsplit_once(':')
        .map(|(_, owner)| owner.trim())
        .filter(|owner| !owner.is_empty())
        .map(str::to_string)
}

pub(super) fn owner_from_provider_bot_ref(provider_bot_ref: &str) -> Result<String, BotUseCaseError> {
    created_by_from_provider_bot_ref(provider_bot_ref).ok_or_else(|| {
        BotUseCaseError::InvalidProviderBotRef(
            "provider_bot_ref must include owner staff_no".to_string(),
        )
    })
}

pub(super) async fn effective_dynamic_status(
    registry: &dyn BotRegistryCoreService,
    bot: &RegisteredBot,
) -> DynamicStatusResponse {
    let is_active =
        bot.status == ActorStatus::Online && registry.is_effectively_online(&bot.bot_uuid).await;
    DynamicStatusResponse {
        status: if is_active { "active" } else { "offline" }.to_string(),
    }
}

pub(super) fn owner_actor_id(created_by: Option<String>) -> Option<String> {
    created_by.map(|owner| {
        if owner.starts_with("human_") {
            owner
        } else {
            format!("human_{owner}")
        }
    })
}

pub(super) fn is_valid_visibility(visibility: &str) -> bool {
    matches!(visibility, "public" | "protected" | "private")
}

pub(super) fn normalized_visibility(visibility: &str) -> &'static str {
    match visibility {
        "public" => "public",
        "protected" => "protected",
        _ => "private",
    }
}

/// Visibility read gate (bot-authority plan Task 6,
/// carry B): the Human→Bot leg consults the CURRENT
/// live management edge — a manager of a controlled
/// Bot may read its visibility info; the historical
/// `created_by` mismatch (and the `created_by: None`
/// legacy read fallback) no longer silently
/// authorizes. A failing authority read is an internal
/// error, never a deny-as-verdict.
pub(super) async fn authorize_visibility_read(
    caller_actor_id: Option<&str>,
    bot: &RegisteredBot,
    authority: Option<&dyn BotAuthorityPort>,
) -> Result<(), BotUseCaseError> {
    let Some(caller_actor_id) = caller_actor_id else {
        return Ok(());
    };

    if caller_actor_id == bot.bot_uuid {
        return Ok(());
    }

    if let Some(staff_no) = caller_actor_id.strip_prefix("human_") {
        let Some(authority) = authority else {
            return Err(BotUseCaseError::Service(
                ServiceError::InternalError(
                    "bot authority hook is not wired".to_string(),
                ),
            ));
        };
        return match authority.can_manage(staff_no, &bot.bot_uuid).await {
            Ok(true) => Ok(()),
            Ok(false) => Err(BotUseCaseError::Forbidden(format!(
                "Not authorized to access bot '{}'",
                bot.bot_uuid
            ))),
            Err(error) => Err(BotUseCaseError::Service(
                ServiceError::InternalError(format!(
                    "bot authority lookup failed: {error}"
                )),
            )),
        };
    }

    match bot.capabilities.visibility.as_str() {
        "public" | "protected" => Ok(()),
        _ => Err(ServiceError::BotNotFound(bot.bot_uuid.clone()).into()),
    }
}

pub(super) fn is_owner_suffixed_bot_id_for_staff(bot_uuid: &str, staff_no: &str) -> bool {
    bot_uuid
        .rsplit_once(':')
        .is_some_and(|(_, suffix)| suffix == staff_no)
}

/// Legacy TC-origin classification (F5): a Bot whose OWN
/// persisted `created_by` matches the `<prefix>:<staff_no>`
/// owner-suffix convention is a TC-origin Bot and must never be
/// deletable through the ordinary Avernet delete path — for ANY
/// current owner or manager. The classification is intrinsic to
/// the Bot record (origin marker), never a function of the
/// requester: ownership transfer must not reclassify it either
/// (`created_by` stays the historical origin marker; spec §3.6.6).
/// A missing `created_by` cannot prove origin — such legacy rows
/// fall through to the ordinary path and need their own data
/// investigation; this helper never invents one.
pub(super) fn is_legacy_tc_origin_bot(bot: &RegisteredBot) -> bool {
    match bot.created_by.as_deref() {
        Some(staff_no) => is_owner_suffixed_bot_id_for_staff(&bot.bot_uuid, staff_no),
        None => false,
    }
}



pub(super) fn authorize_bot_management(
    caller_actor_id: Option<&str>,
    bot: &RegisteredBot,
) -> Result<(), BotUseCaseError> {
    let Some(caller_actor_id) = caller_actor_id else {
        return Err(BotUseCaseError::Unauthorized(format!(
            "caller identity is required to modify bot '{}'",
            bot.bot_uuid
        )));
    };

    if caller_actor_id == bot.bot_uuid {
        return Ok(());
    }

    let Some(owner_staff_no) = bot.created_by.as_deref() else {
        return Err(BotUseCaseError::Forbidden(format!(
            "caller '{}' is not the owner of bot '{}'",
            caller_actor_id, bot.bot_uuid
        )));
    };

    if caller_actor_id == owner_staff_no
        || caller_actor_id.strip_prefix("human_") == Some(owner_staff_no)
    {
        return Ok(());
    }

    Err(BotUseCaseError::Forbidden(format!(
        "caller '{}' is not the owner of bot '{}'",
        caller_actor_id, bot.bot_uuid
    )))
}

pub(super) fn to_usize(value: u64) -> usize {
    usize::try_from(value).unwrap_or(usize::MAX)
}

pub(super) fn now_ms() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod legacy_tc_origin_tests {
    use super::*;

    fn bot(bot_uuid: &str, created_by: Option<&str>) -> RegisteredBot {
        RegisteredBot {
            bot_uuid: bot_uuid.to_string(),
            capabilities: BotCapabilities::default(),
            env: None,
            created_by: created_by.map(str::to_string),
            actor_kind: ActorKind::Bot,
            status: ActorStatus::Online,
        }
    }

    #[test]
    fn tc_origin_is_intrinsic_to_the_bot_record() {
        // The `<prefix>:<staff_no>` suffix matching the persisted
        // creator classifies, regardless of who asks.
        assert!(is_legacy_tc_origin_bot(&bot("teamclaw-bot:alice", Some("alice"))));
        assert!(is_legacy_tc_origin_bot(&bot("custom-prefix:carol", Some("carol"))));
        // Supported multi-colon ids anchor on the LAST segment
        // (the Memory-store parsing rule); the creator must match
        // that anchor.
        assert!(is_legacy_tc_origin_bot(&bot("ns:teamclaw-bot:alice", Some("alice"))));
        assert!(!is_legacy_tc_origin_bot(&bot("ns:teamclaw-bot:alice", Some("ns"))));
    }

    #[test]
    fn non_tc_records_stay_on_the_ordinary_path() {
        // Ordinary ids, suffix/creator mismatch, and a missing
        // creator (origin unprovable — a data-investigation case,
        // never a classification this helper invents).
        assert!(!is_legacy_tc_origin_bot(&bot("plain-bot", Some("alice"))));
        assert!(!is_legacy_tc_origin_bot(&bot("teamclaw-bot:alice", Some("bob"))));
        assert!(!is_legacy_tc_origin_bot(&bot("teamclaw-bot:alice", None)));
        assert!(!is_legacy_tc_origin_bot(&bot("no-colon-id", Some("colin"))));
    }
}
