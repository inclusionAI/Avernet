//! Gateway submodule of the bcs-ws adapter. Originally the standalone
//! crate `bcs-gateway`; moved into `bcs-ws` during C3 of the first-round
//! refactor. The original crate now exists as a pub-use shim.

pub mod abort_manager;
pub mod chat_handler;
pub mod chat_types;
pub mod context;
pub mod event_broadcaster;
pub mod ws_handler;

// Re-exports from bcs-protocol (unified protocol layer)
pub use bcs_protocol::{
    RequestFrame, ResponseFrame, EventFrame, GatewayFrame, ErrorShape, error_codes,
};

// Additional re-exports
pub use abort_manager::ChatAbortManager;
pub use chat_types::{
    ChatEvent, ChatEventState, ChatSendParams, ChatSendResult, ChatSendStatus,
    ChatHistoryParams, ChatHistoryResult, ChatAbortParams, ChatAbortResult,
};
pub use context::{
    DeliveryType, GatewayContext, GatewaySession, RoutingDecision, RoutingTarget,
    SessionAccess, MessageRouting, AuthValidator,
    BotSendResult, RouteAndSendResult,
};
pub use event_broadcaster::EventBroadcaster;
