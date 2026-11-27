//! Connector engine of the distributed executor plane.
//!
//! The Connector makes the downstream A2A calls (one call = one dispatch) and
//! reports agent events to the session's inbox. This crate holds the rules and
//! the engine; it reaches storage and agents only through ports
//! ([`ConnectorPorts`]), so the same code runs against the local and the
//! production stores.
//!
//! # How it fits together
//!
//! ```text
//! command stream hint ("look at disp:<did>")
//!        │
//!        ▼
//! engine: receive → claim → ack          (one engine per Connector process)
//!        │ Acquired: spawn one actor      OwnedByMe: poke the live actor
//!        ▼
//! actor (Call): run → drive → follow loop (one tokio task per held call)
//!        │ tick every ~1.5 s, or at once on a poke
//!        ▼
//! disp document (replies, cancels) ⇄ agent (A2A) → inbox (agent events)
//! ```
//!
//! - The **engine** reads hints from the command stream, claims the dispatch
//!   they name, and acks the hint once the claim decision is made (B2). A
//!   claim that wins makes this process the **holder** of the call and starts
//!   one **actor** for it.
//! - The **actor** (`Call`, private module `call`) owns that one call for one
//!   holder generation: it sends the start, follows the remote task, reports
//!   events, forwards human replies and carries out cancels until the call
//!   ends or another generation takes it.
//! - A **tick** is the actor's periodic re-read of its `disp` document; it is
//!   how the holder learns of replies and cancels (B14). A **poke** is the
//!   engine asking a live actor to tick now, because a hint for its call
//!   arrived.
//!
//! # Where to start reading
//!
//! 1. [`engine`]: the main loop (receive → claim → ack), drain and snapshot.
//! 2. `call.rs`: the actor's life (`run`, `drive`, `follow`, `tick`).
//! 3. `send.rs` (start, resolve an uncertain start, re-attach),
//!    `control.rs` (replies and cancels), `report.rs` (events to the inbox).
//! 4. [`rules`]: the pure decisions (`DistConnectorCoreService`), testable
//!    without any store or agent.
//!
//! Other modules:
//! - [`identity`]: stable request, input and interaction ids (re-exported
//!   from `bcs_dist_encoding::connector`).
//! - [`config`]: settings and ports.
//! - [`delayed`]: the explicit stub of a contract that is not final (D2).
//!
//! The per-call actor lives in the private modules `call`, `send`, `control`
//! and `report`; they all add methods to the one `Call` type.

mod call;
mod control;
mod report;
mod node_history;
mod send;

pub mod config;
pub mod delayed;
pub mod engine;
pub mod identity;
pub mod rules;

pub use config::{ConnectorConfig, ConnectorPorts};
pub use engine::ConnectorEngine;
