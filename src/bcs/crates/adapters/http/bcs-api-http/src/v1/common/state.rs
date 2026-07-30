use std::sync::Arc;

use bcs_service_api::application::v1::{
    FriendshipService, GroupService, InvitationService, SessionMessageService, SessionService,
};

use super::PrincipalVerifier;

#[derive(Clone)]
pub struct ApiState {
    pub group_service: Arc<dyn GroupService>,
    pub session_service: Arc<dyn SessionService>,
    pub message_service: Arc<dyn SessionMessageService>,
    pub invitation_service: Arc<dyn InvitationService>,
    pub friendship_service: Arc<dyn FriendshipService>,
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
            group_service,
            session_service,
            message_service,
            invitation_service,
            friendship_service,
            principal_verifier,
        }
    }
}
