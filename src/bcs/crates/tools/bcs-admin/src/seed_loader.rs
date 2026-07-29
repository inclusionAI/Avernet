use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};

use anyhow::{Context, Result, bail};
use bcs_domain::CollaborationDefinition;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const DEFAULT_PRIORITY: u32 = u32::MAX;

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct TemplateSeedCatalog {
    pub(crate) templates: Vec<TemplateSeedEntry>,
    pub(crate) supported_languages: Vec<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct TemplateSeedEntry {
    pub(crate) id: String,
    pub(crate) tags: Vec<String>,
    pub(crate) priority: u32,
    pub(crate) contents: Vec<TemplateSeedContent>,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct TemplateSeedContent {
    pub(crate) template_id: String,
    pub(crate) lang: String,
    pub(crate) name: String,
    pub(crate) description: Option<String>,
    pub(crate) participant_summary_json: serde_json::Value,
    pub(crate) definition_json: serde_json::Value,
    pub(crate) yaml_text: String,
    pub(crate) yaml_sha256: String,
    pub(crate) version: u64,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct RegistryFile {
    #[serde(default)]
    templates: BTreeMap<String, RegistryTemplate>,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct RegistryTemplate {
    #[serde(default)]
    tags: Vec<String>,
    #[serde(default)]
    priority: Option<u32>,
    #[serde(default)]
    sort_order: Option<u32>,
}

impl RegistryTemplate {
    fn priority(&self) -> u32 {
        self.priority.or(self.sort_order).unwrap_or(DEFAULT_PRIORITY)
    }
}

#[derive(Debug, Serialize)]
struct ParticipantSummary {
    #[serde(skip_serializing_if = "Option::is_none")]
    display_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    description: Option<String>,
    required: bool,
}

pub(crate) fn load_template_seed_catalog(
    base_dir: impl AsRef<Path>,
) -> Result<TemplateSeedCatalog> {
    let base_dir = base_dir.as_ref();
    let registry = load_seed_registry(base_dir)?;
    let mut templates: BTreeMap<String, TemplateSeedEntry> = BTreeMap::new();
    let mut supported_languages = BTreeSet::new();

    for (id, registry_entry) in &registry.templates {
        validate_template_id(id)?;
        validate_tags(&registry_entry.tags)?;
        templates.insert(
            id.clone(),
            TemplateSeedEntry {
                id: id.clone(),
                tags: registry_entry.tags.clone(),
                priority: registry_entry.priority(),
                contents: Vec::new(),
            },
        );
    }

    for dir_entry in fs::read_dir(base_dir)
        .with_context(|| format!("read template seed dir '{}'", base_dir.display()))?
    {
        let dir_entry = dir_entry?;
        if !dir_entry.file_type()?.is_dir() {
            continue;
        }

        let lang = dir_entry
            .file_name()
            .to_str()
            .map(ToString::to_string)
            .with_context(|| {
                format!(
                    "template language directory '{}' is not UTF-8",
                    dir_entry.path().display()
                )
            })?;
        validate_language(&lang)?;
        supported_languages.insert(lang.clone());

        for file_entry in fs::read_dir(dir_entry.path())? {
            let file_entry = file_entry?;
            if !file_entry.file_type()?.is_file() || !is_yaml_file(&file_entry.path()) {
                continue;
            }

            let template_id = template_id_from_seed_path(&file_entry.path())?;
            let registry_entry = registry.templates.get(&template_id).cloned().unwrap_or_default();
            let content = load_seed_content(&file_entry.path(), &template_id, &lang)?;
            let entry = templates
                .entry(template_id.clone())
                .or_insert_with(|| TemplateSeedEntry {
                    id: template_id.clone(),
                    priority: registry_entry.priority(),
                    tags: registry_entry.tags,
                    contents: Vec::new(),
                });
            entry.contents.push(content);
        }
    }

    for entry in templates.values_mut() {
        entry.contents.sort_by(|left, right| left.lang.cmp(&right.lang));
    }

    let mut templates: Vec<_> = templates
        .into_values()
        .filter(|entry| !entry.contents.is_empty())
        .collect();
    templates.sort_by(|left, right| {
        left.priority
            .cmp(&right.priority)
            .then_with(|| left.id.cmp(&right.id))
    });

    Ok(TemplateSeedCatalog {
        templates,
        supported_languages: supported_languages.into_iter().collect(),
    })
}

fn load_seed_registry(base_dir: &Path) -> Result<RegistryFile> {
    let registry_path = base_dir.join("registry.yaml");
    let raw = match fs::read_to_string(&registry_path) {
        Ok(raw) => raw,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(RegistryFile::default());
        }
        Err(error) => return Err(error).context(format!("read '{}'", registry_path.display())),
    };

    serde_yaml::from_str(&raw)
        .with_context(|| format!("template registry '{}' invalid", registry_path.display()))
}

fn template_id_from_seed_path(path: &Path) -> Result<String> {
    let id = path
        .file_stem()
        .and_then(|value| value.to_str())
        .with_context(|| {
            format!(
                "template file '{}' does not have a valid UTF-8 file stem",
                path.display()
            )
        })?;
    validate_template_id(id)?;
    Ok(id.to_string())
}

fn load_seed_content(path: &Path, template_id: &str, lang: &str) -> Result<TemplateSeedContent> {
    let yaml_text = fs::read_to_string(path)
        .with_context(|| format!("read template YAML '{}'", path.display()))?;
    let yaml_value = serde_yaml::from_str::<serde_yaml::Value>(&yaml_text)
        .with_context(|| format!("template YAML '{}' invalid", path.display()))?;
    let definition = serde_yaml::from_value::<CollaborationDefinition>(yaml_value.clone())
        .with_context(|| {
            format!(
                "template YAML '{}' is not a CollaborationDefinition",
                path.display()
            )
        })?;
    let definition_json = serde_json::to_value(yaml_value)?;
    let participant_summary_json = serde_json::to_value(participant_summaries(&definition))?;
    let yaml_sha256 = hex::encode(Sha256::digest(yaml_text.as_bytes()));

    Ok(TemplateSeedContent {
        template_id: template_id.to_string(),
        lang: lang.to_string(),
        name: definition.name,
        description: definition.metadata.description,
        participant_summary_json,
        definition_json,
        yaml_text,
        yaml_sha256,
        version: 1,
    })
}

fn participant_summaries(
    definition: &CollaborationDefinition,
) -> BTreeMap<String, ParticipantSummary> {
    definition
        .participants
        .iter()
        .map(|(key, participant)| {
            (
                key.clone(),
                ParticipantSummary {
                    display_name: participant.display_name.clone(),
                    description: participant.description.clone(),
                    required: participant.required,
                },
            )
        })
        .collect()
}

fn validate_template_id(value: &str) -> Result<()> {
    if is_valid_token(value) {
        Ok(())
    } else {
        bail!("invalid template id '{}'", value)
    }
}

fn validate_language(value: &str) -> Result<()> {
    if !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    {
        Ok(())
    } else {
        bail!("invalid template language '{}'", value)
    }
}

fn validate_tags(tags: &[String]) -> Result<()> {
    for tag in tags {
        if !is_valid_token(tag) {
            bail!("invalid template tag '{}'", tag);
        }
    }
    Ok(())
}

fn is_valid_token(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
}

fn is_yaml_file(path: &Path) -> bool {
    path.extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| matches!(extension, "yaml" | "yml"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn seed_template_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../seeds/collaboration-templates")
    }

    #[test]
    fn loads_seed_catalog_with_projected_fields() -> Result<()> {
        let catalog = load_template_seed_catalog(seed_template_dir())?;

        assert_eq!(catalog.supported_languages, vec!["en-US", "zh-CN"]);
        assert_eq!(
            catalog
                .templates
                .iter()
                .map(|template| template.id.as_str())
                .collect::<Vec<_>>(),
            vec![
                "write-and-review",
                "parallel-expert-review",
                "solution-and-risk-review",
                "bot-human-bot-review",
                "world-cup-preview-content-production",
                "micro-merchant-event-orchestration",
                "single-bot-guided-answer",
            ]
        );

        let write_and_review = catalog
            .templates
            .iter()
            .find(|template| template.id == "write-and-review")
            .with_context(|| "missing write-and-review")?;
        assert_eq!(write_and_review.priority, 10);
        assert_eq!(write_and_review.contents.len(), 2);
        assert!(write_and_review.tags.contains(&"judge".to_string()));
        assert!(write_and_review.tags.contains(&"branching".to_string()));
        assert!(write_and_review.tags.contains(&"serial".to_string()));

        let zh_cn = write_and_review
            .contents
            .iter()
            .find(|content| content.lang == "zh-CN")
            .with_context(|| "missing zh-CN content")?;
        assert_eq!(zh_cn.template_id, "write-and-review");
        assert_eq!(zh_cn.name, "写作质检协同");
        assert_eq!(zh_cn.yaml_sha256.len(), 64);
        assert!(zh_cn.participant_summary_json.get("writer").is_some());
        assert!(zh_cn.definition_json.get("id").is_none());
        assert!(zh_cn.definition_json.get("version").is_none());

        let bot_human_bot = catalog
            .templates
            .iter()
            .find(|template| template.id == "bot-human-bot-review")
            .with_context(|| "missing bot-human-bot-review")?;
        assert_eq!(bot_human_bot.priority, 25);
        assert_eq!(bot_human_bot.contents.len(), 2);
        assert!(bot_human_bot.tags.contains(&"judge".to_string()));
        assert!(bot_human_bot.tags.contains(&"serial".to_string()));

        let human_review = bot_human_bot
            .contents
            .iter()
            .find(|content| content.lang == "zh-CN")
            .with_context(|| "missing bot-human-bot-review zh-CN content")?;
        assert_eq!(human_review.name, "Bot-Human-Bot 三节点协作");
        assert!(
            human_review
                .participant_summary_json
                .get("worker")
                .is_some()
        );
        assert_eq!(
            human_review.definition_json["runtime"]["state_machine"]["nodes"]["human_review"]
                ["kind"],
            "human_input"
        );
        assert_eq!(
            human_review.definition_json["runtime"]["state_machine"]["nodes"]["human_review"]
                ["assignee"],
            serde_json::json!({
                "type": "runtime_actor",
                "actor": "human_1001",
            })
        );
        assert_eq!(
            human_review.definition_json["runtime"]["state_machine"]["nodes"]["human_review"]
                ["notification"]["mode"],
            "direct_assignee"
        );

        Ok(())
    }
}
