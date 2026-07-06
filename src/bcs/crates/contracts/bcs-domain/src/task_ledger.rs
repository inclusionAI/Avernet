//! Task ledger summary shared by task coordination callers.

use serde::{Deserialize, Serialize};

#[derive(Debug, Default, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LedgerSummary {
    pub pending: Vec<String>,
    pub replied: Vec<String>,
    pub failed: Vec<String>,
    pub timed_out: Vec<String>,
}
