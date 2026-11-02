//! Friendship / friend-connection route tests.
//!
//! Split target (1_329-line baseline): fixtures in `fixtures`, read-side and
//! write-side behavior suites beside. Every assertion moved verbatim.

#[path = "friendship_routes/fixtures.rs"]
mod fixtures;
#[path = "friendship_routes/read.rs"]
mod read;
#[path = "friendship_routes/write.rs"]
mod write;
