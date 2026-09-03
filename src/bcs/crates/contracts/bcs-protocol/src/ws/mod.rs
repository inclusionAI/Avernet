pub mod coordination;
pub mod protocol;

pub use coordination::{
    CONTRACT_VERSION, CoordinationCall, MAGIC_KEY, TOOL_ASSIGN_TASK, TOOL_SEND_TASK_MESSAGE,
    TOOL_TASK_COMPLETE,
};
pub use protocol::{
    AgentEventPayload, AgentStream, BCS_MIN_SUPPORTED_VERSION, BCS_PROTOCOL_VERSION, BcsFrame,
    BotConnectParams, BotConnectResponse, BotStatus, BotStatusParams, ChannelInfo, ChannelSource,
    ChatAbortParams, ChatAbortResult, ChatEventPayload, ChatEventRouting, ChatEventState,
    ChatInjectParams, ChatSendParams, ChatSendResponse, ContentBlock, DirectiveAction, ErrorShape,
    EventFrame, GROUP_ID_PREFIX, GatewayFrame, GroupContext, GroupContextDeliveryType,
    GroupContextInput, GroupContextParticipant, MessageContent, OnboardRequestParams,
    OnboardResponsePayload, ProtocolDeprecation, RequestFrame, RequestSource, ResponseDirective,
    ResponseFrame, ResponseMode, RouteSelectorWire, ToolEventData, ToolPhase, ToolResult,
    ToolResultContent, UsageInfo, WsBotCapabilities, apply_channel_info, apply_sender_display_name,
    build_chat_inject_frame, build_chat_send_frame, build_direct_chat_inject_frame,
    build_direct_chat_send_frame, build_recipient_group_context, build_session_key, error_codes,
    now_ms, response_directive_for_delivery,
};
