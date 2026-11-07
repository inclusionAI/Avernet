//! Bot / invite-code route tests.
//!
//! Split target (1_292-line baseline): fixtures in `fixtures`, read-side and
//! write-side behavior suites beside. Every assertion moved verbatim.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

#[path = "bot_routes/fixtures.rs"]
mod fixtures;
#[path = "bot_routes/read.rs"]
mod read;
#[path = "bot_routes/write.rs"]
mod write;
#[path = "bot_routes/avatars.rs"]
mod avatars;
