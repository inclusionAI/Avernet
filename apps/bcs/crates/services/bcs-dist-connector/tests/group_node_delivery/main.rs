//! T6 (Stage 3): 下游会话也按节点隔离，送达未知不盲发。
//!
//! Downstream sessions are node-isolated: every operation carries a
//! trusted `CallAuthority` — the FSM round, node, attempt and binding of
//! the dispatch — and unknown delivery is never resolved by sending again.
//! The three planes never impersonate each other: logical rounds, send
//! attempts and actual downstream executions are counted separately.
//!
//! Vector families, one module each:
//! - `authority`: a dropped reply, two nodes of one bot, revoked grants.
//! - ` takeover`: the dedupe proof a resend may rely on.
//! - `stop_evidence`: foreign-task evidence and Completed ≠ product Final.
//! - `publication`: shared bindings with distinct grants, and the memory
//!   isolation the capability gate requires before anything publishes.
//!
//! Node history responsibility: BCS hands the node its own frozen input in
//! the send payload; the remote task keeps its own continuation context.
//! The two are never concatenated — a continuation addresses the remote
//! task only, and a first start neither reuses nor fabricates a context.

#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod authority;
mod harness;
mod publication;
mod stop_evidence;
mod takeover;
