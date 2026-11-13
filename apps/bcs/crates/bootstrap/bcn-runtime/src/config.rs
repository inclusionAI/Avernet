use bcn_runtime_model::*;
use serde::{Deserialize, Serialize};
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeConfig {
    pub scope: Scope,
    pub profile: RuntimeProfile,
    pub storage: StorageSelection,
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub allowlist: Vec<ExecutionTarget>,
    #[serde(default)]
    pub limits: RuntimeDefaults,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StorageSelection { Memory, External }
impl RuntimeConfig {
    pub fn validate(&self) -> RuntimeResult<()> {
        self.scope.validate().map_err(|e| RuntimeError::InvalidInput(e.to_string()))?;
        if self.limits != RuntimeDefaults::default() {
            return Err(RuntimeError::InvalidInput("MVP execution limits are fixed; overrides are unsupported".into()));
        }
        if self.profile == RuntimeProfile::Production && self.storage == StorageSelection::Memory {
            return Err(RuntimeError::InvalidInput("production requires external durable ports".into()));
        }
        if self.enabled && self.allowlist.is_empty() {
            return Err(RuntimeError::InvalidInput("enabled admission requires explicit targets".into()));
        }
        for target in &self.allowlist {
            let (scope, id) = match target { ExecutionTarget::Agent { agent_id } => (&agent_id.scope, &agent_id.value), ExecutionTarget::Group { group_id } => (&group_id.scope, &group_id.value) };
            if scope != &self.scope || id.trim().is_empty() || id.len() > 256 { return Err(RuntimeError::InvalidInput("allowlist target scope/identity".into())); }
        }
        Ok(())
    }
}
