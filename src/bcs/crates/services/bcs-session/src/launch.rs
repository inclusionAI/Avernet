//! Shared Session launch authorization and orchestration.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::port::repo::NewSessionParams;
use bcs_service_api::{
    ActorKind, AuthenticatedHumanCaller, BotRegistryCoreService, CollaborationRuntimeService,
    CreateOrReactivateCommand, CreateSessionLaunch, DeliveryType, Group, GroupCoreService,
    GroupStrategy, InitialSessionRun, InitialSessionRunActivityKind, InitialSessionRunState,
    Participant, ParticipantMode, ParticipantRole, ReactivateSessionLaunch, RequestedSessionRole,
    Session, SessionCaller, SessionKind, SessionLaunchError, SessionLaunchOutcome,
    SessionLaunchRequest, SessionLaunchService, SessionManagementService, SessionUseCaseError,
    StartStateMachineRunCommand, SystemMessageEvent, SystemMessageService, backfill_bot_names,
    resolve_session_topic,
};

pub struct SessionLaunchApplication {
    registry: Arc<dyn BotRegistryCoreService>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    runtime: Arc<dyn CollaborationRuntimeService>,
    system_message: Arc<dyn SystemMessageService>,
}

struct PreparedLaunch {
    group: Group,
    params: NewSessionParams,
    caller: SessionCaller,
    context_delivery: Option<DeliveryType>,
    deferred_after_create: Option<Participant>,
}

struct BuiltParticipants {
    initial: Vec<Participant>,
    deferred_after_create: Option<Participant>,
}

impl SessionLaunchApplication {
    pub fn new(
        registry: Arc<dyn BotRegistryCoreService>,
        groups: Arc<dyn GroupCoreService>,
        sessions: Arc<dyn SessionManagementService>,
        runtime: Arc<dyn CollaborationRuntimeService>,
        system_message: Arc<dyn SystemMessageService>,
    ) -> Self {
        Self {
            registry,
            groups,
            sessions,
            runtime,
            system_message,
        }
    }

    async fn load_group(&self, group_id: &str) -> Result<Group, SessionLaunchError> {
        self.groups
            .try_get(group_id)
            .await?
            .ok_or_else(|| SessionLaunchError::GroupNotFound(group_id.to_string()))
    }

    async fn human_has_group_access(
        &self,
        group: &Group,
        actor_id: &str,
        owner_id: &str,
    ) -> Result<bool, SessionLaunchError> {
        if group
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == actor_id)
        {
            return Ok(true);
        }
        let owned = self.registry.try_list_bots_by_creator(owner_id).await?;
        Ok(owned.iter().any(|bot| {
            group
                .participants
                .iter()
                .any(|participant| participant.bot_uuid == bot.bot_uuid)
        }))
    }

    async fn caller_has_group_access(
        &self,
        group: &Group,
        caller: &SessionCaller,
    ) -> Result<bool, SessionLaunchError> {
        if group.visibility == "public" {
            return Ok(true);
        }
        match caller {
            SessionCaller::Bot { bot_uuid } => Ok(group
                .participants
                .iter()
                .any(|participant| participant.bot_uuid == *bot_uuid)),
            SessionCaller::Human {
                actor_id, owner_id, ..
            } => self.human_has_group_access(group, actor_id, owner_id).await,
        }
    }

    async fn resolve_creator(
        &self,
        caller: &SessionCaller,
        requested_creator: Option<&str>,
    ) -> Result<String, SessionLaunchError> {
        let requester = caller.actor_id();
        let Some(target) = requested_creator else {
            return Ok(requester.to_string());
        };
        if target == requester {
            return Ok(target.to_string());
        }
        if target.starts_with("human_") {
            return Err(SessionLaunchError::Forbidden(format!(
                "caller is not authorized to create session as {target}"
            )));
        }
        let SessionCaller::Human { owner_id, .. } = caller else {
            return Err(SessionLaunchError::Forbidden(format!(
                "caller does not own bot {target}"
            )));
        };
        let owned = self
            .registry
            .try_get(target)
            .await?
            .and_then(|bot| bot.created_by)
            .is_some_and(|created_by| created_by == *owner_id);
        if !owned {
            return Err(SessionLaunchError::Forbidden(format!(
                "caller does not own bot {target}"
            )));
        }
        Ok(target.to_string())
    }

    async fn creator_has_group_access(
        &self,
        group: &Group,
        caller: &SessionCaller,
        creator: &str,
    ) -> Result<bool, SessionLaunchError> {
        if group.visibility == "public" {
            return Ok(true);
        }
        if creator.starts_with("human_") {
            let SessionCaller::Human { owner_id, .. } = caller else {
                return Ok(false);
            };
            return self.human_has_group_access(group, creator, owner_id).await;
        }
        Ok(group
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == creator))
    }

    fn resolve_kind(group: &Group, requested: Option<SessionKind>) -> SessionKind {
        requested.unwrap_or_else(|| {
            if group.group_strategy == GroupStrategy::StateMachine {
                SessionKind::ServiceInvocation
            } else {
                SessionKind::Chat
            }
        })
    }

    fn validate_public_creator_role(
        group: &Group,
        role: Option<&RequestedSessionRole>,
    ) -> Result<(), SessionLaunchError> {
        if group.visibility != "public" {
            return Ok(());
        }
        match role {
            Some(RequestedSessionRole::Known(ParticipantRole::Driver)) => {
                Err(SessionLaunchError::InvalidRole(
                    "Non-member actors cannot use the driver role".to_string(),
                ))
            }
            Some(RequestedSessionRole::Unknown(role)) => {
                Err(SessionLaunchError::InvalidRole(format!(
                    "Invalid caller_role '{role}': must be one of consultant, manager, worker, observer"
                )))
            }
            _ => Ok(()),
        }
    }

    async fn build_participants(
        &self,
        group: &Group,
        request: &SessionLaunchRequest,
        creator: &str,
        kind: SessionKind,
    ) -> Result<BuiltParticipants, SessionLaunchError> {
        let mut participants = group.participants.clone();
        for participant in &mut participants {
            if participant.mode.is_none() {
                participant.mode = Some(ParticipantMode::default_for(participant.actor_kind));
            }
        }

        if group.group_strategy == GroupStrategy::StateMachine
            && kind == SessionKind::ServiceInvocation
            && let SessionCaller::Human {
                actor_id,
                display_name,
                ..
            } = &request.caller
        {
            participants.retain(|participant| participant.bot_uuid != *actor_id);
            participants.push(Participant {
                bot_uuid: actor_id.clone(),
                bot_name: display_name.clone(),
                kind: None,
                role: ParticipantRole::Observer,
                actor_kind: ActorKind::Human,
                mode: Some(ParticipantMode::Present),
                tags: Vec::new(),
            });
        }

        if group.visibility == "public"
            && !participants
                .iter()
                .any(|participant| participant.bot_uuid == creator)
        {
            let is_human = creator.starts_with("human_");
            let bot_name = if is_human {
                request.caller.display_name().map(str::to_string)
            } else {
                self.registry
                    .try_get(creator)
                    .await?
                    .and_then(|bot| bot.capabilities.name)
            };
            participants.push(Participant {
                bot_uuid: creator.to_string(),
                bot_name,
                kind: None,
                role: request
                    .public_creator_role
                    .as_ref()
                    .and_then(|role| match role {
                        RequestedSessionRole::Known(role) => Some(*role),
                        RequestedSessionRole::Unknown(_) => None,
                    })
                    .unwrap_or(ParticipantRole::Consultant),
                actor_kind: if is_human {
                    ActorKind::Human
                } else {
                    ActorKind::Bot
                },
                mode: Some(if is_human {
                    ParticipantMode::Present
                } else {
                    ParticipantMode::Auto
                }),
                tags: Vec::new(),
            });
        }

        let explicit_human_creator = request
            .requested_creator
            .as_deref()
            .is_some_and(|requested| requested.starts_with("human_"));
        let deferred_after_create = (explicit_human_creator
            && !participants
                .iter()
                .any(|participant| participant.bot_uuid == creator)
        )
        .then(|| Participant {
            bot_uuid: creator.to_string(),
            bot_name: request.caller.display_name().map(str::to_string),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Human,
            mode: Some(ParticipantMode::Present),
            tags: Vec::new(),
        });

        Ok(BuiltParticipants {
            initial: participants,
            deferred_after_create,
        })
    }

    async fn prepare(
        &self,
        request: SessionLaunchRequest,
    ) -> Result<PreparedLaunch, SessionLaunchError> {
        let mut group = self.load_group(&request.group_id).await?;
        backfill_bot_names(self.registry.as_ref(), &mut group).await;

        if !self
            .caller_has_group_access(&group, &request.caller)
            .await?
        {
            return Err(SessionLaunchError::Forbidden(
                "caller is not a participant".to_string(),
            ));
        }

        Self::validate_public_creator_role(&group, request.public_creator_role.as_ref())?;

        if group.visibility == "public"
            && let SessionCaller::Human {
                owner_id,
                display_name,
                ..
            } = &request.caller
        {
            self.registry
                .ensure_human_actor(
                    owner_id,
                    display_name.as_deref().unwrap_or(owner_id.as_str()),
                )
                .await?;
        }

        let creator = self
            .resolve_creator(&request.caller, request.requested_creator.as_deref())
            .await?;
        if !self
            .creator_has_group_access(&group, &request.caller, &creator)
            .await?
        {
            let message = if creator.starts_with("human_") {
                format!("human {creator} does not own any bot in this group")
            } else {
                format!("bot {creator} is not a participant in this group")
            };
            return Err(SessionLaunchError::Forbidden(message));
        }

        let kind = Self::resolve_kind(&group, request.kind);
        let participants = self
            .build_participants(&group, &request, &creator, kind)
            .await?;
        let caller = request.caller.clone();
        let context_delivery = request.context_delivery;
        let params = NewSessionParams {
            session_kind: kind,
            participants: participants.initial,
            group_version: Some(group.version),
            caller_principal: Some(request.caller.actor_id().to_string()),
            input: request.input,
            created_by: Some(creator),
            session_title: request.title,
            meta: request.meta,
            ..Default::default()
        };
        Ok(PreparedLaunch {
            group,
            params,
            caller,
            context_delivery,
            deferred_after_create: participants.deferred_after_create,
        })
    }

    async fn finish_launch(
        &self,
        prepared: PreparedLaunch,
        session: Session,
        created: bool,
        emit_session_context: bool,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError> {
        if prepared.group.group_strategy == GroupStrategy::StateMachine
            && session.session_kind == SessionKind::ServiceInvocation
        {
            let authenticated_human = match &prepared.caller {
                SessionCaller::Human {
                    actor_id,
                    display_name,
                    ..
                } => Some(AuthenticatedHumanCaller {
                    actor_id: actor_id.clone(),
                    display_name: display_name.clone(),
                }),
                SessionCaller::Bot { .. } => None,
            };
            let run = self
                .runtime
                .start_state_machine_run(StartStateMachineRunCommand {
                    group_id: prepared.group.id.clone(),
                    session_id: Some(session.id.clone()),
                    definition_yaml: None,
                    definition: None,
                    definition_ref: None,
                    participant_bindings: None,
                    opening_message_override: None,
                    input: session.input.clone().unwrap_or(serde_json::Value::Null),
                    caller_id: Some(prepared.caller.actor_id().to_string()),
                    authenticated_human,
                })
                .await?;
            return Ok(SessionLaunchOutcome {
                session,
                created,
                state_machine_run: Some(run.view),
                initial_run: None,
            });
        }

        let initial_run = if emit_session_context {
            let group_id = prepared.group.id.clone();
            let session_id = session.id.clone();
            let session_input = session.input.clone();
            let participants = session.participants.clone();
            let reason = resolve_session_topic(
                session_input.as_ref(),
                prepared.group.context.as_deref(),
                prepared.group.label.as_deref(),
            )
            .unwrap_or_default();
            match self
                .system_message
                .notify_with_outcome(
                    &group_id,
                    SystemMessageEvent::SessionContext {
                        group_id: group_id.clone(),
                        session_id: session_id.clone(),
                        reason,
                        session_input,
                        task_ledger: None,
                        driver_delivery: prepared.context_delivery,
                    },
                    &session_id,
                    &participants,
                )
                .await
            {
                Ok(dispatch) => dispatch
                    .recipient_results
                    .iter()
                    .find(|result| result.delivery_type == DeliveryType::Send)
                    .map(|result| InitialSessionRun {
                        run_id: result.run_id.clone(),
                        bot_uuid: result.recipient_id.clone(),
                        activity_kind: InitialSessionRunActivityKind::SessionContext,
                        state: if result.delivered {
                            InitialSessionRunState::Running
                        } else {
                            InitialSessionRunState::Failed
                        },
                        started_at: chrono::Utc::now().to_rfc3339(),
                    }),
                Err(error) => {
                    tracing::warn!(
                        group_id = %group_id,
                        session_id = %session_id,
                        error = %error,
                        "failed to deliver new Session context"
                    );
                    None
                }
            }
        } else {
            None
        };

        Ok(SessionLaunchOutcome {
            session,
            created,
            state_machine_run: None,
            initial_run,
        })
    }
}

fn map_session_error(error: SessionUseCaseError) -> SessionLaunchError {
    match error {
        SessionUseCaseError::NotFound(message) => SessionLaunchError::SessionNotFound(message),
        SessionUseCaseError::InvalidParams(message) => SessionLaunchError::InvalidRequest(message),
        SessionUseCaseError::CallbackPending(message) => {
            SessionLaunchError::CallbackPending(message)
        }
        SessionUseCaseError::Conflict(message) => SessionLaunchError::Conflict(message),
        SessionUseCaseError::Internal(error) => SessionLaunchError::Internal(error),
    }
}

#[async_trait]
impl SessionLaunchService for SessionLaunchApplication {
    async fn create(
        &self,
        command: CreateSessionLaunch,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError> {
        let group_id = command.request.group_id.clone();
        let prepared = self.prepare(command.request).await?;
        let outcome = self
            .sessions
            .create_or_reactivate(CreateOrReactivateCommand {
                group_id,
                session_id: None,
                params: prepared.params.clone(),
            })
            .await
            .map_err(map_session_error)?;
        let created = outcome.created;
        let mut session = outcome.session;
        if created
            && let Some(participant) = prepared.deferred_after_create.clone()
        {
            session = self
                .sessions
                .add_participant(&session.id, participant)
                .await
                .map_err(map_session_error)?;
        }
        self.finish_launch(prepared, session, created, created)
            .await
    }

    async fn reactivate(
        &self,
        command: ReactivateSessionLaunch,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError> {
        let group_id = command.request.group_id.clone();
        let prepared = self.prepare(command.request).await?;
        let belongs = self
            .sessions
            .belongs_to_group(&command.session_id, &group_id)
            .await
            .map_err(map_session_error)?;
        if !belongs {
            return Err(SessionLaunchError::SessionNotFound(command.session_id));
        }
        let outcome = self
            .sessions
            .create_or_reactivate(CreateOrReactivateCommand {
                group_id,
                session_id: Some(command.session_id),
                params: prepared.params.clone(),
            })
            .await
            .map_err(map_session_error)?;
        let created = outcome.created;
        self.finish_launch(prepared, outcome.session, created, false)
            .await
    }
}
