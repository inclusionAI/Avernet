use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    sync::{Arc, RwLock},
};

use async_trait::async_trait;
use bcs_domain::CollaborationDefinition;
use bcs_service_api::{
    CollaborationTemplateDetail, CollaborationTemplateEntry, CollaborationTemplateError,
    CollaborationTemplateListResponse, CollaborationTemplateParticipantSummary,
    CollaborationTemplateRepoPort, CollaborationTemplateService, CollaborationTemplateSummary,
    GetCollaborationTemplateQuery, ListCollaborationTemplatesQuery, ServiceError, ServiceResult,
};
use serde::Deserialize;
use tracing::warn;

const FALLBACK_LANGUAGE: &str = "en-US";
const DEFAULT_PRIORITY: u32 = u32::MAX;

#[derive(Debug, Clone)]
pub struct FileCollaborationTemplateRepo {
    base_dir: PathBuf,
    index_cache: Arc<RwLock<Option<TemplateIndex>>>,
}

impl FileCollaborationTemplateRepo {
    pub fn new(base_dir: impl Into<PathBuf>) -> Self {
        Self {
            base_dir: base_dir.into(),
            index_cache: Arc::new(RwLock::new(None)),
        }
    }

    fn load_index(&self) -> ServiceResult<TemplateIndex> {
        let cached_index = self
            .index_cache
            .read()
            .map_err(|error| {
                ServiceError::InternalError(format!("template index cache poisoned: {error}"))
            })?
            .clone();
        if let Some(index) = cached_index {
            return Ok(index);
        }

        let index = self.scan_index()?;
        let mut cache = self
            .index_cache
            .write()
            .map_err(|error| {
                ServiceError::InternalError(format!("template index cache poisoned: {error}"))
            })?;
        if let Some(index) = cache.clone() {
            return Ok(index);
        }
        *cache = Some(index.clone());
        Ok(index)
    }

    fn scan_index(&self) -> ServiceResult<TemplateIndex> {
        let registry = self.load_registry()?;
        let mut templates: BTreeMap<String, IndexedTemplate> = BTreeMap::new();
        let mut supported_languages = BTreeSet::new();

        let dir_entries = match fs::read_dir(&self.base_dir) {
            Ok(entries) => entries,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(TemplateIndex::default());
            }
            Err(error) => return Err(ServiceError::IoError(error)),
        };

        for dir_entry in dir_entries {
            let dir_entry = dir_entry?;
            if !dir_entry.file_type()?.is_dir() {
                continue;
            }

            let lang = dir_entry.file_name();
            let lang = match lang.to_str() {
                Some(value) if is_valid_language(value) => value.to_string(),
                _ => continue,
            };
            supported_languages.insert(lang.clone());

            for file_entry in fs::read_dir(dir_entry.path())? {
                let file_entry = file_entry?;
                if !file_entry.file_type()?.is_file() || !is_yaml_file(&file_entry.path()) {
                    continue;
                }

                let Some(template_id) = template_id_from_path(&file_entry.path()) else {
                    continue;
                };
                let template = templates
                    .entry(template_id.clone())
                    .or_insert_with(|| indexed_template(&template_id, &registry));
                template
                    .language_paths
                    .insert(lang.clone(), file_entry.path());
            }
        }

        for (id, registry_entry) in registry.templates {
            let priority = registry_entry.priority();
            templates.entry(id.clone()).or_insert_with(|| IndexedTemplate {
                id,
                tags: registry_entry.tags,
                priority,
                language_paths: BTreeMap::new(),
            });
        }

        Ok(TemplateIndex {
            templates,
            supported_languages: supported_languages.into_iter().collect(),
        })
    }

    fn load_registry(&self) -> ServiceResult<RegistryFile> {
        let registry_path = self.base_dir.join("registry.yaml");
        let raw = match fs::read_to_string(&registry_path) {
            Ok(raw) => raw,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(RegistryFile::default());
            }
            Err(error) => return Err(ServiceError::IoError(error)),
        };

        serde_yaml::from_str(&raw).map_err(|error| {
            ServiceError::InternalError(format!(
                "template registry '{}' invalid: {error}",
                registry_path.display()
            ))
        })
    }
}

#[async_trait]
impl CollaborationTemplateRepoPort for FileCollaborationTemplateRepo {
    async fn list_entries(&self) -> ServiceResult<Vec<CollaborationTemplateEntry>> {
        let repo = self.clone();
        spawn_blocking_service(move || {
            let index = repo.load_index()?;
            Ok(index
                .templates
                .into_values()
                .map(|template| CollaborationTemplateEntry {
                    id: template.id,
                    tags: template.tags,
                    priority: template.priority,
                    available_languages: template.language_paths.into_keys().collect(),
                })
                .collect())
        })
        .await
    }

    async fn get_raw_yaml(&self, id: &str, lang: &str) -> ServiceResult<Option<String>> {
        let repo = self.clone();
        let id = id.to_string();
        let lang = lang.to_string();
        spawn_blocking_service(move || {
            let index = repo.load_index()?;
            let Some(path) = index
                .templates
                .get(&id)
                .and_then(|template| template.language_paths.get(&lang))
            else {
                return Ok(None);
            };

            Ok(Some(fs::read_to_string(path)?))
        })
        .await
    }

    async fn available_languages(&self, id: &str) -> ServiceResult<Vec<String>> {
        let repo = self.clone();
        let id = id.to_string();
        spawn_blocking_service(move || {
            let index = repo.load_index()?;
            Ok(index
                .templates
                .get(&id)
                .map(|template| template.language_paths.keys().cloned().collect())
                .unwrap_or_default())
        })
        .await
    }

    async fn supported_languages(&self) -> ServiceResult<Vec<String>> {
        let repo = self.clone();
        spawn_blocking_service(move || Ok(repo.load_index()?.supported_languages)).await
    }
}

async fn spawn_blocking_service<T, F>(operation: F) -> ServiceResult<T>
where
    T: Send + 'static,
    F: FnOnce() -> ServiceResult<T> + Send + 'static,
{
    tokio::task::spawn_blocking(operation)
        .await
        .map_err(|error| {
            ServiceError::InternalError(format!("template blocking task failed: {error}"))
        })?
}

#[derive(Clone)]
pub struct CollaborationTemplateServiceImpl {
    repo: Arc<dyn CollaborationTemplateRepoPort>,
    default_language: String,
    judge_templates_enabled: bool,
    tag_labels: BTreeMap<String, BTreeMap<String, String>>,
}

impl CollaborationTemplateServiceImpl {
    pub fn new(
        repo: Arc<dyn CollaborationTemplateRepoPort>,
        default_language: impl Into<String>,
    ) -> Self {
        Self {
            repo,
            default_language: default_language.into(),
            judge_templates_enabled: false,
            tag_labels: default_tag_labels(),
        }
    }

    pub fn with_judge_templates_enabled(mut self, enabled: bool) -> Self {
        self.judge_templates_enabled = enabled;
        self
    }

    fn language_candidates(
        &self,
        requested_language: Option<&str>,
        accept_language: Option<&str>,
        supported_languages: &[String],
    ) -> Result<Vec<String>, CollaborationTemplateError> {
        let mut candidates = Vec::new();

        if let Some(lang) = requested_language.and_then(non_empty_trimmed) {
            if !is_valid_language(lang) {
                return Err(CollaborationTemplateError::InvalidLanguage(
                    lang.to_string(),
                ));
            }
            push_language_with_base(&mut candidates, lang);
        } else if let Some(accept_language) = accept_language {
            for lang in parse_accept_language(accept_language) {
                if is_valid_language(&lang) {
                    push_language_with_base(&mut candidates, &lang);
                }
            }
        }

        push_language_with_base(&mut candidates, &self.default_language);
        push_language_with_base(&mut candidates, FALLBACK_LANGUAGE);

        for lang in supported_languages {
            push_unique(&mut candidates, lang);
        }

        Ok(candidates)
    }

    async fn load_definition(
        &self,
        id: &str,
        lang: &str,
    ) -> Result<(String, CollaborationDefinition, serde_json::Value), CollaborationTemplateError>
    {
        let yaml = self
            .repo
            .get_raw_yaml(id, lang)
            .await
            .map_err(|error| CollaborationTemplateError::Io(error.to_string()))?
            .ok_or_else(|| CollaborationTemplateError::LanguageNotAvailable {
                id: id.to_string(),
                requested: lang.to_string(),
            })?;

        let yaml_value = serde_yaml::from_str::<serde_yaml::Value>(&yaml)
            .map_err(|error| CollaborationTemplateError::YamlInvalid(error.to_string()))?;
        let definition = serde_yaml::from_value::<CollaborationDefinition>(yaml_value.clone())
            .map_err(|error| CollaborationTemplateError::YamlInvalid(error.to_string()))?;
        let definition_value = serde_json::to_value(yaml_value)
            .map_err(|error| CollaborationTemplateError::YamlInvalid(error.to_string()))?;

        Ok((yaml, definition, definition_value))
    }
}

#[async_trait]
impl CollaborationTemplateService for CollaborationTemplateServiceImpl {
    async fn list_templates(
        &self,
        query: ListCollaborationTemplatesQuery,
    ) -> Result<CollaborationTemplateListResponse, CollaborationTemplateError> {
        validate_tags(&query.tags)?;

        let supported_languages = self
            .repo
            .supported_languages()
            .await
            .map_err(|error| CollaborationTemplateError::RegistryInvalid(error.to_string()))?;
        let language_candidates = self.language_candidates(
            query.requested_language.as_deref(),
            query.accept_language.as_deref(),
            &supported_languages,
        )?;
        let tag_filter: BTreeSet<&str> = query.tags.iter().map(String::as_str).collect();

        let mut summaries = Vec::new();
        for entry in self
            .repo
            .list_entries()
            .await
            .map_err(|error| CollaborationTemplateError::RegistryInvalid(error.to_string()))?
        {
            if !tag_filter.is_empty()
                && !entry.tags.iter().any(|tag| tag_filter.contains(tag.as_str()))
            {
                continue;
            }

            let available_languages =
                order_languages_with_default(entry.available_languages, &self.default_language);
            let Some(lang) = pick_language(&language_candidates, &available_languages) else {
                continue;
            };
            let (_yaml, definition, _definition_value) =
                match self.load_definition(&entry.id, &lang).await {
                    Ok(definition) => definition,
                    Err(
                        error @ (CollaborationTemplateError::LanguageNotAvailable { .. }
                        | CollaborationTemplateError::YamlInvalid(_)
                        | CollaborationTemplateError::Io(_)),
                    ) => {
                        warn!(
                            template_id = %entry.id,
                            lang = %lang,
                            error = %error,
                            "Skipping invalid collaboration template in list response"
                        );
                        continue;
                    }
                    Err(error) => return Err(error),
                };
            let judge_requires_llm = !self.judge_templates_enabled && definition.uses_judge();
            let name = if judge_requires_llm {
                append_llm_required_hint(definition.name, &lang)
            } else {
                definition.name
            };
            let priority = if judge_requires_llm {
                DEFAULT_PRIORITY
            } else {
                entry.priority
            };

            summaries.push(CollaborationTemplateSummary {
                id: entry.id,
                name,
                description: definition.metadata.description.unwrap_or_default(),
                participants: definition
                    .participants
                    .into_iter()
                    .map(|(key, participant)| {
                        (
                            key,
                            CollaborationTemplateParticipantSummary {
                                display_name: participant.display_name,
                                description: participant.description,
                                required: participant.required,
                            },
                        )
                    })
                    .collect(),
                tags: entry.tags,
                priority,
                available_languages,
            });
        }

        summaries.sort_by(|left, right| {
            left.priority
                .cmp(&right.priority)
                .then_with(|| left.id.cmp(&right.id))
        });

        Ok(CollaborationTemplateListResponse {
            templates: summaries,
            tag_labels: self.tag_labels.clone(),
            default_language: self.default_language.clone(),
            supported_languages: order_languages_with_default(
                supported_languages,
                &self.default_language,
            ),
        })
    }

    async fn get_template(
        &self,
        query: GetCollaborationTemplateQuery,
    ) -> Result<CollaborationTemplateDetail, CollaborationTemplateError> {
        if !is_valid_template_id(&query.template_id) {
            return Err(CollaborationTemplateError::NotFound(query.template_id));
        }

        let supported_languages = self
            .repo
            .supported_languages()
            .await
            .map_err(|error| CollaborationTemplateError::RegistryInvalid(error.to_string()))?;
        let available_languages = self
            .repo
            .available_languages(&query.template_id)
            .await
            .map_err(|error| CollaborationTemplateError::RegistryInvalid(error.to_string()))?;
        if available_languages.is_empty() {
            return Err(CollaborationTemplateError::NotFound(query.template_id));
        }

        let language_candidates = self.language_candidates(
            query.requested_language.as_deref(),
            query.accept_language.as_deref(),
            &supported_languages,
        )?;
        let available_languages =
            order_languages_with_default(available_languages, &self.default_language);
        let Some(lang) = pick_language(&language_candidates, &available_languages) else {
            let requested = query
                .requested_language
                .or(query.accept_language)
                .unwrap_or_else(|| self.default_language.clone());
            return Err(CollaborationTemplateError::LanguageNotAvailable {
                id: query.template_id,
                requested,
            });
        };
        let (yaml, definition, definition_value) =
            self.load_definition(&query.template_id, &lang).await?;

        Ok(CollaborationTemplateDetail {
            id: query.template_id,
            lang,
            name: definition.name,
            yaml,
            definition: definition_value,
        })
    }
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

#[derive(Debug, Clone)]
struct IndexedTemplate {
    id: String,
    tags: Vec<String>,
    priority: u32,
    language_paths: BTreeMap<String, PathBuf>,
}

#[derive(Debug, Clone, Default)]
struct TemplateIndex {
    templates: BTreeMap<String, IndexedTemplate>,
    supported_languages: Vec<String>,
}

fn indexed_template(id: &str, registry: &RegistryFile) -> IndexedTemplate {
    let registry_entry = registry.templates.get(id).cloned().unwrap_or_default();
    let priority = registry_entry.priority();
    IndexedTemplate {
        id: id.to_string(),
        tags: registry_entry.tags,
        priority,
        language_paths: BTreeMap::new(),
    }
}

fn template_id_from_path(path: &Path) -> Option<String> {
    let id = path.file_stem()?.to_str()?;
    is_valid_template_id(id).then(|| id.to_string())
}

fn is_yaml_file(path: &Path) -> bool {
    path.extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| matches!(extension, "yaml" | "yml"))
}

fn validate_tags(tags: &[String]) -> Result<(), CollaborationTemplateError> {
    for tag in tags {
        if !is_valid_tag(tag) {
            return Err(CollaborationTemplateError::InvalidTags(tag.clone()));
        }
    }
    Ok(())
}

fn is_valid_template_id(value: &str) -> bool {
    is_token(value)
}

fn is_valid_tag(value: &str) -> bool {
    is_token(value)
}

fn is_valid_language(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

fn is_token(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
}

fn non_empty_trimmed(value: &str) -> Option<&str> {
    let trimmed = value.trim();
    (!trimmed.is_empty()).then_some(trimmed)
}

fn parse_accept_language(header: &str) -> Vec<String> {
    let mut languages = Vec::new();
    for (index, item) in header.split(',').enumerate() {
        let mut parts = item.trim().split(';');
        let Some(lang) = parts.next().and_then(non_empty_trimmed) else {
            continue;
        };
        if lang == "*" {
            continue;
        }
        let quality = parts
            .find_map(|part| part.trim().strip_prefix("q="))
            .and_then(|value| value.parse::<f32>().ok())
            .unwrap_or(1.0);
        languages.push((lang.to_string(), quality, index));
    }
    languages.sort_by(|left, right| {
        right
            .1
            .partial_cmp(&left.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.2.cmp(&right.2))
    });
    languages.into_iter().map(|(lang, _quality, _index)| lang).collect()
}

fn push_language_with_base(candidates: &mut Vec<String>, lang: &str) {
    push_unique(candidates, lang);
    if let Some(base) = lang.split_once('-').map(|(base, _region)| base) {
        push_unique(candidates, base);
    }
}

fn push_unique(values: &mut Vec<String>, value: &str) {
    if !values.iter().any(|existing| existing == value) {
        values.push(value.to_string());
    }
}

fn pick_language(candidates: &[String], available_languages: &[String]) -> Option<String> {
    candidates
        .iter()
        .find(|candidate| available_languages.iter().any(|lang| lang == *candidate))
        .cloned()
}

fn order_languages_with_default(
    languages: impl IntoIterator<Item = String>,
    default_language: &str,
) -> Vec<String> {
    let mut unique: BTreeSet<String> = languages.into_iter().collect();
    let mut ordered = Vec::new();
    if unique.remove(default_language) {
        ordered.push(default_language.to_string());
    }
    if FALLBACK_LANGUAGE != default_language && unique.remove(FALLBACK_LANGUAGE) {
        ordered.push(FALLBACK_LANGUAGE.to_string());
    }
    ordered.extend(unique);
    ordered
}

fn default_tag_labels() -> BTreeMap<String, BTreeMap<String, String>> {
    [
        ("judge", "自动评审", "Judge"),
        ("branching", "条件分支", "Branching"),
        ("parallel", "并行", "Parallel"),
        ("serial", "串行", "Serial"),
        ("single-step", "单步", "Single Step"),
    ]
    .into_iter()
    .map(|(tag, zh_cn, en_us)| {
        (
            tag.to_string(),
            BTreeMap::from([
                ("zh-CN".to_string(), zh_cn.to_string()),
                ("en-US".to_string(), en_us.to_string()),
            ]),
        )
    })
    .collect()
}

fn append_llm_required_hint(mut name: String, lang: &str) -> String {
    if lang.starts_with("zh") {
        name.push_str("（需要启用 LLM）");
    } else {
        name.push_str(" (requires LLM)");
    }
    name
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::{
        CollaborationTemplateFormat, GetCollaborationTemplateQuery,
        ListCollaborationTemplatesQuery,
    };

    fn seed_template_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../seeds/collaboration-templates")
    }

    const MINIMAL_TEMPLATE_YAML: &str = r#"
name: Good Template
metadata:
  description: A valid minimal template.
participants:
  assistant:
    display_name: Assistant
    description: Answers the user request.
    required: true
runtime:
  kind: state_machine
  state_machine:
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: assistant
        instruction: Answer the user query.
        final_output: true
"#;

    fn write_template(
        root: &Path,
        lang: &str,
        id: &str,
        yaml: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let lang_dir = root.join(lang);
        fs::create_dir_all(&lang_dir)?;
        fs::write(lang_dir.join(format!("{id}.yaml")), yaml)?;
        Ok(())
    }

    #[tokio::test]
    async fn lists_default_language_templates_by_priority() -> Result<(), Box<dyn std::error::Error>>
    {
        let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
        let service =
            CollaborationTemplateServiceImpl::new(repo, "zh-CN").with_judge_templates_enabled(true);

        let response = service
            .list_templates(ListCollaborationTemplatesQuery::default())
            .await?;

        assert_eq!(response.default_language, "zh-CN");
        assert_eq!(response.supported_languages, vec!["zh-CN", "en-US"]);
        assert_eq!(
            response
                .templates
                .iter()
                .map(|template| template.id.as_str())
                .collect::<Vec<_>>(),
            vec![
                "write-and-review",
                "parallel-expert-review",
                "solution-and-risk-review",
                "single-bot-guided-answer",
            ]
        );
        let first = response
            .templates
            .first()
            .ok_or_else(|| std::io::Error::other("missing first template"))?;
        assert_eq!(first.name, "写作质检协同");
        assert!(first.participants.contains_key("writer"));
        assert_eq!(response.tag_labels["judge"]["zh-CN"], "自动评审");
        assert_eq!(response.tag_labels["serial"]["zh-CN"], "串行");

        Ok(())
    }

    #[tokio::test]
    async fn list_marks_judge_templates_when_disabled() -> Result<(), Box<dyn std::error::Error>>
    {
        let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
        let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN");

        let response = service
            .list_templates(ListCollaborationTemplatesQuery::default())
            .await?;

        assert_eq!(
            response
                .templates
                .iter()
                .map(|template| template.id.as_str())
                .collect::<Vec<_>>(),
            vec![
                "parallel-expert-review",
                "solution-and-risk-review",
                "single-bot-guided-answer",
                "write-and-review",
            ]
        );
        let judge_template = response
            .templates
            .last()
            .ok_or_else(|| std::io::Error::other("missing judge template"))?;
        assert_eq!(judge_template.name, "写作质检协同（需要启用 LLM）");
        assert_eq!(judge_template.priority, DEFAULT_PRIORITY);

        let english_response = service
            .list_templates(ListCollaborationTemplatesQuery {
                requested_language: Some("en-US".to_string()),
                accept_language: None,
                tags: vec!["judge".to_string()],
            })
            .await?;
        assert_eq!(english_response.templates.len(), 1);
        assert_eq!(
            english_response.templates[0].name,
            "Write & Review (requires LLM)"
        );

        Ok(())
    }

    #[tokio::test]
    async fn filters_by_tag_and_language() -> Result<(), Box<dyn std::error::Error>> {
        let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
        let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN");

        let response = service
            .list_templates(ListCollaborationTemplatesQuery {
                requested_language: Some("en-US".to_string()),
                accept_language: None,
                tags: vec!["parallel".to_string()],
            })
            .await?;

        assert_eq!(
            response
                .templates
                .iter()
                .map(|template| template.id.as_str())
                .collect::<Vec<_>>(),
            vec![
                "parallel-expert-review",
                "solution-and-risk-review",
            ]
        );
        assert_eq!(response.templates[0].name, "Parallel Expert Review");

        Ok(())
    }

    #[tokio::test]
    async fn returns_raw_yaml_and_parsed_definition() -> Result<(), Box<dyn std::error::Error>> {
        let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
        let service =
            CollaborationTemplateServiceImpl::new(repo, "zh-CN").with_judge_templates_enabled(true);

        let detail = service
            .get_template(GetCollaborationTemplateQuery {
                template_id: "write-and-review".to_string(),
                requested_language: Some("zh-CN".to_string()),
                accept_language: None,
                format: CollaborationTemplateFormat::Json,
            })
            .await?;

        assert_eq!(detail.id, "write-and-review");
        assert_eq!(detail.lang, "zh-CN");
        assert!(detail.yaml.contains("# 写作质检协同模板。"));
        assert_eq!(detail.definition["name"], "写作质检协同");
        assert!(detail.definition.get("id").is_none());

        Ok(())
    }

    #[tokio::test]
    async fn get_returns_judge_template_when_disabled() -> Result<(), Box<dyn std::error::Error>> {
        let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
        let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN");

        let detail = service
            .get_template(GetCollaborationTemplateQuery {
                template_id: "write-and-review".to_string(),
                requested_language: Some("zh-CN".to_string()),
                accept_language: None,
                format: CollaborationTemplateFormat::Yaml,
            })
            .await?;

        assert_eq!(detail.id, "write-and-review");
        assert_eq!(detail.name, "写作质检协同");
        assert!(detail.yaml.contains("judge:"));
        Ok(())
    }

    #[tokio::test]
    async fn rejects_path_traversal_template_id() -> Result<(), Box<dyn std::error::Error>> {
        let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
        let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN");

        let error = service
            .get_template(GetCollaborationTemplateQuery {
                template_id: "..".to_string(),
                requested_language: Some("zh-CN".to_string()),
                accept_language: None,
                format: CollaborationTemplateFormat::Yaml,
            })
            .await
            .err()
            .ok_or_else(|| std::io::Error::other("expected error"))?;

        assert!(matches!(error, CollaborationTemplateError::NotFound(_)));
        Ok(())
    }

    #[tokio::test]
    async fn list_skips_malformed_template_yaml() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = tempfile::tempdir()?;
        write_template(
            temp_dir.path(),
            "zh-CN",
            "good-template",
            MINIMAL_TEMPLATE_YAML,
        )?;
        write_template(
            temp_dir.path(),
            "zh-CN",
            "broken-template",
            "name: [not valid yaml",
        )?;

        let repo = Arc::new(FileCollaborationTemplateRepo::new(temp_dir.path()));
        let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN");

        let response = service
            .list_templates(ListCollaborationTemplatesQuery::default())
            .await?;

        assert_eq!(
            response
                .templates
                .iter()
                .map(|template| template.id.as_str())
                .collect::<Vec<_>>(),
            vec!["good-template"]
        );
        Ok(())
    }

    #[tokio::test]
    async fn missing_template_dir_is_empty_and_not_found() -> Result<(), Box<dyn std::error::Error>>
    {
        let temp_dir = tempfile::tempdir()?;
        let missing_dir = temp_dir.path().join("missing");
        let repo = Arc::new(FileCollaborationTemplateRepo::new(missing_dir));
        let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN");

        let response = service
            .list_templates(ListCollaborationTemplatesQuery::default())
            .await?;
        assert!(response.templates.is_empty());
        assert!(response.supported_languages.is_empty());

        let error = service
            .get_template(GetCollaborationTemplateQuery {
                template_id: "anything".to_string(),
                requested_language: Some("zh-CN".to_string()),
                accept_language: None,
                format: CollaborationTemplateFormat::Yaml,
            })
            .await
            .err()
            .ok_or_else(|| std::io::Error::other("expected error"))?;
        assert!(matches!(error, CollaborationTemplateError::NotFound(_)));

        Ok(())
    }
}
