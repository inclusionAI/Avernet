use std::fmt::Write as _;
use std::path::Path;

use anyhow::{Context, Result, anyhow};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub const DEFINITION_YAML_MAX_BYTES: usize = 65_536;
pub const SNAPSHOT_MESSAGE_MAX_BYTES: usize = 262_144;
pub const SNAPSHOT_TITLE_MAX_CODE_POINTS: usize = 120;

const MESSAGE_PREFIX: &str =
    "<AixUI type=\"card\" component=\"bcsPanel.CollaborationDefinitionCard\" params='";
const MESSAGE_SUFFIX: &str = "' />";

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CollaborationDefinitionSnapshot {
    pub schema_version: u8,
    pub document_id: String,
    pub snapshot_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_snapshot_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    pub definition_yaml: String,
}

#[derive(Debug, Serialize)]
pub struct CollaborationDefinitionPreviewOutput {
    pub snapshot: CollaborationDefinitionSnapshot,
    pub message: String,
}

pub struct PreviewOptions<'a> {
    pub file: &'a Path,
    pub title: Option<&'a str>,
    pub document_id: Option<&'a str>,
    pub parent_snapshot_id: Option<&'a str>,
    pub snapshot_id: Option<&'a str>,
}

fn standard_uuid(value: &str, flag: &str) -> Result<String> {
    let parsed = Uuid::parse_str(value).map_err(|_| {
        anyhow!("{flag} must be a standard hyphenated UUID, got {value:?}")
    })?;
    let canonical = parsed.hyphenated().to_string();
    if !value.eq_ignore_ascii_case(&canonical) {
        return Err(anyhow!(
            "{flag} must be a standard hyphenated UUID, got {value:?}"
        ));
    }
    Ok(canonical)
}

fn validate_title(title: Option<&str>) -> Result<Option<String>> {
    let Some(title) = title else {
        return Ok(None);
    };
    if title.chars().all(is_ecmascript_whitespace) {
        return Err(anyhow!("--title must not be empty or blank"));
    }
    let code_points = title.chars().count();
    if code_points > SNAPSHOT_TITLE_MAX_CODE_POINTS {
        return Err(anyhow!(
            "--title exceeds the {} Unicode code point limit (got {code_points})",
            SNAPSHOT_TITLE_MAX_CODE_POINTS
        ));
    }
    Ok(Some(title.to_string()))
}

fn is_ecmascript_whitespace(value: char) -> bool {
    matches!(
        value,
        '\u{0009}'
            ..='\u{000d}'
                | '\u{0020}'
                | '\u{00a0}'
                | '\u{1680}'
                | '\u{2000}'..='\u{200a}'
                | '\u{2028}'
                | '\u{2029}'
                | '\u{202f}'
                | '\u{205f}'
                | '\u{3000}'
                | '\u{feff}'
    )
}

fn escape_aixui_attribute_json(json: &str) -> String {
    let mut escaped = String::with_capacity(json.len());
    for value in json.chars() {
        if is_ecmascript_whitespace(value) || matches!(value, '&' | '<' | '>' | '\'') {
            write!(escaped, "\\u{:04x}", value as u32)
                .expect("writing to String cannot fail");
        } else {
            escaped.push(value);
        }
    }
    escaped
}

pub fn serialize_snapshot_params(snapshot: &CollaborationDefinitionSnapshot) -> Result<String> {
    let json = serde_json::to_string(snapshot).context("Failed to serialize snapshot")?;
    Ok(escape_aixui_attribute_json(&json))
}

pub fn build_snapshot_message(snapshot: &CollaborationDefinitionSnapshot) -> Result<String> {
    let params = serialize_snapshot_params(snapshot)?;
    let message = format!("{MESSAGE_PREFIX}{params}{MESSAGE_SUFFIX}");
    let message_bytes = message.len();
    if message_bytes > SNAPSHOT_MESSAGE_MAX_BYTES {
        return Err(anyhow!(
            "serialized message exceeds the {} byte limit (got {message_bytes})",
            SNAPSHOT_MESSAGE_MAX_BYTES
        ));
    }
    Ok(message)
}

pub fn build_preview(options: PreviewOptions<'_>) -> Result<CollaborationDefinitionPreviewOutput> {
    if options.parent_snapshot_id.is_some() && options.document_id.is_none() {
        return Err(anyhow!(
            "--parent-snapshot-id requires an explicit --document-id"
        ));
    }

    let bytes = std::fs::read(options.file)
        .with_context(|| format!("Failed to read YAML file {}", options.file.display()))?;
    if bytes.len() > DEFINITION_YAML_MAX_BYTES {
        return Err(anyhow!(
            "definition YAML exceeds the {} byte limit (got {})",
            DEFINITION_YAML_MAX_BYTES,
            bytes.len()
        ));
    }
    let definition_yaml = String::from_utf8(bytes).map_err(|_| {
        anyhow!(
            "YAML file {} is not valid UTF-8",
            options.file.display()
        )
    })?;

    let document_id = options
        .document_id
        .map(|value| standard_uuid(value, "--document-id"))
        .transpose()?
        .unwrap_or_else(|| Uuid::new_v4().hyphenated().to_string());
    let snapshot_id = options
        .snapshot_id
        .map(|value| standard_uuid(value, "--snapshot-id"))
        .transpose()?
        .unwrap_or_else(|| Uuid::new_v4().hyphenated().to_string());
    let parent_snapshot_id = options
        .parent_snapshot_id
        .map(|value| standard_uuid(value, "--parent-snapshot-id"))
        .transpose()?;
    if parent_snapshot_id.as_deref() == Some(snapshot_id.as_str()) {
        return Err(anyhow!(
            "--parent-snapshot-id must not equal --snapshot-id"
        ));
    }

    let snapshot = CollaborationDefinitionSnapshot {
        schema_version: 1,
        document_id,
        snapshot_id,
        parent_snapshot_id,
        title: validate_title(options.title)?,
        definition_yaml,
    };
    let message = build_snapshot_message(&snapshot)?;
    Ok(CollaborationDefinitionPreviewOutput { snapshot, message })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct ContractVectors {
        limits: ContractLimits,
        valid: Vec<ValidVector>,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct ContractLimits {
        definition_yaml_utf8_bytes: usize,
        message_utf8_bytes: usize,
        title_code_points: usize,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct ValidVector {
        snapshot: CollaborationDefinitionSnapshot,
        serialized_params: String,
        message: String,
    }

    fn vectors() -> ContractVectors {
        serde_json::from_str(include_str!(
            "../../../../tests/fixtures/collaboration-definition-snapshot/vectors.json"
        ))
        .expect("contract vectors must be valid")
    }

    #[test]
    fn serializer_matches_shared_contract_vectors() {
        let vectors = vectors();
        for vector in vectors.valid {
            assert_eq!(
                serialize_snapshot_params(&vector.snapshot).unwrap(),
                vector.serialized_params
            );
            assert_eq!(build_snapshot_message(&vector.snapshot).unwrap(), vector.message);
        }
    }

    #[test]
    fn constants_match_shared_contract_limits() {
        let limits = vectors().limits;
        assert_eq!(DEFINITION_YAML_MAX_BYTES, limits.definition_yaml_utf8_bytes);
        assert_eq!(SNAPSHOT_MESSAGE_MAX_BYTES, limits.message_utf8_bytes);
        assert_eq!(SNAPSHOT_TITLE_MAX_CODE_POINTS, limits.title_code_points);
    }

    #[test]
    fn yaml_limit_is_measured_in_utf8_bytes() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("definition.yaml");
        std::fs::write(&file, "中".repeat(DEFINITION_YAML_MAX_BYTES / 3 + 1)).unwrap();
        let error = build_preview(PreviewOptions {
            file: &file,
            title: None,
            document_id: None,
            parent_snapshot_id: None,
            snapshot_id: None,
        })
        .unwrap_err();
        assert!(error.to_string().contains("65536 byte limit"));
    }

    #[test]
    fn serialized_message_limit_accepts_exact_boundary_and_rejects_next_byte() {
        let snapshot_with_yaml = |definition_yaml: String| CollaborationDefinitionSnapshot {
            schema_version: 1,
            document_id: "11111111-1111-4111-8111-111111111111".to_string(),
            snapshot_id: "22222222-2222-4222-8222-222222222222".to_string(),
            parent_snapshot_id: None,
            title: None,
            definition_yaml,
        };

        let fixed_bytes = build_snapshot_message(&snapshot_with_yaml(String::new()))
            .unwrap()
            .len();
        let variable_bytes = SNAPSHOT_MESSAGE_MAX_BYTES - fixed_bytes;
        let escaped_spaces = variable_bytes / 6;
        let plain_bytes = variable_bytes % 6;
        let exact_yaml = format!(
            "{}{}",
            " ".repeat(escaped_spaces),
            "a".repeat(plain_bytes)
        );
        assert!(exact_yaml.len() <= DEFINITION_YAML_MAX_BYTES);
        let exact_message =
            build_snapshot_message(&snapshot_with_yaml(exact_yaml.clone())).unwrap();
        assert_eq!(exact_message.len(), SNAPSHOT_MESSAGE_MAX_BYTES);

        let error = build_snapshot_message(&snapshot_with_yaml(format!("{exact_yaml}a")))
            .unwrap_err();
        assert!(error.to_string().contains("262144 byte limit"));
    }
}
