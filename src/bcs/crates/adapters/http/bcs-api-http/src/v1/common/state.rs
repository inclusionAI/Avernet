use std::sync::Arc;

use bcs_config_api::ManifestConfig;
use bcs_service_api::application::v1::{
    AuthService as ApplicationAuthService, BotService, CollaborationDefinitionService, CollaborationTemplateService, EventSubscriptionService, FriendConnectionService, FriendshipService, GroupService, InvitationService, RegisterService,
    SessionFileApplicationService, SessionMessageService, SessionService,
};
use bcs_service_api::application::channel::ChannelService;
use bcs_service_api::application::CollaborationRuntimeService;

use crate::v1::openapi::SessionFileUrlProjector;

use super::PrincipalVerifier;

pub trait PrincipalVerificationState: Clone + Send + Sync + 'static {
    fn principal_verifier(&self) -> &Arc<dyn PrincipalVerifier>;
}

#[derive(Clone)]
pub struct ApiState {
    pub auth_service: Option<Arc<dyn ApplicationAuthService>>,
    pub auth_public_base_url: String,
    pub bot_service: Option<Arc<dyn BotService>>,
    pub event_subscription_service: Option<Arc<dyn EventSubscriptionService>>,
    pub group_service: Arc<dyn GroupService>,
    pub session_service: Arc<dyn SessionService>,
    pub message_service: Arc<dyn SessionMessageService>,
    pub invitation_service: Arc<dyn InvitationService>,
    pub register_service: Arc<dyn RegisterService>,
    pub friendship_service: Arc<dyn FriendshipService>,
    pub friend_connection_service: Option<Arc<dyn FriendConnectionService>>,
    pub channel_service: Option<Arc<dyn ChannelService>>,
    pub session_file_service: Option<Arc<dyn SessionFileApplicationService>>,
    pub session_file_url_projector: Option<SessionFileUrlProjector>,
    pub collaboration_template_service: Option<Arc<dyn CollaborationTemplateService>>,
    pub collaboration_definition_service: Option<Arc<dyn CollaborationDefinitionService>>,
    pub collaboration_runtime_service: Option<Arc<dyn CollaborationRuntimeService>>,
    pub manifest: ManifestConfig,
    pub manifest_env: String,
    pub principal_verifier: Arc<dyn PrincipalVerifier>,
}

impl ApiState {
    pub fn new(
        group_service: Arc<dyn GroupService>,
        session_service: Arc<dyn SessionService>,
        message_service: Arc<dyn SessionMessageService>,
        invitation_service: Arc<dyn InvitationService>,
        register_service: Arc<dyn RegisterService>,
        friendship_service: Arc<dyn FriendshipService>,
        principal_verifier: Arc<dyn PrincipalVerifier>,
    ) -> Self {
        Self {
            auth_service: None,
            auth_public_base_url: "http://127.0.0.1/openapi/v1/auth".to_string(),
            bot_service: None,
            event_subscription_service: None,
            group_service,
            session_service,
            message_service,
            invitation_service,
            register_service,
            friendship_service,
            friend_connection_service: None,
            channel_service: None,
            session_file_service: None,
            session_file_url_projector: None,
            collaboration_template_service: None,
            collaboration_definition_service: None,
            collaboration_runtime_service: None,
            manifest: ManifestConfig::default(),
            manifest_env: "local".to_string(),
            principal_verifier,
        }
    }

    pub fn with_auth_service(
        mut self,
        service: Arc<dyn ApplicationAuthService>,
        public_base_url: String,
    ) -> Self {
        self.auth_service = Some(service);
        self.auth_public_base_url = public_base_url;
        self
    }

    /// Add the Bot control-plane V1 slice.
    ///
    /// The service remains optional until the production trusted-Principal
    /// rollout mounts this adapter in the bootstrap composition root.
    pub fn with_bot_service(mut self, bot_service: Arc<dyn BotService>) -> Self {
        self.bot_service = Some(bot_service);
        self
    }

    pub fn with_friend_connection_service(
        mut self,
        service: Arc<dyn FriendConnectionService>,
    ) -> Self {
        self.friend_connection_service = Some(service);
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

    /// Add the V1 collaboration-definition validation facade. Validation is a
    /// read-only compile/validate operation, not scoped to a Bot or Session;
    /// permission stays on the protected boundary. Fail-closed (handler returns
    /// `internal` if None) until bootstrap mounts it.
    pub fn with_collaboration_definition_service(
        mut self,
        service: Arc<dyn CollaborationDefinitionService>,
    ) -> Self {
        self.collaboration_definition_service = Some(service);
        self
    }

    /// Add the legacy CollaborationRuntime service for the v1 state-machine-run
    /// endpoints. Auth is performed in the HTTP layer; this service is reused
    /// verbatim. Fail-closed (handler returns `internal` if None) until
    /// bootstrap mounts it.
    pub fn with_collaboration_runtime_service(
        mut self,
        service: Arc<dyn CollaborationRuntimeService>,
    ) -> Self {
        self.collaboration_runtime_service = Some(service);
        self
    }

    /// Provide the bundle manifest config served by the public v1
    /// `/api/v1/collaboration/manifest` route.
    pub fn with_manifest_config(mut self, env: String, manifest: ManifestConfig) -> Self {
        self.manifest_env = env;
        self.manifest = manifest;
        self
    }
}

impl PrincipalVerificationState for ApiState {
    fn principal_verifier(&self) -> &Arc<dyn PrincipalVerifier> {
        &self.principal_verifier
    }
}
