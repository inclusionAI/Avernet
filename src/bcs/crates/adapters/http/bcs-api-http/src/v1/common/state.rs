use std::sync::Arc;

use bcs_service_api::application::v1::{
    BotService, CollaborationTemplateService, EventSubscriptionService, FriendshipService, GroupService, InvitationService,
    SessionFileApplicationService, SessionMessageService, SessionService,
};
use bcs_service_api::application::channel::ChannelService;

use crate::v1::openapi::SessionFileUrlProjector;

use super::PrincipalVerifier;

pub trait PrincipalVerificationState: Clone + Send + Sync + 'static {
    fn principal_verifier(&self) -> &Arc<dyn PrincipalVerifier>;
}

#[derive(Clone)]
pub struct ApiState {
    pub bot_service: Option<Arc<dyn BotService>>,
    pub event_subscription_service: Option<Arc<dyn EventSubscriptionService>>,
    pub group_service: Arc<dyn GroupService>,
    pub session_service: Arc<dyn SessionService>,
    pub message_service: Arc<dyn SessionMessageService>,
    pub invitation_service: Arc<dyn InvitationService>,
    pub friendship_service: Arc<dyn FriendshipService>,
    pub channel_service: Option<Arc<dyn ChannelService>>,
    pub session_file_service: Option<Arc<dyn SessionFileApplicationService>>,
    pub session_file_url_projector: Option<SessionFileUrlProjector>,
    pub collaboration_template_service: Option<Arc<dyn CollaborationTemplateService>>,
    pub principal_verifier: Arc<dyn PrincipalVerifier>,
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
            event_subscription_service: None,
            group_service,
            session_service,
            message_service,
            invitation_service,
            friendship_service,
            channel_service: None,
            session_file_service: None,
            session_file_url_projector: None,
            collaboration_template_service: None,
            principal_verifier,
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

    /// Add the shared Channel application service. Channels reuse the legacy
    /// `ChannelService` impl; permission stays in the adapter, not the app.
    /// Fail-closed (handler returns `internal` if None) until bootstrap mounts it.
    pub fn with_channel_service(mut self, service: Arc<dyn ChannelService>) -> Self {
        self.channel_service = Some(service);
        self
    }

    pub fn with_event_subscription_service(
        mut self,
        service: Arc<dyn EventSubscriptionService>,
    ) -> Self {
        self.event_subscription_service = Some(service);
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

    /// Add the V1 collaboration-template catalog facade. Catalog reads are
    /// read-only and not scoped to a Bot or Session; permission stays on the
    /// protected boundary. Fail-closed (handler returns `internal` if None)
    /// until bootstrap mounts it.
    pub fn with_collaboration_template_service(
        mut self,
        service: Arc<dyn CollaborationTemplateService>,
    ) -> Self {
        self.collaboration_template_service = Some(service);
        self
    }
}

impl PrincipalVerificationState for ApiState {
    fn principal_verifier(&self) -> &Arc<dyn PrincipalVerifier> {
        &self.principal_verifier
    }
}
