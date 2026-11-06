#[path = "../../../test-support/message_flow_contract_support.rs"]
mod flow_support;
#[path = "support/session.rs"]
mod session_support;

#[path = "cases/a2a_deadline_cache.rs"]
mod a2a_deadline_cache;
#[path = "cases/conformance_direct_a2a.rs"]
mod conformance_direct_a2a;
#[path = "cases/conformance_message_flow_services.rs"]
mod conformance_message_flow_services;
#[path = "cases/contract_a2a_chat.rs"]
mod contract_a2a_chat;
#[path = "cases/contract_bot_event.rs"]
mod contract_bot_event;
#[path = "cases/contract_coordination_chat_final.rs"]
mod contract_coordination_chat_final;
#[path = "cases/contract_coordination_tool_identity.rs"]
mod contract_coordination_tool_identity;
#[path = "cases/contract_group_fusion.rs"]
mod contract_group_fusion;
#[path = "cases/contract_human_notify_context.rs"]
mod contract_human_notify_context;
#[path = "cases/run_reply.rs"]
mod run_reply;
