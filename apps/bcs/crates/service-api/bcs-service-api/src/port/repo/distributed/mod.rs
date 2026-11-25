//! Distributed repository contracts v1. No provider is installed by this module.
//! Providers must pass the reusable contract suite before serving traffic.
pub mod common;
pub mod dispatch;
pub mod inbox;
pub mod ownership;
pub mod result;
pub mod session;
pub mod task_feed;

pub mod admission;

pub mod control;

pub mod budget;
