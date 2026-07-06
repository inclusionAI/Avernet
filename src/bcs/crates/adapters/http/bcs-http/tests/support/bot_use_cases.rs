#![allow(dead_code)]

use bcs_service_api::{
    BotConnectCommand, BotConnectResult, BotDetailCommand, BotDetailResult, BotLeaveCommand,
    BotLeaveResult, BotListCommand, BotListResult, BotManagementService, BotPagedListCommand,
    BotPagedListResult, BotQueryByIdsCommand, BotQueryByIdsResult, BotQueryService,
    BotStatusUpdateCommand, BotStatusUpdateResult, BotUseCaseError, BotVisibilityCommand,
    BotVisibilityQueryCommand, BotVisibilityQueryResult, BotVisibilityResult, MyBotsCommand,
    ServiceError, SwitchDeliveryToProviderCommand, SwitchDeliveryToProviderResult,
};
use tokio::sync::Mutex;

/// Recording bot query service for HTTP adapter contract tests.
pub struct RecordingBotQueryService {
    pub list_commands: Mutex<Vec<BotListCommand>>,
    pub detail_commands: Mutex<Vec<BotDetailCommand>>,
    pub visibility_commands: Mutex<Vec<BotVisibilityQueryCommand>>,
    pub paged_commands: Mutex<Vec<BotPagedListCommand>>,
    pub my_bots_commands: Mutex<Vec<MyBotsCommand>>,
    pub query_by_ids_commands: Mutex<Vec<BotQueryByIdsCommand>>,
    pub list_result: Result<BotListResult, BotUseCaseError>,
    pub detail_result: Result<BotDetailResult, BotUseCaseError>,
    pub visibility_result: Result<BotVisibilityQueryResult, BotUseCaseError>,
    pub paged_result: Result<BotPagedListResult, BotUseCaseError>,
    pub my_bots_result: Result<BotPagedListResult, BotUseCaseError>,
    pub query_by_ids_result: Result<BotQueryByIdsResult, BotUseCaseError>,
}

impl Default for RecordingBotQueryService {
    fn default() -> Self {
        Self {
            list_commands: Mutex::new(Vec::new()),
            detail_commands: Mutex::new(Vec::new()),
            visibility_commands: Mutex::new(Vec::new()),
            paged_commands: Mutex::new(Vec::new()),
            my_bots_commands: Mutex::new(Vec::new()),
            query_by_ids_commands: Mutex::new(Vec::new()),
            list_result: Err(not_configured(
                "RecordingBotQueryService::list_bots is not configured",
            )),
            detail_result: Err(not_configured(
                "RecordingBotQueryService::get_bot is not configured",
            )),
            visibility_result: Err(not_configured(
                "RecordingBotQueryService::get_visibility is not configured",
            )),
            paged_result: Err(not_configured(
                "RecordingBotQueryService::list_bots_paged is not configured",
            )),
            my_bots_result: Err(not_configured(
                "RecordingBotQueryService::list_my_bots is not configured",
            )),
            query_by_ids_result: Err(not_configured(
                "RecordingBotQueryService::query_bots_by_ids is not configured",
            )),
        }
    }
}

#[async_trait::async_trait]
impl BotQueryService for RecordingBotQueryService {
    async fn list_bots(&self, command: BotListCommand) -> Result<BotListResult, BotUseCaseError> {
        self.list_commands.lock().await.push(command);
        clone_result(&self.list_result)
    }

    async fn get_bot(&self, command: BotDetailCommand) -> Result<BotDetailResult, BotUseCaseError> {
        self.detail_commands.lock().await.push(command);
        clone_result(&self.detail_result)
    }

    async fn get_visibility(
        &self,
        command: BotVisibilityQueryCommand,
    ) -> Result<BotVisibilityQueryResult, BotUseCaseError> {
        self.visibility_commands.lock().await.push(command);
        clone_result(&self.visibility_result)
    }

    async fn list_bots_paged(
        &self,
        command: BotPagedListCommand,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        self.paged_commands.lock().await.push(command);
        clone_result(&self.paged_result)
    }

    async fn list_my_bots(
        &self,
        command: MyBotsCommand,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        self.my_bots_commands.lock().await.push(command);
        clone_result(&self.my_bots_result)
    }

    async fn query_bots_by_ids(
        &self,
        command: BotQueryByIdsCommand,
    ) -> Result<BotQueryByIdsResult, BotUseCaseError> {
        self.query_by_ids_commands.lock().await.push(command);
        clone_result(&self.query_by_ids_result)
    }
}

/// Recording bot management service for HTTP adapter contract tests.
pub struct RecordingBotManagementService {
    pub connect_commands: Mutex<Vec<BotConnectCommand>>,
    pub status_commands: Mutex<Vec<BotStatusUpdateCommand>>,
    pub visibility_commands: Mutex<Vec<BotVisibilityCommand>>,
    pub leave_commands: Mutex<Vec<BotLeaveCommand>>,
    pub switch_delivery_commands: Mutex<Vec<SwitchDeliveryToProviderCommand>>,
    pub connect_result: Result<BotConnectResult, BotUseCaseError>,
    pub status_result: Result<BotStatusUpdateResult, BotUseCaseError>,
    pub visibility_result: Result<BotVisibilityResult, BotUseCaseError>,
    pub leave_result: Result<BotLeaveResult, BotUseCaseError>,
    pub switch_delivery_result: Result<SwitchDeliveryToProviderResult, BotUseCaseError>,
}

impl Default for RecordingBotManagementService {
    fn default() -> Self {
        Self {
            connect_commands: Mutex::new(Vec::new()),
            status_commands: Mutex::new(Vec::new()),
            visibility_commands: Mutex::new(Vec::new()),
            leave_commands: Mutex::new(Vec::new()),
            switch_delivery_commands: Mutex::new(Vec::new()),
            connect_result: Err(not_configured(
                "RecordingBotManagementService::connect_bot is not configured",
            )),
            status_result: Err(not_configured(
                "RecordingBotManagementService::update_status is not configured",
            )),
            visibility_result: Err(not_configured(
                "RecordingBotManagementService::set_visibility is not configured",
            )),
            leave_result: Err(not_configured(
                "RecordingBotManagementService::leave_bot is not configured",
            )),
            switch_delivery_result: Err(not_configured(
                "RecordingBotManagementService::switch_delivery_to_provider is not configured",
            )),
        }
    }
}

#[async_trait::async_trait]
impl BotManagementService for RecordingBotManagementService {
    async fn connect_bot(
        &self,
        command: BotConnectCommand,
    ) -> Result<BotConnectResult, BotUseCaseError> {
        self.connect_commands.lock().await.push(command);
        clone_result(&self.connect_result)
    }

    async fn update_status(
        &self,
        command: BotStatusUpdateCommand,
    ) -> Result<BotStatusUpdateResult, BotUseCaseError> {
        self.status_commands.lock().await.push(command);
        clone_result(&self.status_result)
    }

    async fn set_visibility(
        &self,
        command: BotVisibilityCommand,
    ) -> Result<BotVisibilityResult, BotUseCaseError> {
        self.visibility_commands.lock().await.push(command);
        clone_result(&self.visibility_result)
    }

    async fn leave_bot(&self, command: BotLeaveCommand) -> Result<BotLeaveResult, BotUseCaseError> {
        self.leave_commands.lock().await.push(command);
        clone_result(&self.leave_result)
    }

    async fn switch_delivery_to_provider(
        &self,
        command: SwitchDeliveryToProviderCommand,
    ) -> Result<SwitchDeliveryToProviderResult, BotUseCaseError> {
        self.switch_delivery_commands.lock().await.push(command);
        clone_result(&self.switch_delivery_result)
    }
}

fn not_configured(message: &str) -> BotUseCaseError {
    BotUseCaseError::Service(ServiceError::InvalidOperation {
        message: message.to_string(),
        request_id: None,
    })
}

fn clone_result<T: Clone>(result: &Result<T, BotUseCaseError>) -> Result<T, BotUseCaseError> {
    match result {
        Ok(value) => Ok(value.clone()),
        Err(error) => Err(clone_bot_use_case_error(error)),
    }
}

fn clone_bot_use_case_error(error: &BotUseCaseError) -> BotUseCaseError {
    match error {
        BotUseCaseError::Unauthorized(message) => BotUseCaseError::Unauthorized(message.clone()),
        BotUseCaseError::Forbidden(message) => BotUseCaseError::Forbidden(message.clone()),
        BotUseCaseError::InvalidVisibility(value) => {
            BotUseCaseError::InvalidVisibility(value.clone())
        }
        BotUseCaseError::InvalidBotId(message) => BotUseCaseError::InvalidBotId(message.clone()),
        BotUseCaseError::InvalidProviderBotRef(message) => {
            BotUseCaseError::InvalidProviderBotRef(message.clone())
        }
        BotUseCaseError::ProviderNotFound(provider_id) => {
            BotUseCaseError::ProviderNotFound(provider_id.clone())
        }
        BotUseCaseError::ProviderNotReadyForDownlink { provider_id, reason } => {
            BotUseCaseError::ProviderNotReadyForDownlink {
                provider_id: provider_id.clone(),
                reason: reason.clone(),
            }
        }
        BotUseCaseError::BotAlreadyBound {
            bot_id,
            existing_provider_id,
            existing_provider_bot_ref,
        } => BotUseCaseError::BotAlreadyBound {
            bot_id: bot_id.clone(),
            existing_provider_id: existing_provider_id.clone(),
            existing_provider_bot_ref: existing_provider_bot_ref.clone(),
        },
        BotUseCaseError::Connect(error) => BotUseCaseError::Connect(error.clone()),
        BotUseCaseError::Service(error) => BotUseCaseError::Service(clone_service_error(error)),
    }
}

fn clone_service_error(error: &ServiceError) -> ServiceError {
    match error {
        ServiceError::BotNotFound(bot_id) => ServiceError::BotNotFound(bot_id.clone()),
        ServiceError::BotNotRegistered(bot_id) => ServiceError::BotNotRegistered(bot_id.clone()),
        ServiceError::BotNotConnected(bot_id) => ServiceError::BotNotConnected(bot_id.clone()),
        ServiceError::GroupNotFound(group_id) => ServiceError::GroupNotFound(group_id.clone()),
        ServiceError::ProposalNotFound(proposal_id) => {
            ServiceError::ProposalNotFound(proposal_id.clone())
        }
        ServiceError::ProviderNotFound(provider_id) => {
            ServiceError::ProviderNotFound(provider_id.clone())
        }
        ServiceError::ProviderNotReadyForDownlink { provider_id, reason } => {
            ServiceError::ProviderNotReadyForDownlink {
                provider_id: provider_id.clone(),
                reason: reason.clone(),
            }
        }
        ServiceError::BotAlreadyBound {
            bot_id,
            existing_provider_id,
            existing_provider_bot_ref,
        } => ServiceError::BotAlreadyBound {
            bot_id: bot_id.clone(),
            existing_provider_id: existing_provider_id.clone(),
            existing_provider_bot_ref: existing_provider_bot_ref.clone(),
        },
        ServiceError::InvalidOperation {
            message,
            request_id,
        } => ServiceError::InvalidOperation {
            message: message.clone(),
            request_id: request_id.clone(),
        },
        ServiceError::Conflict(message) => ServiceError::Conflict(message.clone()),
        ServiceError::Unauthorized(message) => ServiceError::Unauthorized(message.clone()),
        ServiceError::Forbidden(message) => ServiceError::Forbidden(message.clone()),
        ServiceError::BotHidden(id) => ServiceError::BotHidden(id.clone()),
        ServiceError::MessageLimitReached(message) => {
            ServiceError::MessageLimitReached(message.clone())
        }
        ServiceError::InternalError(message) => ServiceError::InternalError(message.clone()),
        ServiceError::CannotAddSelf => ServiceError::CannotAddSelf,
        ServiceError::PendingRequestExists {
            request_id,
            from_bot,
            to_bot,
        } => ServiceError::PendingRequestExists {
            request_id: request_id.clone(),
            from_bot: from_bot.clone(),
            to_bot: to_bot.clone(),
        },
        ServiceError::CannotAcceptRejected => ServiceError::CannotAcceptRejected,
        ServiceError::CannotRejectAccepted => ServiceError::CannotRejectAccepted,
        ServiceError::NotFriends(bot_ids) => ServiceError::NotFriends(bot_ids.clone()),
        ServiceError::FriendRequestNotFound(request_id) => {
            ServiceError::FriendRequestNotFound(request_id.clone())
        }
        ServiceError::PrivateBotCannotCollaborate => ServiceError::PrivateBotCannotCollaborate,
        ServiceError::IoError(error) => {
            ServiceError::IoError(std::io::Error::new(error.kind(), error.to_string()))
        }
        ServiceError::JsonError(error) => ServiceError::JsonError(serde_json::Error::io(
            std::io::Error::other(error.to_string()),
        )),
        ServiceError::SessionNotFound(id) => ServiceError::SessionNotFound(id.clone()),
        ServiceError::SessionInvalidParams(msg) => ServiceError::SessionInvalidParams(msg.clone()),
        ServiceError::SessionCallbackPending(id) => ServiceError::SessionCallbackPending(id.clone()),
        ServiceError::ParticipantNotFound(id) => ServiceError::ParticipantNotFound(id.clone()),
        ServiceError::ExistNonPublicBots { bots } => {
            ServiceError::ExistNonPublicBots {
                bots: bots.clone(),
            }
        }
    }
}
