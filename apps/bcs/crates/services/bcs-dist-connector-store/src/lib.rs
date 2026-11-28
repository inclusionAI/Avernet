//! Store providers of the Connector, written only against `bcs-store-api`.
//!
//! The Connector is the process that runs downstream agent calls for the
//! Worker. The Worker writes one dispatch per call; a Connector claims it,
//! talks to the agent, and writes back what happened. This crate is the
//! Connector's whole view of the store; it holds no network or agent code.
//!
//! # The three providers
//!
//! - [`queue`]: `DispatchQueuePort` ([`StoreDispatchQueue`]): the fenced claim
//!   of a dispatch, the resume of a call this process already holds, and the
//!   entry point that opens the command feed.
//! - [`held`]: `HeldDispatchPort` (`StoreHeldDispatch`): the holder's handle
//!   on one claimed dispatch; every read and write it makes is fenced.
//! - [`feed`]: `CommandFeedPort` (`StoreCommandFeed`): reads the command
//!   stream through the consumer group `connectors`.
//!
//! Helpers and stubs:
//!
//! - [`held_index`]: the unfinished calls of one command partition.
//! - [`doc`]: decoding the stored dispatch document and building its patches.
//! - [`membership`]: an explicit stub of the membership provider; it fails
//!   with `need_contract_not_implemented`.
//!
//! Key names and versions come from `bcs-dist-store` (`layout`, `version`,
//! `command`); nothing here builds one. The dispatch document is the truth:
//! the command stream is only a hint and the held index only speeds recovery
//! up.
//!
//! # The `disp` document
//!
//! One JSON document per dispatch, shared by the Worker and the holding
//! Connector. Each side writes only its own parts:
//!
//! - `/spec`: written by the Worker at creation, never changed. It identifies
//!   the logical agent, request and downstream conversation context. The
//!   Connector resolves the agent's capabilities when it handles the call.
//! - `/control`: written by the Worker: `cancel` (a cancel intent) and
//!   `pending_input` (human replies waiting in the Worker's queue).
//! - `/execution`: written by the holder: `holder`, `phase`, `remote`,
//!   `confirmed_input_through`, `cancellation`, `outcome`.
//! - `/connector`: written by the holder: facts only the Connector needs
//!   (`input_sending`, `finish_evidence`).
//! - Worker-only top-level fields (`receipts`, `due`) are not decoded here.
//!   The Connector sets `due` to 0 only when it ends the call, so the Worker
//!   looks at the ended call soon.
//!
//! # Store keys this crate touches
//!
//! ```text
//! disp document (JSON, one per dispatch)
//!   key:   bcn:v1:<cluster>:<ns>:s<shard>:disp:<did>
//!          bcn:v1:c01:main:s0042:disp:s0042.7f3a9c.b21e.n2.a1
//!   value: {"spec": {...}, "control": {"cancel": null, "pending_input": []},
//!           "execution": {"holder": {"instance": {"node": "conn-03",
//!             "incarnation": "1794370950"}, "epoch": 2}, "phase": "accepted",
//!             "remote": {"task_id": "t-81", "context_id": null},
//!             "confirmed_input_through": null,
//!             "cancellation": {"kind": "not_sent"}, "outcome": null},
//!           "connector": {"input_sending": null, "finish_evidence": null},
//!           "receipts": [...], "due": 1794371000000}
//!   version: 2000000003 (claimed by holder epoch 2 at 2 x 10^9, then three
//!            more writes by either side)
//!
//! command stream partition (stream, read through consumer group `connectors`)
//!   key:   bcn:v1:cmd:<cluster>:<ns>:<pool>:p<n>
//!          bcn:v1:cmd:c01:main:a2a:p3
//!   entry: id 1794370950123-0, fields {"did": "s0042.7f3a9c.b21e.n2.a1"}
//!
//! held index (sorted set, one per command partition of a pool)
//!   key:   bcn:v1:<cluster>:<ns>:<pool>:p<nnnn>:held
//!          bcn:v1:c01:main:a2a:p0003:held
//!   value: member "s0042.7f3a9c.b21e.n2.a1", score 1794370950456
//!          (member = dispatch id, score = first listed, in ms)
//!
//! partition owner (read through `ShardLeasePort`, written by the engine)
//!   key:   bcn:v1:<cluster>:<ns>:<pool>:p<nnnn>:owner
//!          bcn:v1:c01:main:a2a:p0003:owner
//! ```
//!
//! # Glossary
//!
//! - **Dispatch** (`did`): one downstream agent call the Worker asked for.
//! - **Holder**: `{instance, epoch}` recorded in `/execution/holder`; the
//!   Connector process run that currently owns the call.
//! - **Instance**: `{node, incarnation}`, one run of a Connector process.
//! - **Holder epoch** (also "generation"): rises by one with every claim. A
//!   claim writes at version `epoch x 10^9`; every other holder write uses
//!   `read version + 1` inside that band. Equal versions are rejected, so of two
//!   claimers one wins, and a former holder's later writes are refused (fenced).
//! - **Claim**: the conditional write that makes an instance the holder.
//! - **Hint**: a command-stream entry with one field `did`, meaning "go look at
//!   `disp:<did>`". Never an instruction; the document decides.
//! - **Partition**: one of the N streams a pool's hints are spread over.
//!   Each has one owner Connector instance, which reads it and runs its calls.
//! - **Idle takeover**: moving hints another consumer left unacknowledged for
//!   too long to this consumer.
//! - **Held index**: the per-partition list of unfinished dispatches. It may
//!   list too much, never too little (spec rule B6).

pub mod doc;
pub mod feed;
pub mod held;
pub mod held_index;
pub mod membership;
pub mod queue;

mod shared;

pub use queue::{DispatchBoardConfig, StoreDispatchQueue};
