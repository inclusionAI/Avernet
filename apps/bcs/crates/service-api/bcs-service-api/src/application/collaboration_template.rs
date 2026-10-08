use std::collections::BTreeMap;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Default)]
pub struct ListCollaborationTemplatesQuery {
    pub requested_language: Option<String>,
    pub accept_language: Option<String>,
    pub tags: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct GetCollaborationTemplateQuery {
    pub template_id: String,
    pub requested_language: Option<String>,
    pub accept_language: Option<String>,
    pub format: CollaborationTemplateFormat,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CollaborationTemplateFormat {
    Yaml,
    Json,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CollaborationTemplateListResponse {
    pub templates: Vec<CollaborationTemplateSummary>,
    pub tag_labels: BTreeMap<String, BTreeMap<String, String>>,
    pub default_language: String,
    pub supported_languages: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CollaborationTemplateSummary {
    pub id: String,
    pub name: String,
    pub description: String,
    pub participants: BTreeMap<String, CollaborationTemplateParticipantSummary>,
    pub tags: Vec<String>,
    pub priority: u32,
    pub available_languages: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CollaborationTemplateParticipantSummary {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    pub required: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CollaborationTemplateDetail {
    pub id: String,
    pub lang: String,
    pub name: String,
    pub yaml: String,
    pub definition: Value,
}

#[derive(Debug, thiserror::Error)]
pub enum CollaborationTemplateError {
    #[error("Template '{0}' not found")]
    NotFound(String),
    #[error("Language '{requested}' is not available for template '{id}'")]
    LanguageNotAvailable { id: String, requested: String },
    #[error("Invalid template format: {0}")]
    InvalidFormat(String),
    #[error("Invalid template tags: {0}")]
    InvalidTags(String),
    #[error("Invalid template language: {0}")]
    InvalidLanguage(String),
    #[error("Template registry invalid: {0}")]
    RegistryInvalid(String),
    #[error("Template YAML invalid: {0}")]
    YamlInvalid(String),
    #[error("Template IO error: {0}")]
    Io(String),
}

#[async_trait]
pub trait CollaborationTemplateService: Send + Sync {
    async fn list_templates(
        &self,
        query: ListCollaborationTemplatesQuery,
    ) -> Result<CollaborationTemplateListResponse, CollaborationTemplateError>;

    async fn get_template(
        &self,
        query: GetCollaborationTemplateQuery,
    ) -> Result<CollaborationTemplateDetail, CollaborationTemplateError>;
}
