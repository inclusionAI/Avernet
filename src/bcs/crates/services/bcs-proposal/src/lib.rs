//! BCS proposal service implementation.

pub mod application;
pub mod core;

pub use application::{GroupProposalUseCases, GroupProposalUseCasesConfig};
pub use core::{ProposalBuilder, ProposalStore};
