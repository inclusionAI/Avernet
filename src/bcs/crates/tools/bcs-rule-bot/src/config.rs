use std::collections::HashSet;
use std::fs;
use std::path::Path;

use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub version: u32,
    pub name: String,
    #[serde(default)]
    pub port_start: Option<u16>,
    #[serde(default)]
    pub port_step: Option<u16>,
    #[serde(default)]
    pub scopes: Option<String>,
    pub bots: Vec<BotProfile>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BotProfile {
    #[serde(default)]
    pub source: Option<String>,
    pub profile: String,
    pub name: String,
    pub summary: String,
    pub domains: String,
    pub skills: String,
    #[serde(default)]
    pub scopes: Option<String>,
    #[serde(default)]
    pub runtime: RuntimeConfig,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum RuntimeConfig {
    #[default]
    Openclaw,
    Rule {
        #[serde(default = "default_response_delay_ms")]
        response_delay_ms: u64,
        behavior: BehaviorConfig,
    },
}

#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StateScope {
    #[default]
    Session,
    Bot,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum BehaviorConfig {
    Fixed {
        replies: Vec<String>,
        #[serde(default)]
        scope: StateScope,
    },
    RandomReply {
        replies: Vec<String>,
        #[serde(default)]
        seed: Option<u64>,
        #[serde(default)]
        scope: StateScope,
    },
    Echo {
        #[serde(default = "default_repeat")]
        repeat: u64,
        #[serde(default)]
        separator: String,
        #[serde(default)]
        prefix: String,
        #[serde(default)]
        suffix: String,
    },
    RandomNumber {
        min: i64,
        max: i64,
        #[serde(default)]
        seed: Option<u64>,
        #[serde(default)]
        scope: StateScope,
    },
    TaskWorker {
        result: Box<BehaviorConfig>,
    },
    Supervisor {
        assignment: SupervisorAssignment,
        completion: SupervisorCompletion,
        summary_template: String,
    },
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SupervisorAssignment {
    pub mode: SupervisorAssignmentMode,
    pub task_template: String,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SupervisorAssignmentMode {
    EachMember,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SupervisorCompletion {
    pub timeout_ms: u64,
    #[serde(default)]
    pub max_retries: u32,
}

fn default_repeat() -> u64 {
    1
}

fn default_response_delay_ms() -> u64 {
    1_000
}

impl Manifest {
    pub fn load(path: &Path) -> Result<Self> {
        let content = fs::read_to_string(path)
            .with_context(|| format!("failed to read manifest {}", path.display()))?;
        let manifest: Self = serde_json::from_str(&content)
            .with_context(|| format!("failed to parse manifest {}", path.display()))?;
        manifest.validate()?;
        Ok(manifest)
    }

    pub fn validate(&self) -> Result<()> {
        if self.version != 2 {
            bail!(
                "bcs-rule-bot requires manifest version 2, got {}",
                self.version
            );
        }
        require_non_empty("name", &self.name)?;
        if self.bots.is_empty() {
            bail!("bots must not be empty");
        }

        let mut profiles = HashSet::new();
        let mut has_openclaw = false;
        for (index, bot) in self.bots.iter().enumerate() {
            bot.validate(index)?;
            if !profiles.insert(bot.profile.as_str()) {
                bail!("bots[{index}].profile is duplicated: {}", bot.profile);
            }
            has_openclaw |= matches!(bot.runtime, RuntimeConfig::Openclaw);
        }
        if has_openclaw {
            if self.port_start.is_none() {
                bail!("port_start is required when an OpenClaw runtime is present");
            }
            match self.port_step {
                Some(0) | None => {
                    bail!("port_step must be greater than 0 when an OpenClaw runtime is present")
                }
                Some(_) => {}
            }
        }
        Ok(())
    }

    pub fn rule_bots(&self) -> impl Iterator<Item = &BotProfile> {
        self.bots
            .iter()
            .filter(|bot| matches!(bot.runtime, RuntimeConfig::Rule { .. }))
    }
}

impl BotProfile {
    fn validate(&self, index: usize) -> Result<()> {
        validate_profile_name(index, &self.profile)?;
        require_non_empty(&format!("bots[{index}].name"), &self.name)?;
        require_non_empty(&format!("bots[{index}].summary"), &self.summary)?;
        require_non_empty(&format!("bots[{index}].domains"), &self.domains)?;
        require_non_empty(&format!("bots[{index}].skills"), &self.skills)?;

        match &self.runtime {
            RuntimeConfig::Openclaw => {
                let source = self.source.as_deref().ok_or_else(|| {
                    anyhow::anyhow!("bots[{index}].source is required for OpenClaw")
                })?;
                require_non_empty(&format!("bots[{index}].source"), source)?;
                if source.contains('/') || source.contains('\\') || source.contains("..") {
                    bail!("bots[{index}].source must be a direct child directory name");
                }
            }
            RuntimeConfig::Rule { behavior, .. } => {
                if self.source.is_some() {
                    bail!("bots[{index}].source is not allowed for rule runtime");
                }
                behavior.validate(&format!("bots[{index}].runtime.behavior"))?;
            }
        }
        Ok(())
    }

    pub fn response_delay_ms(&self) -> u64 {
        match self.runtime {
            RuntimeConfig::Rule {
                response_delay_ms, ..
            } => response_delay_ms,
            RuntimeConfig::Openclaw => 0,
        }
    }

    pub fn behavior(&self) -> Option<&BehaviorConfig> {
        match &self.runtime {
            RuntimeConfig::Rule { behavior, .. } => Some(behavior),
            RuntimeConfig::Openclaw => None,
        }
    }

    pub fn effective_scopes<'a>(&'a self, manifest: &'a Manifest) -> &'a str {
        self.scopes
            .as_deref()
            .or(manifest.scopes.as_deref())
            .unwrap_or("local")
    }

    pub fn behavior_name(&self) -> &'static str {
        self.behavior().map_or("openclaw", BehaviorConfig::name)
    }
}

impl BehaviorConfig {
    pub fn name(&self) -> &'static str {
        match self {
            Self::Fixed { .. } => "fixed",
            Self::RandomReply { .. } => "random_reply",
            Self::Echo { .. } => "echo",
            Self::RandomNumber { .. } => "random_number",
            Self::TaskWorker { .. } => "task_worker",
            Self::Supervisor { .. } => "supervisor",
        }
    }

    fn validate(&self, path: &str) -> Result<()> {
        match self {
            Self::Fixed { replies, .. } | Self::RandomReply { replies, .. } => {
                validate_replies(path, replies)?;
            }
            Self::Echo { repeat, .. } => {
                if *repeat == 0 {
                    bail!("{path}.repeat must be greater than 0");
                }
            }
            Self::RandomNumber { min, max, .. } => {
                if min > max {
                    bail!("{path}.min must be less than or equal to {path}.max");
                }
            }
            Self::TaskWorker { result } => {
                if matches!(
                    result.as_ref(),
                    Self::TaskWorker { .. } | Self::Supervisor { .. }
                ) {
                    bail!("{path}.result must be fixed, random_reply, echo, or random_number");
                }
                result.validate(&format!("{path}.result"))?;
            }
            Self::Supervisor {
                assignment,
                completion,
                summary_template,
            } => {
                require_non_empty(
                    &format!("{path}.assignment.task_template"),
                    &assignment.task_template,
                )?;
                if assignment.mode != SupervisorAssignmentMode::EachMember {
                    bail!("{path}.assignment.mode is not supported");
                }
                if completion.timeout_ms == 0 {
                    bail!("{path}.completion.timeout_ms must be greater than 0");
                }
                require_non_empty(&format!("{path}.summary_template"), summary_template)?;
            }
        }
        Ok(())
    }
}

fn validate_profile_name(index: usize, value: &str) -> Result<()> {
    require_non_empty(&format!("bots[{index}].profile"), value)?;
    if !value
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-')
    {
        bail!("bots[{index}].profile must match ^[A-Za-z0-9_-]+$");
    }
    Ok(())
}

fn validate_replies(path: &str, replies: &[String]) -> Result<()> {
    if replies.is_empty() {
        bail!("{path}.replies must not be empty");
    }
    if let Some(index) = replies.iter().position(|reply| reply.trim().is_empty()) {
        bail!("{path}.replies[{index}] must not be empty");
    }
    Ok(())
}

fn require_non_empty(path: &str, value: &str) -> Result<()> {
    if value.trim().is_empty() {
        bail!("{path} must not be empty");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(value: serde_json::Value) -> Result<Manifest> {
        let manifest: Manifest = serde_json::from_value(value)?;
        manifest.validate()?;
        Ok(manifest)
    }

    fn rule_bot(behavior: serde_json::Value) -> serde_json::Value {
        serde_json::json!({
            "profile": "rule",
            "name": "Rule",
            "summary": "Rule bot",
            "domains": "utility",
            "skills": "reply",
            "runtime": {
                "type": "rule",
                "behavior": behavior
            }
        })
    }

    #[test]
    fn accepts_rule_only_manifest_without_ports() {
        let manifest = parse(serde_json::json!({
            "version": 2,
            "name": "rules",
            "bots": [rule_bot(serde_json::json!({
                "type": "fixed",
                "replies": ["你好"]
            }))]
        }))
        .unwrap_or_else(|error| panic!("rule-only manifest: {error}"));

        assert_eq!(manifest.rule_bots().count(), 1);
        assert_eq!(manifest.bots[0].response_delay_ms(), 1_000);
    }

    #[test]
    fn explicit_zero_response_delay_remains_supported() {
        let mut bot = rule_bot(serde_json::json!({
            "type": "fixed",
            "replies": ["ok"]
        }));
        bot["runtime"]["response_delay_ms"] = serde_json::json!(0);
        let manifest = parse(serde_json::json!({
            "version": 2,
            "name": "rules",
            "bots": [bot]
        }))
        .unwrap_or_else(|error| panic!("explicit zero delay must be valid: {error}"));

        assert_eq!(manifest.bots[0].response_delay_ms(), 0);
    }

    #[test]
    fn rejects_unknown_behavior_fields() {
        let result = parse(serde_json::json!({
            "version": 2,
            "name": "rules",
            "bots": [rule_bot(serde_json::json!({
                "type": "echo",
                "repeat": 1,
                "unknown": true
            }))]
        }));

        assert!(result.is_err());
    }

    #[test]
    fn accepts_unbounded_positive_echo_repeat() {
        let manifest = parse(serde_json::json!({
            "version": 2,
            "name": "rules",
            "bots": [rule_bot(serde_json::json!({
                "type": "echo",
                "repeat": u64::MAX
            }))]
        }))
        .unwrap_or_else(|error| panic!("large repeat must pass config validation: {error}"));

        assert_eq!(manifest.rule_bots().count(), 1);
    }

    #[test]
    fn rejects_rule_source() {
        let mut bot = rule_bot(serde_json::json!({
            "type": "fixed",
            "replies": ["ok"]
        }));
        bot["source"] = serde_json::Value::String("persona".to_string());
        let result = parse(serde_json::json!({
            "version": 2,
            "name": "rules",
            "bots": [bot]
        }));

        assert!(result.is_err());
    }
}
