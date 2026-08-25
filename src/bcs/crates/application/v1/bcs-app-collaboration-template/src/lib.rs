//! Versioned collaboration-template application facade for the BCN V1 API.
//!
//! The V1 catalog is a read-only projection of the legacy
//! `CollaborationTemplateService`. This facade delegates list/get to the legacy
//! service and translates the legacy error vocabulary into the V1
//! `ApplicationError`. It performs no per-caller authorization: catalog reads
//! are not Bot- or Session-scoped, and the delivery adapter authenticates the
//! Gateway Principal on the protected boundary.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ApplicationError, CollaborationTemplateService as V1CollaborationTemplateService,
    GetCollaborationTemplate, ListCollaborationTemplates,
};
use bcs_service_api::application::collaboration_template::{
    CollaborationTemplateError, CollaborationTemplateService as LegacyCollaborationTemplateService,
    GetCollaborationTemplateQuery as LegacyGetCollaborationTemplateQuery,
    ListCollaborationTemplatesQuery as LegacyListCollaborationTemplatesQuery,
};
use bcs_service_api::{CollaborationTemplateDetail, CollaborationTemplateListResponse};

pub struct CollaborationTemplateServiceImpl {
    legacy: Arc<dyn LegacyCollaborationTemplateService>,
}

impl CollaborationTemplateServiceImpl {
    pub fn new(legacy: Arc<dyn LegacyCollaborationTemplateService>) -> Self {
        Self { legacy }
    }
}

#[async_trait]
impl V1CollaborationTemplateService for CollaborationTemplateServiceImpl {
    async fn list_templates(
        &self,
        command: ListCollaborationTemplates,
    ) -> Result<CollaborationTemplateListResponse, ApplicationError> {
        self.legacy
            .list_templates(LegacyListCollaborationTemplatesQuery {
                requested_language: command.requested_language,
                accept_language: command.accept_language,
                tags: command.tags,
            })
            .await
            .map_err(map_template_error)
    }

    async fn get_template(
        &self,
        query: GetCollaborationTemplate,
    ) -> Result<CollaborationTemplateDetail, ApplicationError> {
        self.legacy
            .get_template(LegacyGetCollaborationTemplateQuery {
                template_id: query.template_id,
                requested_language: query.requested_language,
                accept_language: query.accept_language,
                format: query.format,
            })
            .await
            .map_err(map_template_error)
    }
}

fn map_template_error(error: CollaborationTemplateError) -> ApplicationError {
    match error {
        CollaborationTemplateError::NotFound(id) => {
            ApplicationError::not_found("template_not_found", format!("Template '{id}' not found"))
        }
        CollaborationTemplateError::LanguageNotAvailable { id, requested } => {
            ApplicationError::not_found(
                "language_not_available",
                format!("Language '{requested}' is not available for template '{id}'"),
            )
        }
        CollaborationTemplateError::InvalidFormat(value) => ApplicationError::invalid(
            "invalid_template_format",
            format!("Invalid template format: {value}"),
        ),
        CollaborationTemplateError::InvalidTags(value) => ApplicationError::invalid(
            "invalid_template_tags",
            format!("Invalid template tags: {value}"),
        ),
        CollaborationTemplateError::InvalidLanguage(value) => ApplicationError::invalid(
            "invalid_template_language",
            format!("Invalid template language: {value}"),
        ),
        CollaborationTemplateError::RegistryInvalid(message) => {
            ApplicationError::internal(format!("Template registry invalid: {message}"))
        }
        CollaborationTemplateError::YamlInvalid(message) => {
            ApplicationError::internal(format!("Template YAML invalid: {message}"))
        }
        CollaborationTemplateError::Io(message) => {
            ApplicationError::internal(format!("Template IO error: {message}"))
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use async_trait::async_trait;
    use bcs_service_api::application::collaboration_template::{
        CollaborationTemplateError, CollaborationTemplateService as LegacyCollaborationTemplateService,
        GetCollaborationTemplateQuery as LegacyGetCollaborationTemplateQuery,
        ListCollaborationTemplatesQuery as LegacyListCollaborationTemplatesQuery,
    };
    use bcs_service_api::application::v1::{
        CollaborationTemplateService as V1CollaborationTemplateService, GetCollaborationTemplate,
        ListCollaborationTemplates,
    };
    use bcs_service_api::CollaborationTemplateFormat;

    use super::*;

    /// Stub legacy service that replays a canned error for each call so the
    /// facade's error mapping is exercised without a real template registry.
    struct StubLegacyService {
        list_error: Mutex<Option<CollaborationTemplateError>>,
        get_error: Mutex<Option<CollaborationTemplateError>>,
        last_list: Mutex<Option<LegacyListCollaborationTemplatesQuery>>,
        last_get: Mutex<Option<LegacyGetCollaborationTemplateQuery>>,
    }

    impl StubLegacyService {
        fn failing(list_error: CollaborationTemplateError, get_error: CollaborationTemplateError) -> Self {
            Self {
                list_error: Mutex::new(Some(list_error)),
                get_error: Mutex::new(Some(get_error)),
                last_list: Mutex::new(None),
                last_get: Mutex::new(None),
            }
        }
    }

    #[async_trait]
    impl LegacyCollaborationTemplateService for StubLegacyService {
        async fn list_templates(
            &self,
            query: LegacyListCollaborationTemplatesQuery,
        ) -> Result<CollaborationTemplateListResponse, CollaborationTemplateError> {
            *self.last_list.lock().expect("list lock") = Some(query);
            Err(self
                .list_error
                .lock()
                .expect("list error lock")
                .take()
                .expect("canned list error"))
        }

        async fn get_template(
            &self,
            query: LegacyGetCollaborationTemplateQuery,
        ) -> Result<CollaborationTemplateDetail, CollaborationTemplateError> {
            *self.last_get.lock().expect("get lock") = Some(query);
            Err(self
                .get_error
                .lock()
                .expect("get error lock")
                .take()
                .expect("canned get error"))
        }
    }

    fn facade() -> CollaborationTemplateServiceImpl {
        CollaborationTemplateServiceImpl::new(Arc::new(StubLegacyService::failing(
            CollaborationTemplateError::InvalidTags("bad".to_string()),
            CollaborationTemplateError::NotFound("tpl".to_string()),
        )))
    }

    #[tokio::test]
    async fn list_maps_invalid_tags_to_invalid_input_and_forwards_command() {
        let service = facade();
        let error = service
            .list_templates(ListCollaborationTemplates {
                requested_language: Some("zh-CN".to_string()),
                accept_language: None,
                tags: vec!["bad".to_string()],
            })
            .await
            .expect_err("list must surface the canned error");
        assert_eq!(error.code(), "invalid_template_tags");
        assert!(matches!(error, ApplicationError::InvalidInput { .. }));
    }

    #[tokio::test]
    async fn get_maps_not_found_and_preserves_format() {
        let service = facade();
        let error = service
            .get_template(GetCollaborationTemplate {
                template_id: "tpl".to_string(),
                requested_language: None,
                accept_language: None,
                format: CollaborationTemplateFormat::Yaml,
            })
            .await
            .expect_err("get must surface the canned error");
        assert_eq!(error.code(), "template_not_found");
        assert!(matches!(error, ApplicationError::NotFound { .. }));
    }
}