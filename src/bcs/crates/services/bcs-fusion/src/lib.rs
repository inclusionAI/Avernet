//! BCS fusion service implementations.
//!
//! Provides a local fallback implementation and a BCSFuse-backed implementation.

pub mod core;

pub use core::{
    FuseBackedFusionService, FuseClientLifecycle, FuseClientService, FuseWorkerProfileService, LlmClient,
    LocalFusionService, build_participant_id, build_sync_request, load_bot_context,
    normalize_worker_id, sync_worker_with_retry,
};
