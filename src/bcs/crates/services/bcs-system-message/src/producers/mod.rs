//! System-message producers.
//!
//! Each producer converts a specific `SystemMessageEventKind` into a list
//! of `SystemGroupMessage`s ready for delivery.

pub mod bot_joined;
pub mod bot_hidden_notice;
pub mod bot_left;
pub mod generic;
pub mod human_joined;
pub mod participant_mode_changed;
pub mod session_context;

#[cfg(test)]
mod bot_joined_test;

#[cfg(test)]
mod participant_mode_changed_test;

#[cfg(test)]
mod session_context_test;
