//! Versioned Bot control-plane application facade for the BCN V1 API.

pub mod authority;
pub mod authority_policy;
mod bot_self;
pub use authority::BotAuthorityServiceImpl;
pub use bot_self::BotSelfServiceImpl;

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::bot_authority::ERROR_BOT_LIST_CHANGED;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedUserIdentity, Bot, BotCandidate, BotCandidatePurpose,
    BotCandidateSearchItem, BotCandidateSearchMode, BotCandidateSearchResult, BotDescriptor,
    BotKind, BotProvider, BotReachability, BotService, BotSkill, BotStatus, BotVisibility, GetBot,
    HumanBot, InternalBotAttributesService, ListBotCandidates, ListMyBots, Page,
    PatchBotInternalAttributes, PhysicalBot, QueryBots, SearchBotCandidates, UpdateBot,
    require_authenticated_user,
};
use bcs_service_api::application::ConnectService;
use bcs_service_api::port::bot_authority::BotAuthorityHook;
use bcs_service_api::types::bot_authority::BotAccessRelation;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCandidateReadQuery, BotCandidateSearchCoreService,
    BotCandidateSearchMode as CoreCandidateSearchMode, BotCandidateSearchQuery,
    BotCandidateVisibility, BotControlPlaneCoreService, BotControlPlaneDescriptorPatch,
    BotControlPlanePatch, BotControlPlaneRecord, BotControlPlaneView,
    BotRegistryCoreService, ControllableCandidateQuery, ControlledBotCandidate,
    FriendCoreService, RuntimeActivitySnapshot, ServiceError,
};

#[derive(Debug, Clone)]
pub struct BotServiceConfig {
    pub env: String,
}

pub struct BotServiceImpl {
    control_plane: Arc<dyn BotControlPlaneCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    friends: Arc<dyn FriendCoreService>,
    connect_service: Arc<dyn ConnectService>,
    candidate_search: Arc<dyn BotCandidateSearchCoreService>,
    /// The narrow authority hook (bot-authority plan Task 6.3):
    /// every Human→Bot control judgment in this facade consults
    /// it live; a missing wiring fails closed.
    bot_authority: Option<Arc<dyn BotAuthorityHook>>,
    config: BotServiceConfig,
}

/// Map one authority read to application errors: a strict deny
/// (`Forbidden`) is 403 at the entry's own conventions; every
/// other read failure propagates as an internal error (spec §3.2:
/// 存储错误返回错误 — never a deny-as-false).
pub(crate) fn map_authority_error(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::Forbidden(message) => {
            ApplicationError::forbidden(message)
        }
        other => ApplicationError::internal(format!(
            "bot authority lookup failed: {other}"
        )),
    }
}

pub struct InternalBotAttributesServiceImpl {
    control_plane: Arc<dyn BotControlPlaneCoreService>,
    config: BotServiceConfig,
}

impl InternalBotAttributesServiceImpl {
    pub fn new(
        control_plane: Arc<dyn BotControlPlaneCoreService>,
        config: BotServiceConfig,
    ) -> Self {
        Self {
            control_plane,
            config,
        }
    }

    fn validate_bot_id(bot_id: &str) -> Result<(), ApplicationError> {
        if bot_id.trim().is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "bot_id must not be empty",
            ));
        }
        Ok(())
    }

    fn validate_visibility(visibility: Option<&str>) -> Result<(), ApplicationError> {
        if let Some(visibility) = visibility
            && !matches!(visibility, "public" | "protected" | "private")
        {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "visibility must be public, protected, or private",
            ));
        }
        Ok(())
    }

    async fn load_record(&self, bot_id: &str) -> Result<BotControlPlaneRecord, ApplicationError> {
        self.control_plane
            .get_record(bot_id, &self.config.env)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "bot_not_found",
                    format!("Bot '{bot_id}' was not found"),
                )
            })
    }
}

#[async_trait]
impl InternalBotAttributesService for InternalBotAttributesServiceImpl {
    async fn get(
        &self,
        bot_id: String,
    ) -> Result<bcs_service_api::BotInternalAttributes, ApplicationError> {
        Self::validate_bot_id(&bot_id)?;
        Ok(self.load_record(&bot_id).await?.internal_attributes())
    }

    async fn patch(
        &self,
        command: PatchBotInternalAttributes,
    ) -> Result<bcs_service_api::BotInternalAttributes, ApplicationError> {
        Self::validate_bot_id(&command.bot_id)?;
        Self::validate_visibility(command.visibility.as_deref())?;
        if command.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "Bot internal attributes patch must contain at least one field",
            ));
        }
        let updated = self
            .control_plane
            .patch(
                &command.bot_id,
                &self.config.env,
                BotControlPlanePatch {
                    visibility: command.visibility,
                    user_visibility: command.user_visibility,
                    friend_ext: command.friend_ext,
                    friend_check_in_strategy: command.friend_check_in_strategy,
                    ..Default::default()
                },
            )
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "bot_not_found",
                    format!("Bot '{}' was not found", command.bot_id),
                )
            })?;
        Ok(updated.record.internal_attributes())
    }
}

impl BotServiceImpl {
    pub fn new(
        control_plane: Arc<dyn BotControlPlaneCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        friends: Arc<dyn FriendCoreService>,
        connect_service: Arc<dyn ConnectService>,
        candidate_search: Arc<dyn BotCandidateSearchCoreService>,
        config: BotServiceConfig,
    ) -> Self {
        Self {
            control_plane,
            registry,
            friends,
            connect_service,
            candidate_search,
            bot_authority: None,
            config,
        }
    }

    /// Wire the authority hook (bot-authority plan Task 6.3).
    pub fn with_bot_authority_hook(
        mut self,
        bot_authority: Arc<dyn BotAuthorityHook>,
    ) -> Self {
        self.bot_authority = Some(bot_authority);
        self
    }

    /// The wired authority hook; failing to wire one is a
    /// composition error and must fail closed (never deny-as-Ok).
    fn authority(&self) -> Result<&dyn BotAuthorityHook, ApplicationError> {
        self.bot_authority
            .as_deref()
            .ok_or_else(|| {
                ApplicationError::internal(
                    "bot authority hook is not wired",
                )
            })
    }

    fn human_staff_no(
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
    ) -> Result<String, ApplicationError> {
        Ok(require_authenticated_user(caller)?.id.clone())
    }

    fn human_display_name(human: &AuthenticatedUserIdentity) -> String {
        human
            .display_name
            .as_deref()
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .or_else(|| {
                human
                    .full_name
                    .as_deref()
                    .map(str::trim)
                    .filter(|name| !name.is_empty())
            })
            .or_else(|| {
                let username = human.username.trim();
                (!username.is_empty()).then_some(username)
            })
            .unwrap_or(human.id.as_str())
            .to_string()
    }

    fn validate_pagination(offset: u64, limit: u64) -> Result<(), ApplicationError> {
        let _ = offset;
        if (1..=100).contains(&limit) {
            Ok(())
        } else {
            Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ))
        }
    }

    fn validate_bot_id(bot_id: &str) -> Result<(), ApplicationError> {
        if bot_id.trim().is_empty() {
            Err(ApplicationError::invalid(
                "invalid_request",
                "bot_id must not be empty",
            ))
        } else {
            Ok(())
        }
    }

    async fn load_record(&self, bot_id: &str) -> Result<BotControlPlaneRecord, ApplicationError> {
        self.control_plane
            .get_record(bot_id, &self.config.env)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "bot_not_found",
                    format!("Bot '{bot_id}' was not found"),
                )
            })
    }

    async fn load_view(&self, bot_id: &str) -> Result<BotControlPlaneView, ApplicationError> {
        self.control_plane
            .get(bot_id, &self.config.env)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "bot_not_found",
                    format!("Bot '{bot_id}' was not found"),
                )
            })
    }

    async fn project_records(
        &self,
        records: Vec<BotControlPlaneView>,
    ) -> Result<Vec<Bot>, ApplicationError> {
        let physical_ids = records
            .iter()
            .filter(|bot| bot.record.kind == ActorKind::Bot)
            .map(|bot| bot.record.bot_id.clone())
            .collect::<Vec<_>>();
        let runtime_active = self
            .registry
            .list_runtime_active_bot_ids(&physical_ids)
            .await
            .into_iter()
            .collect::<HashSet<_>>();
        self.project_records_with_activity(records, &runtime_active)
            .await
    }

    /// Project records onto the public Bot shapes using an
    /// ALREADY-OBSERVED runtime-activity sampling. The mine
    /// page shares the one `try_runtime_activity_for`
    /// observation with its reachability total (spec §8.0:
    /// 页内 total/items 使用同一采样) instead of
    /// re-observing per page.
    async fn project_records_with_activity(
        &self,
        records: Vec<BotControlPlaneView>,
        runtime_active: &HashSet<String>,
    ) -> Result<Vec<Bot>, ApplicationError> {
        records
            .into_iter()
            .map(|bot| {
                let BotControlPlaneView { record, provider } = bot;
                let visibility = project_visibility(&record.visibility)?;
                let status = project_status(record.status);
                if record.kind == ActorKind::Human {
                    return Ok(Bot::Human(HumanBot {
                        bot_id: record.bot_id,
                        kind: BotKind::Human,
                        name: record.name,
                        visibility,
                        status,
                        env: record.env,
                        created_by: record.created_by,
                        user_visibility: record.user_visibility,
                        friend_ext: record.friend_ext,
                        friend_check_in_strategy: record.friend_check_in_strategy,
                        created_at: record.created_at,
                        updated_at: record.updated_at,
                        // The mine page stamps its
                        // sampled relation afterwards;
                        // other projections stay
                        // relation-less (spec §8 mine
                        // row only).
                        access_relation: None,
                    }));
                }

                let reachability = if record.status == ActorStatus::Online
                    && runtime_active.contains(&record.bot_id)
                {
                    BotReachability::Reachable
                } else {
                    BotReachability::Unreachable
                };
                let provider = provider.map(|provider| BotProvider {
                    provider_id: provider.provider_id,
                    name: provider.name,
                });
                Ok(Bot::Physical(PhysicalBot {
                    bot_id: record.bot_id,
                    kind: BotKind::Bot,
                    name: record.name,
                    visibility,
                    status,
                    env: record.env,
                    created_by: record.created_by,
                    descriptor: BotDescriptor {
                        summary: record.descriptor.summary,
                        domains: record.descriptor.domains,
                        skills: record
                            .descriptor
                            .skills
                            .into_iter()
                            .map(|skill| BotSkill {
                                name: skill.name,
                                description: skill.description,
                            })
                            .collect(),
                        scopes: record.descriptor.scopes,
                    },
                    reachability,
                    provider,
                    agent_code: record.agent_code,
                    task_claim_mode: record.task_claim_mode,
                    task_dream_mode: record.task_dream_mode,
                    user_visibility: record.user_visibility,
                    friend_ext: record.friend_ext,
                    friend_check_in_strategy: record.friend_check_in_strategy,
                    created_at: record.created_at,
                    updated_at: record.updated_at,
                    // The mine page stamps its sampled
                    // relation afterwards; other
                    // projections stay relation-less
                    // (spec §8 mine row only).
                    access_relation: None,
                }))
            })
            .collect()
    }

    async fn project_one(&self, record: BotControlPlaneView) -> Result<Bot, ApplicationError> {
        self.project_records(vec![record])
            .await?
            .into_iter()
            .next()
            .ok_or_else(|| ApplicationError::internal("Bot projection returned no record"))
    }

    /// The candidate query of §8.0, built once per
    /// attempt (the sampling and its backfill
    /// re-derivation share the same persistent shape).
    fn controllable_query(
        &self,
        staff_no: &str,
        command: &ListMyBots,
    ) -> ControllableCandidateQuery {
        ControllableCandidateQuery {
            user_id: staff_no.to_string(),
            env: self.config.env.clone(),
            kind: command.kind.map(|kind| match kind {
                BotKind::Bot => ActorKind::Bot,
                BotKind::Human => ActorKind::Human,
            }),
            name: normalize_optional_name(command.name.clone()),
            status: command.status.map(domain_status),
        }
    }

    /// ONE sampled mine attempt (bot-authority spec §8.0):
    /// 1. ONE persistent candidate snapshot (`list_controllable` —
    ///    the owner|manager union + the Human self entry, persistent
    ///    filtering only, no pagination);
    /// 2. ONE `try_runtime_activity_for` observation over ALL
    ///    candidates (failed reads propagate — never a fake
    ///    unreachable);
    /// 3. the reachability filter over ALL candidates (existing
    ///    semantics: `status = Online && (WS active || HTTP
    ///    Provider routable)`; a specified reachability keeps
    ///    excluding the Human self entry, per the pre-existing
    ///    behavior);
    /// 4. `total` AFTER the filter, then offset/limit;
    /// 5. the batch page backfill through the same Core, sharing
    ///    the observation from step 2 for the reachability
    ///    projection;
    /// 6. the page roles RE-DERIVED store-side (one fresh
    ///    `list_controllable`, no new WS observation and no
    ///    new provider batch): a page target that got deleted
    ///    OR whose owner/manager relation changed since the
    ///    sampling is page drift (spec §8.0 point 4 literal
    ///    删除或角色变化).
    async fn mine_attempt(
        &self,
        staff_no: &str,
        command: &ListMyBots,
    ) -> Result<MineAttempt, ApplicationError> {
        let candidates = self
            .control_plane
            .list_controllable(self.controllable_query(staff_no, command))
            .await
            .map_err(map_service_error)?;

        let candidate_ids = candidates
            .iter()
            .map(|candidate| candidate.id.clone())
            .collect::<Vec<_>>();
        let snapshot = self
            .registry
            .try_runtime_activity_for(&candidate_ids)
            .await
            .map_err(map_service_error)?;

        let filtered: Vec<ControlledBotCandidate> = candidates
            .into_iter()
            .filter(|candidate| match command.reachability {
                None => true,
                Some(requested) => {
                    // Any reachability filter keeps the
                    // Human self entry excluded (existing
                    // behavior); physical bots are compared
                    // against the ONE observation.
                    candidate.kind == ActorKind::Bot
                        && (requested == BotReachability::Reachable)
                            == candidate_is_reachable(candidate, &snapshot)
                }
            })
            .collect();
        // total BEFORE pagination, AFTER the reachability
        // filter — never the truncated-head assumption
        // (spec §8.0).
        let total = filtered.len() as u64;
        let offset = usize::try_from(command.offset).unwrap_or(usize::MAX);
        let limit = usize::try_from(command.limit).unwrap_or(usize::MAX);
        let page = filtered
            .iter()
            .skip(offset)
            .take(limit)
            .cloned()
            .collect::<Vec<_>>();
        let composition = page
            .iter()
            .map(|candidate: &ControlledBotCandidate| {
                (candidate.id.clone(), candidate.access_relation)
            })
            .collect::<Vec<_>>();
        // The mine page exposes the sampled relation per
        // item (spec §8 mine row: 逐项
        // `access_relation=owner|manager` — Human 自身
        // 条目保持 self 语义). The label comes from the
        // SAME candidates as the page; it is a display
        // fact, never an authorization result.
        let relations: HashMap<String, String> = page
            .iter()
            .map(|candidate| {
                (
                    candidate.id.clone(),
                    mine_relation_label(candidate),
                )
            })
            .collect();

        let page_ids = page
            .iter()
            .map(|candidate| candidate.id.clone())
            .collect::<Vec<_>>();
        let mut drift = false;
        let items = if page_ids.is_empty() {
            Vec::new()
        } else {
            // Batch page backfill; the provider
            // hydration inside `get_by_ids` stays
            // batched (no per-bot provider lookup).
            let views = self
                .control_plane
                .get_by_ids(&page_ids, &self.config.env)
                .await
                .map_err(map_service_error)?;
            let mut views_by_id = views
                .into_iter()
                .map(|view| (view.record.bot_id.clone(), view))
                .collect::<HashMap<_, _>>();
            // Store-side role re-derivation for the
            // page ids (spec §8.0 point 4: 补页发现
            // 目标删除或角色变化). ONE fresh candidate
            // snapshot — no new WS observation, no new
            // provider batch loop.
            let fresh = self
                .control_plane
                .list_controllable(self.controllable_query(staff_no, command))
                .await
                .map_err(map_service_error)?;
            let fresh_by_id = fresh
                .iter()
                .map(|candidate| (candidate.id.clone(), candidate.access_relation))
                .collect::<HashMap<_, _>>();
            drift = page
                .iter()
                .any(|candidate| {
                    !views_by_id.contains_key(&candidate.id)
                        || fresh_by_id
                            .get(&candidate.id)
                            .is_none_or(|fresh_relation| {
                                *fresh_relation != candidate.access_relation
                            })
                });
            let ordered = page_ids
                .iter()
                .filter_map(|bot_id| views_by_id.remove(bot_id))
                .collect::<Vec<_>>();
            self.project_records_with_activity(ordered, &snapshot.active_bot_ids)
                .await?
        };
        let items = items
            .into_iter()
            .map(|bot| {
                let relation = relations.get(bot.bot_id()).cloned();
                bot.with_mine_access_relation(relation)
            })
            .collect::<Vec<_>>();
        Ok(MineAttempt {
            items,
            total,
            drift,
            composition,
        })
    }

    /// Bot-authority plan Task 6.3 (row 1 of the mounted-entry
    /// table): a Human uses a Bot as its candidate perspective
    /// through the CURRENT control judgment — a live
    /// `can_manage` edge — never the historical `created_by`.
    /// The Human self entry keeps the `human_{staff_no}` rule.
    async fn authorize_candidate_perspective(
        &self,
        staff_no: &str,
        bot_id: &str,
    ) -> Result<BotControlPlaneRecord, ApplicationError> {
        let acting = self.load_record(bot_id).await?;
        match acting.kind {
            ActorKind::Bot => match self
                .authority()?
                .can_manage(staff_no, bot_id)
                .await
            {
                Ok(true) => Ok(acting),
                Ok(false) => Err(ApplicationError::forbidden(format!(
                    "Current Human cannot use Bot '{bot_id}' as the candidate perspective"
                ))),
                Err(error) => Err(map_authority_error(error)),
            },
            ActorKind::Human if acting.bot_id == format!("human_{staff_no}") => Ok(acting),
            _ => Err(ApplicationError::forbidden(format!(
                "Current Human cannot use Bot '{bot_id}' as the candidate perspective"
            ))),
        }
    }

    async fn list_candidates_with_friend_ids(
        &self,
        command: ListBotCandidates,
        acting: BotControlPlaneRecord,
        friend_ids: HashSet<String>,
    ) -> Result<Page<BotCandidate>, ApplicationError> {
        let (records, total) = self
            .control_plane
            .list_candidates(BotCandidateReadQuery {
                acting_bot_id: command.bot_id,
                env: acting.env,
                visibility: match command.purpose {
                    BotCandidatePurpose::Discovery => BotCandidateVisibility::Discovery,
                    BotCandidatePurpose::Collaboration => BotCandidateVisibility::Collaboration,
                },
                friend_ids,
                name: normalize_optional_name(command.name),
                offset: command.offset,
                limit: command.limit,
            })
            .await
            .map_err(map_service_error)?;
        let is_friend = records
            .iter()
            .map(|record| record.is_friend)
            .collect::<Vec<_>>();
        let bots = self
            .project_records(records.into_iter().map(|record| record.bot).collect())
            .await?;
        let mut items = Vec::with_capacity(bots.len());
        for (bot, is_friend) in bots.into_iter().zip(is_friend) {
            let Bot::Physical(bot) = bot else {
                return Err(ApplicationError::internal(
                    "Candidate store returned a Human record",
                ));
            };
            items.push(BotCandidate { bot, is_friend });
        }
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }
}

#[async_trait]
impl BotService for BotServiceImpl {
    async fn list_candidates(
        &self,
        command: ListBotCandidates,
    ) -> Result<Page<BotCandidate>, ApplicationError> {
        let staff_no = Self::human_staff_no(&command.caller)?;
        Self::validate_bot_id(&command.bot_id)?;
        Self::validate_pagination(command.offset, command.limit)?;
        let acting = self
            .authorize_candidate_perspective(&staff_no, &command.bot_id)
            .await?;

        let friend_ids = self
            .friends
            .list_friends(&command.bot_id)
            .await
            .into_iter()
            .collect();
        self.list_candidates_with_friend_ids(command, acting, friend_ids)
            .await
    }

    async fn list_eligible_candidates(
        &self,
        command: ListBotCandidates,
    ) -> Result<Page<BotCandidate>, ApplicationError> {
        let staff_no = Self::human_staff_no(&command.caller)?;
        Self::validate_bot_id(&command.bot_id)?;
        Self::validate_pagination(command.offset, command.limit)?;
        let acting = self
            .authorize_candidate_perspective(&staff_no, &command.bot_id)
            .await?;

        let friend_ids = self
            .connect_service
            .list_friends(&command.bot_id)
            .await
            .map_err(map_service_error)?
            .into_iter()
            .filter(|entry| entry.kind == ActorKind::Bot)
            .map(|entry| entry.actor_id)
            .collect();
        self.list_candidates_with_friend_ids(command, acting, friend_ids)
            .await
    }

    async fn search_candidates(
        &self,
        command: SearchBotCandidates,
    ) -> Result<BotCandidateSearchResult, ApplicationError> {
        const CANDIDATE_SEARCH_LIMIT: usize = 20;

        let staff_no = Self::human_staff_no(&command.caller)?;
        Self::validate_bot_id(&command.bot_id)?;
        let acting = self
            .authorize_candidate_perspective(&staff_no, &command.bot_id)
            .await?;
        let search = self
            .candidate_search
            .search_candidates(BotCandidateSearchQuery {
                query: command
                    .query
                    .as_deref()
                    .unwrap_or_default()
                    .trim()
                    .to_string(),
                acting_actor_id: command.bot_id,
                visibility: match command.purpose {
                    BotCandidatePurpose::Discovery => BotCandidateVisibility::Discovery,
                    BotCandidatePurpose::Collaboration => BotCandidateVisibility::Collaboration,
                },
                limit: CANDIDATE_SEARCH_LIMIT,
            })
            .await;
        let search_mode = search.mode;
        let searched_ids = search
            .hits
            .iter()
            .map(|hit| hit.bot.bot_uuid.clone())
            .collect::<Vec<_>>();
        let projected = self
            .project_records(
                self.control_plane
                    .get_by_ids(&searched_ids, &acting.env)
                    .await
                    .map_err(map_service_error)?,
            )
            .await?;
        let mut projected_by_id = projected
            .into_iter()
            .map(|bot| (bot.bot_id().to_string(), bot))
            .collect::<HashMap<_, _>>();
        let mut items = Vec::with_capacity(search.hits.len());
        for hit in search.hits {
            let Some(bot) = projected_by_id.remove(&hit.bot.bot_uuid) else {
                continue;
            };
            let Bot::Physical(bot) = bot else {
                return Err(ApplicationError::internal(
                    "Actor search returned a Human record",
                ));
            };
            let (score, short_profile) = if search_mode == CoreCandidateSearchMode::Semantic {
                (hit.score, hit.short_profile)
            } else {
                (None, None)
            };
            items.push(BotCandidateSearchItem {
                bot,
                is_friend: hit.is_friend,
                tags: hit.tags,
                score,
                short_profile,
            });
        }
        Ok(BotCandidateSearchResult {
            items,
            search_mode: match search_mode {
                CoreCandidateSearchMode::EmptyQuery => BotCandidateSearchMode::EmptyQuery,
                CoreCandidateSearchMode::Semantic => BotCandidateSearchMode::Semantic,
                CoreCandidateSearchMode::NameFallback => BotCandidateSearchMode::NameFallback,
            },
        })
    }

    async fn query(&self, command: QueryBots) -> Result<Vec<Bot>, ApplicationError> {
        Self::human_staff_no(&command.caller)?;
        if command.bot_ids.len() > 100
            || command
                .bot_ids
                .iter()
                .any(|bot_id| bot_id.trim().is_empty())
        {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "bot_ids must contain at most 100 non-empty identifiers",
            ));
        }
        let records = self
            .control_plane
            .get_by_ids(&command.bot_ids, &self.config.env)
            .await
            .map_err(map_service_error)?;
        self.project_records(records).await
    }

    async fn get(&self, query: GetBot) -> Result<Bot, ApplicationError> {
        Self::human_staff_no(&query.caller)?;
        Self::validate_bot_id(&query.bot_id)?;
        self.project_one(self.load_view(&query.bot_id).await?).await
    }

    async fn update(&self, command: UpdateBot) -> Result<Bot, ApplicationError> {
        let staff_no = Self::human_staff_no(&command.caller)?;
        Self::validate_bot_id(&command.bot_id)?;
        if command.patch.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "Bot patch must contain at least one mutable field",
            ));
        }
        if command
            .patch
            .descriptor
            .as_ref()
            .is_some_and(|descriptor| descriptor.is_empty())
        {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "descriptor patch must contain at least one field",
            ));
        }
        let record = self.load_record(&command.bot_id).await?;
        // Bot-authority plan Task 6.3 (row 2 of the mounted-entry
        // table): the descriptor patch is a §8 BUSINESS operation
        // ("Bot 配置 … 使用当前角色"), so the CURRENT ROLE gate
        // admits owner OR manager (`require_manage`, spec §1 goal
        // 1: manager 获得相同的 Bot 业务操作权限) — never the
        // historical `created_by` (spec §3.3: 旧创建者在失去
        // manager 后不再有特权). Only the ownership-transfer
        // commands stay owner-exclusive.
        //
        // Human-kind rows carry NO authority edges (spec §3.2.4
        // 管理边不把 Human 当 Bot): the gate narrows to ACTOR
        // SELF — the calling Human may patch only its OWN
        // `human_{staff_no}` row (the legacy created_by fact was
        // exactly that self row) — every other Human row is 403.
        match record.kind {
            ActorKind::Bot => {
                self.authority()?
                    .require_manage(&staff_no, &command.bot_id)
                    .await
                    .map_err(map_authority_error)?;
            }
            ActorKind::Human
                if record.bot_id == format!("human_{staff_no}") => {}
            ActorKind::Human => {
                return Err(ApplicationError::forbidden(format!(
                    "Current Human does not own Bot '{}'",
                    command.bot_id
                )));
            }
        }
        if record.kind == ActorKind::Human
            && (command.patch.descriptor.is_some()
                || command.patch.task_claim_mode.is_some()
                || command.patch.task_dream_mode.is_some())
        {
            return Err(ApplicationError::invalid(
                "invalid_bot_kind",
                "Human rows do not support descriptor or task-mode patches",
            ));
        }

        let name = command
            .patch
            .name
            .as_deref()
            .map(str::trim)
            .map(str::to_string);
        if name.as_deref().is_some_and(str::is_empty) {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "name must not be empty",
            ));
        }
        let descriptor = command
            .patch
            .descriptor
            .map(|descriptor| {
                let skills = descriptor.skills.map(|skills| {
                    skills
                        .into_iter()
                        .map(|skill| {
                            let name = skill.name.trim().to_string();
                            if name.is_empty() {
                                return Err(ApplicationError::invalid(
                                    "invalid_request",
                                    "descriptor skill name must not be empty",
                                ));
                            }
                            Ok(bcs_service_api::Skill {
                                name,
                                description: skill.description,
                            })
                        })
                        .collect::<Result<Vec<_>, _>>()
                });
                Ok(BotControlPlaneDescriptorPatch {
                    summary: descriptor.summary,
                    domains: descriptor.domains,
                    skills: skills.transpose()?,
                    scopes: descriptor.scopes,
                })
            })
            .transpose()?;
        let updated = self
            .control_plane
            .patch(
                &command.bot_id,
                &self.config.env,
                BotControlPlanePatch {
                    name,
                    visibility: command.patch.visibility.map(visibility_value),
                    status: command.patch.status.map(domain_status),
                    descriptor,
                    task_claim_mode: command.patch.task_claim_mode,
                    task_dream_mode: command.patch.task_dream_mode,
                    user_visibility: command.patch.user_visibility,
                    friend_ext: command.patch.friend_ext,
                    friend_check_in_strategy: command.patch.friend_check_in_strategy,
                    ..Default::default()
                },
            )
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "bot_not_found",
                    format!("Bot '{}' was not found", command.bot_id),
                )
            })?;
        self.project_one(updated).await
    }

    async fn list_mine(&self, command: ListMyBots) -> Result<Page<Bot>, ApplicationError> {
        let human = require_authenticated_user(&command.caller)?;
        Self::validate_pagination(command.offset, command.limit)?;
        let staff_no = human.id.clone();
        let display_name = Self::human_display_name(human);
        self.registry
            .ensure_human_actor(&staff_no, &display_name)
            .await
            .map_err(map_service_error)?;

        // Bot-authority spec §8.0 dynamic mine: persistent
        // candidates first, then ONE runtime observation,
        // then reachable/unreachable over ALL candidates,
        // total AFTER the filter, offset/limit, and the
        // batch page backfill (page fields, same sampling).
        let first = self
            .mine_attempt(&staff_no, &command)
            .await?;
        if !first.drift {
            return Ok(Page {
                items: first.items,
                total: first.total,
                offset: command.offset,
                limit: command.limit,
            });
        }

        // Page drift (a page target deleted or its role
        // changed since the sampling): retry the WHOLE
        // sampling once; a page that still does not
        // reproduce is `bot_list_changed` — never a
        // silently shifted page pretending to be exact
        // (spec §8.0 point 4).
        let second = self
            .mine_attempt(&staff_no, &command)
            .await?;
        if !second.drift && second.composition == first.composition {
            return Ok(Page {
                items: second.items,
                total: second.total,
                offset: command.offset,
                limit: command.limit,
            });
        }
        Err(ApplicationError::conflict(
            ERROR_BOT_LIST_CHANGED,
            "the controllable Bot list changed while the page \
             was sampled; re-request the page",
        ))
    }
}

/// One sampled mine attempt (spec §8.0): candidates → ONE
/// runtime observation → reachability over ALL candidates →
/// total → offset/limit → batch page backfill.
///
/// `drift` reports whether the page backfill discovered a
/// page target missing from the same store (deleted or
/// otherwise unresolvable since the candidate sampling).
struct MineAttempt {
    items: Vec<Bot>,
    total: u64,
    drift: bool,
    composition: Vec<(String, BotAccessRelation)>,
}

/// The public mine item label for one sampled candidate
/// (spec §8 mine row): physical Bots carry their edge
/// relation (`owner` | `manager`), and the Human self
/// entry keeps the `self` semantics — the edge-less
/// candidate's internal Owner DISPLAY tag never becomes a
/// public `owner` claim.
fn mine_relation_label(
    candidate: &ControlledBotCandidate,
) -> String {
    match candidate.kind {
        ActorKind::Human => "self".to_string(),
        ActorKind::Bot => match candidate.access_relation {
            BotAccessRelation::Owner => "owner".to_string(),
            BotAccessRelation::Manager => "manager".to_string(),
        },
    }
}

/// Existing reachability semantics of a candidate against the
/// ONE §8.0 observation: `status = Online && (WS active ||
/// HTTP Provider routable)`. The Human kind has no
/// reachability semantics and stays excluded under any
/// specified filter; the page total is computed from the
/// same sampling, never from a truncated head.
fn candidate_is_reachable(
    candidate: &ControlledBotCandidate,
    snapshot: &RuntimeActivitySnapshot,
) -> bool {
    candidate.kind == ActorKind::Bot
        && candidate.status == ActorStatus::Online
        && snapshot.active_bot_ids.contains(&candidate.id)
}

fn normalize_optional_name(name: Option<String>) -> Option<String> {
    name.map(|name| name.trim().to_string())
        .filter(|name| !name.is_empty())
}

fn project_visibility(value: &str) -> Result<BotVisibility, ApplicationError> {
    match value {
        "public" => Ok(BotVisibility::Public),
        "protected" => Ok(BotVisibility::Protected),
        "private" => Ok(BotVisibility::Private),
        other => Err(ApplicationError::internal(format!(
            "Bot has unsupported visibility '{other}'"
        ))),
    }
}

fn visibility_value(value: BotVisibility) -> String {
    match value {
        BotVisibility::Public => "public",
        BotVisibility::Protected => "protected",
        BotVisibility::Private => "private",
    }
    .to_string()
}

fn project_status(value: ActorStatus) -> BotStatus {
    match value {
        ActorStatus::Online => BotStatus::Online,
        ActorStatus::Hidden => BotStatus::Hidden,
    }
}

fn domain_status(value: BotStatus) -> ActorStatus {
    match value {
        BotStatus::Online => ActorStatus::Online,
        BotStatus::Hidden => ActorStatus::Hidden,
    }
}

fn map_service_error(error: ServiceError) -> ApplicationError {
    ApplicationError::internal(error.to_string())
}
