//! Independent human wait and total Task caps; execution uses the existing timeout.
use serde::{Deserialize,Serialize};

#[derive(Clone,Debug,Serialize,Deserialize)]
#[serde(default,deny_unknown_fields)]
pub struct A2aInputConfig {pub wait_timeout_ms:u64,pub total_timeout_ms:u64}
impl Default for A2aInputConfig {
    fn default()->Self {Self {wait_timeout_ms:600_000,total_timeout_ms:1_800_000}}
}
impl A2aInputConfig {
    pub fn validate(&self)->Result<(),String> {
        if self.wait_timeout_ms==0 || self.total_timeout_ms==0 || self.wait_timeout_ms>self.total_timeout_ms
            || self.total_timeout_ms>86_400_000 {
            return Err("a2a_input requires 0 < wait_timeout_ms <= total_timeout_ms <= 86400000".into());
        } Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn input_budget_config_defaults_bounds_and_unknown_fields() {
        let defaults:A2aInputConfig=toml::from_str("").unwrap();assert!(defaults.validate().is_ok());
        assert_eq!(defaults.wait_timeout_ms,600_000);
        for text in ["wait_timeout_ms=0","wait_timeout_ms=2000000","total_timeout_ms=86400001","total_timeout_ms=0"] {
            assert!(toml::from_str::<A2aInputConfig>(text).unwrap().validate().is_err());
        }
        assert!(toml::from_str::<A2aInputConfig>("wait_timout_ms=100").is_err());
    }
}
