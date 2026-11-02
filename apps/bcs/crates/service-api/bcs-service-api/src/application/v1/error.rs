//! OpenAPI v1 use-case error vocabulary for the application layer.
//!
//! [`ApplicationError`] and its pinned `ERROR_*` codes live in the shared
//! `types` module (the one low-level module `application`, `core`, and
//! `port` may all reference without reverse dependencies), because the
//! outbound ports — e.g. `port::oauth` — type their results with the same
//! transport-independent vocabulary. These re-exports keep every
//! pre-existing import path (`crate::application::v1::error::*` and the
//! `application::v1` glob) compiling unchanged — and deliberately stop
//! there, so the shared `types` error items that arrived on their own
//! (`ServiceError`/`ServiceResult`) never leak into this namespace's
//! glob surface.
pub use crate::types::error::{
    ApplicationError, ERROR_EVENT_DELIVERY_LANE_BLOCKED, ERROR_EVENT_DELIVERY_NOT_FOUND,
    ERROR_EVENT_DELIVERY_NOT_REPLAYABLE, ERROR_EVENT_SUBSCRIPTION_FORBIDDEN,
    ERROR_EVENT_SUBSCRIPTION_LIMIT_REACHED, ERROR_EVENT_SUBSCRIPTION_NOT_FOUND,
    ERROR_EVENT_SUBSCRIPTION_REVISION_CONFLICT, ERROR_INVALID_EVENT_FILTER,
    ERROR_INVALID_EVENT_SCOPE, ERROR_INVALID_WEBHOOK_URL, ERROR_INVITE_CODE_ALREADY_BOUND,
    ERROR_INVITE_CODE_CLAIM_LIMIT_REACHED, ERROR_INVITE_CODE_NOT_APPLICABLE,
    ERROR_INVITE_CODE_REQUIRED, ERROR_INVITE_CODE_UNAVAILABLE,
};
