use std::collections::HashSet;
use std::io::Read;
use std::path::Path;

use schemars::{JsonSchema, schema_for};
use serde::{Deserialize, Serialize};

use crate::error::AppError;
use crate::models::ExecutionMode;

#[derive(Debug, Clone, Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct GoalBatchFile {
    pub schema_version: String,
    #[serde(default)]
    pub defaults: GoalBatchDefaults,
    pub goals: Vec<GoalBatchItem>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct GoalBatchDefaults {
    #[serde(rename = "from", skip_serializing_if = "Option::is_none")]
    pub from_ref: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mode: Option<ExecutionMode>,
}

#[derive(Debug, Clone, Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct GoalBatchItem {
    pub id: String,
    pub goal: String,
    #[serde(rename = "from", skip_serializing_if = "Option::is_none")]
    pub from_ref: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub to: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mode: Option<ExecutionMode>,
}

#[derive(Debug, Clone, Serialize)]
pub struct BatchExecutionUnit {
    pub goal_id: String,
    pub goal: String,
    pub from_ref: String,
    pub target: Option<String>,
    pub mode: ExecutionMode,
}

#[derive(Debug, Serialize)]
pub struct BatchValidationResult {
    pub valid: bool,
    pub goal_count: usize,
    pub execution_count: usize,
    pub executions: Vec<BatchExecutionUnit>,
}

pub fn read_batch_file(path: &Path) -> Result<GoalBatchFile, AppError> {
    let mut content = String::new();
    if path == Path::new("-") {
        std::io::stdin().read_to_string(&mut content)?;
    } else {
        content = std::fs::read_to_string(path)?;
    }
    serde_yaml::from_str(&content)
        .map_err(|error| AppError::InvalidInput(format!("INVALID_BATCH_FILE: {error}")))
}

pub fn validate_and_expand(file: &GoalBatchFile) -> Result<Vec<BatchExecutionUnit>, AppError> {
    if file.schema_version != "1" {
        return Err(AppError::InvalidInput(format!(
            "UNSUPPORTED_SCHEMA_VERSION: expected 1, got {}",
            file.schema_version
        )));
    }
    if file.goals.is_empty() {
        return Err(AppError::InvalidInput(
            "EMPTY_GOALS: goals must contain at least one item".to_string(),
        ));
    }

    let default_from = file.defaults.from_ref.as_deref().unwrap_or("human");
    validate_from(default_from, "defaults.from")?;
    let default_mode = file.defaults.mode.unwrap_or_default();
    let mut ids = HashSet::new();
    let mut units = Vec::new();

    for (index, item) in file.goals.iter().enumerate() {
        let path = format!("goals[{index}]");
        if item.id.trim().is_empty() {
            return Err(AppError::InvalidInput(format!("EMPTY_GOAL_ID: {path}.id")));
        }
        if !ids.insert(item.id.clone()) {
            return Err(AppError::InvalidInput(format!(
                "DUPLICATE_GOAL_ID: {}",
                item.id
            )));
        }
        if item.goal.trim().is_empty() {
            return Err(AppError::InvalidInput(format!("EMPTY_GOAL: {path}.goal")));
        }

        let from_ref = item.from_ref.as_deref().unwrap_or(default_from);
        validate_from(from_ref, &format!("{path}.from"))?;
        let mode = item.mode.unwrap_or(default_mode);

        match &item.to {
            None => units.push(BatchExecutionUnit {
                goal_id: item.id.clone(),
                goal: item.goal.trim().to_string(),
                from_ref: from_ref.to_string(),
                target: None,
                mode,
            }),
            Some(targets) => {
                if targets.is_empty() {
                    return Err(AppError::InvalidInput(format!("EMPTY_TARGETS: {path}.to")));
                }
                let mut seen = HashSet::new();
                for (target_index, target) in targets.iter().enumerate() {
                    validate_target(target, &format!("{path}.to[{target_index}]"))?;
                    if !seen.insert(target.clone()) {
                        return Err(AppError::InvalidInput(format!(
                            "DUPLICATE_TARGET: {target}"
                        )));
                    }
                    units.push(BatchExecutionUnit {
                        goal_id: item.id.clone(),
                        goal: item.goal.trim().to_string(),
                        from_ref: from_ref.to_string(),
                        target: Some(target.clone()),
                        mode,
                    });
                }
            }
        }
    }
    Ok(units)
}

pub fn validate_from(value: &str, path: &str) -> Result<(), AppError> {
    if value == "human" {
        return Ok(());
    }
    let reference = value.strip_prefix("bot:").unwrap_or_default();
    if reference.trim().is_empty() {
        return Err(AppError::InvalidInput(format!(
            "INVALID_FROM: {path} must be human or bot:<ref>"
        )));
    }
    Ok(())
}

pub fn validate_target(value: &str, path: &str) -> Result<(), AppError> {
    if value.starts_with("group:") {
        return Err(AppError::InvalidInput(format!(
            "UNSUPPORTED_TARGET_TYPE: {path} only supports bot:<ref>"
        )));
    }
    let reference = value.strip_prefix("bot:").unwrap_or_default();
    if reference.trim().is_empty() {
        return Err(AppError::InvalidInput(format!(
            "INVALID_TARGET: {path} must be bot:<ref>"
        )));
    }
    Ok(())
}

pub fn json_schema() -> Result<serde_json::Value, AppError> {
    let mut schema = serde_json::to_value(schema_for!(GoalBatchFile))?;
    let root_properties = schema
        .get_mut("properties")
        .and_then(serde_json::Value::as_object_mut)
        .ok_or_else(|| AppError::Serialization("batch schema has no properties".to_string()))?;
    if let Some(version) = root_properties
        .get_mut("schema_version")
        .and_then(serde_json::Value::as_object_mut)
    {
        version.insert("const".to_string(), serde_json::json!("1"));
    }
    let definitions = schema
        .get_mut("$defs")
        .and_then(serde_json::Value::as_object_mut)
        .ok_or_else(|| AppError::Serialization("batch schema has no definitions".to_string()))?;
    for definition_name in ["GoalBatchDefaults", "GoalBatchItem"] {
        if let Some(from) = definitions
            .get_mut(definition_name)
            .and_then(|value| value.get_mut("properties"))
            .and_then(|value| value.get_mut("from"))
            .and_then(serde_json::Value::as_object_mut)
        {
            from.insert(
                "pattern".to_string(),
                serde_json::json!(r"^(human|bot:.+)$"),
            );
        }
    }
    let item_properties = definitions
        .get_mut("GoalBatchItem")
        .and_then(|value| value.get_mut("properties"))
        .and_then(serde_json::Value::as_object_mut)
        .ok_or_else(|| AppError::Serialization("batch item schema is missing".to_string()))?;
    for key in ["id", "goal"] {
        if let Some(value) = item_properties
            .get_mut(key)
            .and_then(serde_json::Value::as_object_mut)
        {
            value.insert("minLength".to_string(), serde_json::json!(1));
        }
    }
    if let Some(targets) = item_properties
        .get_mut("to")
        .and_then(serde_json::Value::as_object_mut)
    {
        targets.insert("minItems".to_string(), serde_json::json!(1));
        targets.insert("uniqueItems".to_string(), serde_json::json!(true));
        if let Some(items) = targets
            .get_mut("items")
            .and_then(serde_json::Value::as_object_mut)
        {
            items.insert("pattern".to_string(), serde_json::json!(r"^bot:.+$"));
        }
    }
    Ok(schema)
}

pub fn example() -> GoalBatchFile {
    GoalBatchFile {
        schema_version: "1".to_string(),
        defaults: GoalBatchDefaults {
            from_ref: Some("human".to_string()),
            mode: Some(ExecutionMode::Relay),
        },
        goals: vec![
            GoalBatchItem {
                id: "goal1".to_string(),
                goal: "分析支付失败率".to_string(),
                from_ref: None,
                to: Some(vec!["bot:risk-bot".to_string()]),
                mode: None,
            },
            GoalBatchItem {
                id: "goal2".to_string(),
                goal: "分析退款失败率".to_string(),
                from_ref: Some("bot:my-assistant".to_string()),
                to: Some(vec![
                    "bot:risk-bot-a".to_string(),
                    "bot:risk-bot-b".to_string(),
                ]),
                mode: Some(ExecutionMode::Centralized),
            },
        ],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn example_round_trips_and_expands_targets() {
        let yaml = serde_yaml::to_string(&example()).expect("serialize example");
        let parsed: GoalBatchFile = serde_yaml::from_str(&yaml).expect("parse example");
        let units = validate_and_expand(&parsed).expect("validate example");
        assert_eq!(units.len(), 3);
        assert_eq!(units[1].from_ref, "bot:my-assistant");
    }

    #[test]
    fn rejects_group_before_network_use() {
        let mut file = example();
        file.goals[0].to = Some(vec!["group:risk".to_string()]);
        let error = validate_and_expand(&file).expect_err("group must fail");
        assert!(error.to_string().contains("UNSUPPORTED_TARGET_TYPE"));
    }

    #[test]
    fn rejects_unknown_fields() {
        let error =
            serde_yaml::from_str::<GoalBatchFile>("schema_version: '1'\nruntime: {}\ngoals: []\n")
                .expect_err("unknown field must fail");
        assert!(error.to_string().contains("unknown field"));
    }

    #[test]
    fn schema_declares_version_and_unique_bot_targets() {
        let schema = json_schema().expect("schema");
        assert_eq!(schema["properties"]["schema_version"]["const"], "1");
        let targets = &schema["$defs"]["GoalBatchItem"]["properties"]["to"];
        assert_eq!(targets["uniqueItems"], true);
        assert_eq!(targets["items"]["pattern"], "^bot:.+$");
    }
}
