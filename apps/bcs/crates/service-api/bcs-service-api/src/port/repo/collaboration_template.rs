use async_trait::async_trait;

use crate::ServiceResult;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CollaborationTemplateEntry {
    pub id: String,
    pub tags: Vec<String>,
    pub priority: u32,
    pub available_languages: Vec<String>,
}

#[async_trait]
pub trait CollaborationTemplateRepoPort: Send + Sync {
    async fn list_entries(&self) -> ServiceResult<Vec<CollaborationTemplateEntry>>;

    async fn get_raw_yaml(&self, id: &str, lang: &str) -> ServiceResult<Option<String>>;

    async fn available_languages(&self, id: &str) -> ServiceResult<Vec<String>>;

    async fn supported_languages(&self) -> ServiceResult<Vec<String>>;
}
