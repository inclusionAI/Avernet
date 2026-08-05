//! Versioned Bot control-plane application facade for the BCN V1 API.

use std::collections::HashSet;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ApplicationError, Bot, BotCandidate, BotCandidatePurpose, BotDescriptor, BotKind, BotProvider,
    BotReachability, BotService, BotSkill, BotStatus, BotVisibility, GetBot, HumanBot,
    ListBotCandidates, ListMyBots, Page, PhysicalBot, QueryBots, UpdateBot,
    require_authenticated_user,
};
use bcs_service_api::{
    ActorKind, ActorStatus, BotCandidateReadQuery, BotCandidateVisibility,
    BotControlPlaneCoreService, BotControlPlaneDescriptorPatch, BotControlPlaneOwnedQuery,
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
    config: BotServiceConfig,
}

impl BotServiceImpl {
    pub fn new(
        control_plane: Arc<dyn BotControlPlaneCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        friends: Arc<dyn FriendCoreService>,
        config: BotServiceConfig,
    ) -> Self {
        Self {
            control_plane,
            registry,
            friends,
            config,
        }
    }

    fn human_staff_no(
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
    ) -> Result<String, ApplicationError> {
        Ok(require_authenticated_user(caller)?.id.clone())
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
        let acting = self.load_record(&command.bot_id).await?;
        if acting.kind != ActorKind::Bot {
            return Err(ApplicationError::invalid(
                "invalid_bot_kind",
                "Candidate search requires a physical acting Bot",
            ));
        }
        if acting.created_by.as_deref() != Some(staff_no.as_str()) {
            return Err(ApplicationError::forbidden(format!(
                "Current Human does not manage Bot '{}'",
                command.bot_id
            )));
        }

        let friend_ids = self
            .friends
            .list_friends(&command.bot_id)
            .await
            .into_iter()
            .collect();
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
        self.project_one(self.load_view(&query.bot_id).await?)
            .await
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
        if record.created_by.as_deref() != Some(staff_no.as_str()) {
            return Err(ApplicationError::forbidden(format!(
                "Current Human does not own Bot '{}'",
                command.bot_id
            )));
        }
        if record.kind == ActorKind::Human && command.patch.descriptor.is_some() {
            return Err(ApplicationError::invalid(
                "invalid_bot_kind",
                "Human rows do not have a descriptor",
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
        let staff_no = Self::human_staff_no(&command.caller)?;
        Self::validate_pagination(command.offset, command.limit)?;
        let records = self
            .control_plane
            .list_by_creator(BotControlPlaneOwnedQuery {
                created_by: staff_no,
                env: self.config.env.clone(),
                kind: command.kind.map(|kind| match kind {
                    BotKind::Bot => ActorKind::Bot,
                    BotKind::Human => ActorKind::Human,
                }),
                name: normalize_optional_name(command.name),
                status: command.status.map(domain_status),
            })
            .await
            .map_err(map_service_error)?;
        let mut bots = self.project_records(records).await?;
        if let Some(reachability) = command.reachability {
            bots.retain(|bot| {
                matches!(bot, Bot::Physical(physical) if physical.reachability == reachability)
            });
        }
        let total = bots.len() as u64;
        let offset = usize::try_from(command.offset).unwrap_or(usize::MAX);
        let limit = usize::try_from(command.limit).unwrap_or(usize::MAX);
        let items = bots.into_iter().skip(offset).take(limit).collect();
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
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
    ApplicationError::internal(error.to_string())
}
