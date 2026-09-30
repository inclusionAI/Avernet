//! Server-owned limits for the fixed-loop compiler; no runtime enablement flag.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default, deny_unknown_fields)]
pub struct FixedLoopLimits {
    pub max_fixed_loop_iterations: u32,
    pub max_fixed_loop_body_nodes: usize,
    pub max_compiled_state_machine_nodes: usize,
    pub max_compiled_state_machine_bytes: usize,
}

impl Default for FixedLoopLimits {
    fn default() -> Self {
        Self {
            max_fixed_loop_iterations: 32,
            max_fixed_loop_body_nodes: 64,
            max_compiled_state_machine_nodes: 2048,
            max_compiled_state_machine_bytes: 4 * 1024 * 1024,
        }
    }
}

impl FixedLoopLimits {
    pub fn validate(&self) -> Result<(), String> {
        if self.max_fixed_loop_iterations == 0
            || self.max_fixed_loop_body_nodes == 0
            || self.max_compiled_state_machine_nodes == 0
            || self.max_compiled_state_machine_bytes == 0
        {
            return Err("collaboration.fixed_loop_limits values must all be greater than zero".into());
        }
        Ok(())
    }
}
