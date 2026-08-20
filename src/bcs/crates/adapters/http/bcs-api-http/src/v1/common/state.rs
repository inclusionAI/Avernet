use std::sync::Arc;

use bcs_service_api::application::v1::{
    BotService, FriendshipService, GroupService, InternalBotAttributesService, InvitationService,
    SessionFileApplicationService, SessionMessageService, SessionService,
};

use crate::v1::openapi::SessionFileUrlProjector;

use super::PrincipalVerifier;

pub trait PrincipalVerificationState: Clone + Send + Sync + 'static {
    fn principal_verifier(&self) -> &Arc<dyn PrincipalVerifier>;
}

#[derive(Clone)]
pub struct ApiState {
    pub bot_service: Option<Arc<dyn BotService>>,
    pub group_service: Arc<dyn GroupService>,
    pub session_service: Arc<dyn SessionService>,
    pub message_service: Arc<dyn SessionMessageService>,
    pub invitation_service: Arc<dyn InvitationService>,
    pub friendship_service: Arc<dyn FriendshipService>,
    pub session_file_service: Option<Arc<dyn SessionFileApplicationService>>,
    pub session_file_url_projector: Option<SessionFileUrlProjector>,
    pub principal_verifier: Arc<dyn PrincipalVerifier>,
    pub(crate) internal_bot_attributes_service: Option<Arc<dyn InternalBotAttributesService>>,
}

impl ApiState {
    pub fn new(
        group_service: Arc<dyn GroupService>,
        session_service: Arc<dyn SessionService>,
        message_service: Arc<dyn SessionMessageService>,
        invitation_service: Arc<dyn InvitationService>,
        friendship_service: Arc<dyn FriendshipService>,
        principal_verifier: Arc<dyn PrincipalVerifier>,
    ) -> Self {
        Self {
            bot_service: None,
            group_service,
            session_service,
            message_service,
            invitation_service,
            friendship_service,
            session_file_service: None,
            session_file_url_projector: None,
            principal_verifier,
            internal_bot_attributes_service: None,
        }
    }

    /// Add the Bot control-plane V1 slice.
    ///
    /// The service remains optional until the production trusted-Principal
    /// rollout mounts this adapter in the bootstrap composition root.
    pub fn with_bot_service(mut self, bot_service: Arc<dyn BotService>) -> Self {
        self.bot_service = Some(bot_service);
        self
    }

    pub fn with_session_file_service(
        mut self,
        service: Arc<dyn SessionFileApplicationService>,
        url_projector: SessionFileUrlProjector,
    ) -> Self {
        self.session_file_service = Some(service);
        self.session_file_url_projector = Some(url_projector);
        self
    }

    /// Retain the shared Bot-attributes application service for the legacy HTTP
    /// adapter, which owns the Provider-scoped transport route.
    pub fn with_internal_bot_attributes_service(
        mut self,
        service: Arc<dyn InternalBotAttributesService>,
    ) -> Self {
        self.internal_bot_attributes_service = Some(service);
        self
    }

    pub fn internal_bot_attributes_service(
        &self,
    ) -> Option<Arc<dyn InternalBotAttributesService>> {
        self.internal_bot_attributes_service.clone()
    }
}

impl PrincipalVerificationState for ApiState {
    fn principal_verifier(&self) -> &Arc<dyn PrincipalVerifier> {
        &self.principal_verifier
    }
}
