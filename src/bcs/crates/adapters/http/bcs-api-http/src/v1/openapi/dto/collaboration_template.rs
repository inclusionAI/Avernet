use bcs_service_api::application::collaboration_template::CollaborationTemplateFormat;
use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListCollaborationTemplatesQuery {
    #[serde(default)]
    pub lang: Option<String>,
    #[serde(default)]
    pub tags: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GetCollaborationTemplateQuery {
    #[serde(default)]
    pub lang: Option<String>,
    #[serde(default = "default_format")]
    pub format: CollaborationTemplateFormatQuery,
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CollaborationTemplateFormatQuery {
    Yaml,
    Json,
}

fn default_format() -> CollaborationTemplateFormatQuery {
    CollaborationTemplateFormatQuery::Yaml
}

impl From<CollaborationTemplateFormatQuery> for CollaborationTemplateFormat {
    fn from(value: CollaborationTemplateFormatQuery) -> Self {
        match value {
            CollaborationTemplateFormatQuery::Yaml => Self::Yaml,
            CollaborationTemplateFormatQuery::Json => Self::Json,
        }
    }
}