#[path = "../../../test-support/message_flow_contract_support.rs"]
mod flow_support;
#[path = "support/session.rs"]
mod session_support;

#[path = "cases/contract_message_flow.rs"]
mod contract_message_flow;
#[path = "cases/authority_cutover.rs"]
mod authority_cutover;
#[path = "cases/bounded_context.rs"]
mod bounded_context;
#[path = "cases/caller_dm_web_send_contract.rs"]
mod caller_dm_web_send_contract;
#[path = "cases/delivery_session_access.rs"]
mod delivery_session_access;
#[path = "cases/delivery_through_bot_bounds.rs"]
mod delivery_through_bot_bounds;
#[path = "cases/delivery_via_controlled_bot.rs"]
mod delivery_via_controlled_bot;
#[path = "cases/observe_fanout_contract.rs"]
mod observe_fanout_contract;
#[path = "cases/routed_reply_derivation_contract.rs"]
mod routed_reply_derivation_contract;
#[path = "cases/speaking_lease_web_send_contract.rs"]
mod speaking_lease_web_send_contract;
#[path = "cases/web_send_session_membership.rs"]
mod web_send_session_membership;
