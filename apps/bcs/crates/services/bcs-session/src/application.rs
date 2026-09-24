//! SessionManagementService 实现：薄编排，逻辑下沉到 core + repo。

use std::collections::BTreeMap;

use std::sync::Arc;

use std::time::Duration;


use async_trait::async_trait;

use bcs_domain::{
    BCS_SESSION_OPENING_MESSAGE_SENDER, BCS_SESSION_OPENING_MESSAGE_SENDER_NAME, MessageAudience,
    MessageVisibilityDomain, NewMessage, OpeningMessageRenderContext, SESSION_OPENING_MESSAGE_TYPE,
    SenderType,
};


use bcs_service_api::application::session::{
    ClaimSessionCallbackCommand, ClaimSessionCallbackOutcome, CompleteSessionCallbackCommand,
    CreateOrReactivateCommand, CreateOrReactivateOutcome, SessionManagementService,
    SessionUseCaseError,
};

use bcs_service_api::core::session::new_session_id;

use bcs_service_api::port::repo::{
    AddSessionParticipantWithEvent, ClaimSessionCallback, CompleteSessionCallback,
    CompleteSessionWithEvent, CreateSessionWithEvent, GroupRepoPort, MessageRepoPort,
    RemoveSessionParticipantWithEvent, SessionRepoPort,
    UpdateSessionParticipantMessageViewScopeWithEvent,
};

use bcs_service_api::port::{
    EventRecordFactoryPort, FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort,
    FrontendDeliveryTarget, NewEvent,
};

use bcs_service_api::types::MessageViewScope;

use bcs_service_api::types::{EVENT_SCHEMA_VERSION_V1, EventScope, EventSubject};

use bcs_service_api::{
    ActorKind, BotRuntimeConnectionService, CollaborationRuntimeService, GroupStrategy,
    Participant, ParticipantMode, ParticipantRole, ServiceError, Session, SessionKind,
    SessionStatus,
};

use chrono::{SecondsFormat, Utc};

use serde_json::{Value, json};



#[path = "application/sessionmanagementserviceimpl.rs"]
mod sessionmanagementserviceimpl;
pub use sessionmanagementserviceimpl::{SessionManagementServiceImpl, SessionManagementWithRuntimeCleanup};
#[path = "application/sessionmanagementserviceimpl_2.rs"]
mod sessionmanagementserviceimpl_2;
