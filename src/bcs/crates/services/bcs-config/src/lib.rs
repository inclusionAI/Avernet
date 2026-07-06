//! BCS configuration service.
//!
//! Owns environment resolution and config loaders. The top-level `BcsConfig`
//! loader still lives in `bootstrap/bcs/src/config.rs` until the remaining
//! config graph is decomposed.

pub mod core;

pub use core::env::{ProcessEnvView, RuntimeEnv, resolve_env, resolve_env_str};
pub use core::mysql_loader::{ConfigLoadError, MysqlDbLoader};
pub use core::redis_loader::RedisCacheLoader;
