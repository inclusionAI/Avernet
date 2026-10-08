//! Strict authority resolution Core (plan Task 3).
//!
//! [`BotAuthorityCoreServiceImpl`] implements the `BotAuthorityCoreService`
//! contract over an injected `Arc<dyn BotAuthorityRepoPort>`. The layering
//! rule it enforces: the application-level `BotAuthorityHook` (and every
//! later manager/transfer/owner use case) resolves authority questions
//! through THIS core and never touches the repo port directly.
//!
//! The core composes the repo's strict reads into authorization answers:
//! before any `role`/`roles_for` answer it validates every involved Bot via
//! `ownership` (existence → `BotNotFound`, version 0 →
//! `OwnershipNotInitialized`, missing/non-unique owner → `CorruptAuthority`),
//! so a corrupted authority state never degrades into an ordinary deny —
//! and a batch mentioning any invalid Bot fails closed as a whole (spec
//! §12.4/§13.5: batch reads failing to authorize must fail unified).

mod manager;
mod ownership;
mod reads;
mod team_sync;

pub use reads::BotAuthorityCoreServiceImpl;