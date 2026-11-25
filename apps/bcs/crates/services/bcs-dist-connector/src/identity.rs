//! Connector wire identities are shared with Inbox admission.
//!
//! The identities now live in `bcs_dist_encoding::connector` (crate
//! `bcs-dist-encoding`), because the Inbox validator must compute exactly the
//! same values as the Connector; this module only re-exports them. They are:
//!
//! - request ids sent to the agent as the message id: `<did>:start` for the
//!   call's first message, `<did>:in:<seq>` for human reply `seq`;
//! - key-event input ids `ae:<hash>` ("agent event") and interaction ids
//!   `ia:<hash>` (same hash), derived from the dispatch id, the key event kind
//!   and, for questions, the round, never from the holder epoch (B8);
//! - the question round of `KeyEvent::InputRequired(n)` /
//!   `KeyEvent::AuthRequired(n)` (1-based), and the business digest of a key
//!   event (`key_digest`).
pub use bcs_dist_encoding::{connector::*, digest_fields};
