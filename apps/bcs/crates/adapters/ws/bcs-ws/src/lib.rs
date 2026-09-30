//! WebSocket delivery adapter for BCS.
//!
//! The bot-facing dispatcher/handler and adapter-owned registries live here.
//! Bootstrap still owns route composition and the web workbench dispatcher
//! wrapper until the remaining server state coupling is removed.

pub mod bot;
pub mod gateway;
pub mod shared;
pub mod web;
