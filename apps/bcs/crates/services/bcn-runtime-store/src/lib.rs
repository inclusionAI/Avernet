//! Fenced repositories over the explicitly injected atomic storage contract.
use bcn_runtime_storage_api::SharedAtomicStore;
mod keys;
mod persistence;
mod ownership;
mod liveness;
#[derive(Clone)]
pub struct RuntimeStore { atomic: SharedAtomicStore }
impl RuntimeStore { pub fn new(atomic: SharedAtomicStore) -> Self { Self { atomic } } }

mod admission;
mod session;
mod inbox;
mod query;

mod commands;
mod dispatch;

mod events;
mod results;
pub use results::LocalResultStore;
