//! Versioned Bot control-plane application facade for the BCN V1 API.

mod authority;
mod bot_self;
mod management;
mod mine;
mod team_sync;
pub use authority::BotAuthorityHookImpl;
pub use bot_self::BotSelfServiceImpl;
pub use management::BotManagerServiceImpl;
pub use team_sync::{TeamManagerSyncServiceConfig, TeamManagerSyncServiceImpl};

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedUserIdentity, Bot, BotCandidate, BotCandidatePurpose,
    BotCandidateSearchItem, BotCandidateSearchMode, BotCandidateSearchResult, BotDescriptor,
    BotKind, BotProvider, BotReachability, BotService, BotSkill, BotStatus, BotVisibility, GetBot,
    HumanBot, InternalBotAttributesService, ListBotCandidates, ListMyBots, MyBot, Page,
    PatchBotInternalAttributes, PhysicalBot, QueryBots, SearchBotCandidates, UpdateBot,
    require_authenticated_user,
};
use bcs_service_api::application::ConnectService;
use bcs_service_api::application::v1::BotAuthorityHook;
use bcs_service_api::port::repo::bot_authority::human_actor_id;
use bcs_service_api::types::BotOperationActor;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCandidateReadQuery, BotCandidateSearchCoreService,
    BotCandidateSearchMode as CoreCandidateSearchMode, BotCandidateSearchQuery,
    BotCandidateVisibility, BotControlPlaneCoreService, BotControlPlaneDescriptorPatch,
    BotControlPlanePatch, BotControlPlaneRecord, BotControlPlaneView, BotRegistryCoreService,
    FriendCoreService, ServiceError,
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
    authority: Arc<dyn BotAuthorityHook>,
    config: BotServiceConfig,
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
                // The internal Bot-attributes lane is a machine interface
                // with no verified Human: the System branch records that
                // honestly ("no Human was involved") — never a fabricated
                // Human and never a silently skipped audit (spec §12.5).
                bot_internal_attributes_operation(&command.bot_id),
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
        authority: Arc<dyn BotAuthorityHook>,
        config: BotServiceConfig,
    ) -> Self {
        Self {
            control_plane,
            registry,
            friends,
            connect_service,
            candidate_search,
            authority,
            config,
        }
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

    async fn authorize_candidate_perspective(
        &self,
        staff_no: &str,
        bot_id: &str,
    ) -> Result<BotControlPlaneRecord, ApplicationError> {
        let acting = self.load_record(bot_id).await?;
        match acting.kind {
            // The current Human's own Human actor row stays a valid
            // perspective (self identity compatibility); other Humans
            // never become one.
            ActorKind::Human => {
                if acting.bot_id == human_actor_id(staff_no) {
                    Ok(acting)
                } else {
                    Err(ApplicationError::forbidden(format!(
                        "Current Human cannot use Bot '{bot_id}' as the candidate perspective"
                    )))
                }
            }
            // Physical Bot perspectives follow the CURRENT authority edges
            // (spec §12.4 cutover): created_by is creation provenance,
            // never a permission. Strict-read failures (uninitialized or
            // corrupt authority) propagate with their typed branches.
            ActorKind::Bot => {
                if self
                    .authority
                    .can_manage(staff_no, bot_id)
                    .await
                    .map_err(map_service_error)?
                {
                    Ok(acting)
                } else {
                    Err(ApplicationError::forbidden(format!(
                        "Current Human cannot use Bot '{bot_id}' as the candidate perspective"
                    )))
                }
            }
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
        // Control-plane cutover (spec §12.4): authority comes from the
        // CURRENT owner/manager edges through the hook — `created_by` is
        // creation provenance and never a permission. The caller's own
        // Human row stays patchable as explicit self identity
        // (compatibility projection); every other Human row is denied.
        let authorized = match record.kind {
            ActorKind::Human => record.bot_id == human_actor_id(&staff_no),
            ActorKind::Bot => self
                .authority
                .can_manage(&staff_no, &command.bot_id)
                .await
                .map_err(map_service_error)?,
        };
        if !authorized {
            return Err(ApplicationError::forbidden(format!(
                "Current Human does not control Bot '{}'",
                command.bot_id
            )));
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
        // REQUIRED audit identity (spec §12.5): built HERE after
        // authentication and authorization — the store commits the
        // UPDATE and its `update/bot/applied` audit row together; the
        // operator never comes from a transport request body.
        let operation = bot_patch_operation(
            &staff_no,
            &command.bot_id,
        );
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
                operation,
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

    async fn list_mine(&self, command: ListMyBots) -> Result<Page<MyBot>, ApplicationError> {
        // The union-projection use case lives in `mine.rs` (plan Task 9):
        // owner ∪ manager via the controllable read, one item per Bot
        // with the REQUIRED access_relation label, plus the Human self
        // row as an explicit `Owner`-labeled compatibility projection.
        self.list_mine_impl(command).await
    }
}

/// REQUIRED audit identity of one Human-issued Bot PATCH (spec §12.5).
/// `effective_actor_id` is the Bot the authorization selected for the
/// operation; the trusted operator stays the Human.
fn bot_patch_operation(staff_no: &str, bot_id: &str) -> bcs_service_api::types::BotOperationContext {
    bcs_service_api::types::BotOperationContext {
        operation_id: uuid::Uuid::new_v4().to_string(),
        actor: BotOperationActor::Human {
            user_id: staff_no.to_string(),
            effective_actor_id: bot_id.to_string(),
        },
    }
}

/// Audit identity of the internal Bot-attributes lane: a machine
/// interface with no verified Human — the System branch records "no
/// Human was involved" honestly; the selected effective actor is the
/// patched Bot.
fn bot_internal_attributes_operation(bot_id: &str) -> bcs_service_api::types::BotOperationContext {
    bcs_service_api::types::BotOperationContext {
        operation_id: uuid::Uuid::new_v4().to_string(),
        actor: BotOperationActor::System {
            system_id: "internal_bot_attributes_api".to_string(),
            effective_actor_id: bot_id.to_string(),
        },
    }
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
    match error {
        // Strict authority branches surface their fixed spec codes
        // (ownership_not_initialized / corrupt_authority / forbidden …) —
        // they are business failures, not storage noise.
        ServiceError::Authority(authority) => ApplicationError::authority(authority),
        ServiceError::BotNotFound(bot_id) => ApplicationError::not_found(
            "bot_not_found",
            format!("Bot '{bot_id}' was not found"),
        ),
        other => ApplicationError::internal(other.to_string()),
    }
}
