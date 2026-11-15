use super::*;
use std::{collections::BTreeMap, sync::{Arc, Mutex}};
use bcs_service_api::{CollaborationTemplateService, CollaborationTemplateDetail, CollaborationTemplateListResponse};

#[derive(Default)]
struct Templates {
    lists: Mutex<Vec<ListCollaborationTemplatesQuery>>,
    gets: Mutex<Vec<GetCollaborationTemplateQuery>>,
}

#[async_trait::async_trait]
impl CollaborationTemplateService for Templates {
    async fn list_templates(&self, query: ListCollaborationTemplatesQuery) -> Result<CollaborationTemplateListResponse, CollaborationTemplateError> {
        self.lists.lock().unwrap().push(query);
        Ok(CollaborationTemplateListResponse { templates: Vec::new(), tag_labels: BTreeMap::new(),
            default_language: "en-US".into(), supported_languages: vec!["en-US".into()] })
    }
    async fn get_template(&self, query: GetCollaborationTemplateQuery) -> Result<CollaborationTemplateDetail, CollaborationTemplateError> {
        let id = query.template_id.clone();
        self.gets.lock().unwrap().push(query);
        if id == "missing" { return Err(CollaborationTemplateError::NotFound(id)); }
        Ok(CollaborationTemplateDetail { id, lang: "en-US".into(), name: "Fixture".into(),
            yaml: "name: Fixture\n".into(), definition: serde_json::json!({"name":"Fixture"}) })
    }
}

#[tokio::test]
async fn coverage_templates_forward_query_and_serialize_yaml_and_json() {
    let templates = Arc::new(Templates::default());
    let state = HttpAppState::new(bcs_services_container::Services::builder()
        .collaboration_templates(templates.clone()).build_for_test());
    let mut headers = HeaderMap::new(); headers.insert(ACCEPT_LANGUAGE, HeaderValue::from_static("zh-CN,en-US;q=0.8"));
    let response = list_templates(State(state.clone()), Query(ListTemplatesQuery {
        lang: Some("en-US".into()), tags: Some(" review, ,chat ".into()),
    }), headers.clone()).await.unwrap().into_response();
    assert_eq!(response.status(), StatusCode::OK);
    let queries = templates.lists.lock().unwrap();
    assert_eq!(queries[0].tags, ["review", "chat"]);
    assert_eq!(queries[0].requested_language.as_deref(), Some("en-US"));
    assert_eq!(queries[0].accept_language.as_deref(), Some("zh-CN,en-US;q=0.8"));
    drop(queries);
    for format in [None, Some("json".into())] {
        let response = get_template(State(state.clone()), Path("fixture".into()),
            Query(GetTemplateQuery { lang: None, format: format.clone() }), headers.clone()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let content_type = response.headers()[CONTENT_TYPE].to_str().unwrap().to_string();
        if format.is_none() {
            assert_eq!(content_type, "text/yaml; charset=utf-8");
            assert_eq!(response.headers()[CONTENT_LANGUAGE], "en-US");
            assert_eq!(response.headers()["x-template-id"], "fixture");
            assert_eq!(response.headers()["x-template-lang"], "en-US");
        } else { assert!(content_type.starts_with("application/json")); }
        let bytes = axum::body::to_bytes(response.into_body(), 4096).await.unwrap();
        if format.is_none() { assert_eq!(bytes.as_ref(), b"name: Fixture\n"); }
        else { assert_eq!(serde_json::from_slice::<serde_json::Value>(&bytes).unwrap()["definition"]["name"], "Fixture"); }
    }
    assert_eq!(templates.gets.lock().unwrap().len(), 2);
    assert!(get_template(State(state.clone()), Path("fixture".into()), Query(GetTemplateQuery {
        lang: None, format: Some("xml".into()),
    }), HeaderMap::new()).await.is_err());
    assert_eq!(templates.gets.lock().unwrap().len(), 2, "invalid format must be rejected before calling the service");
    assert!(get_template(State(state), Path("missing".into()), Query(GetTemplateQuery {
        lang: None, format: None,
    }), HeaderMap::new()).await.is_err());
    assert!(parse_tags(None).is_empty());
    let mut invalid = HeaderMap::new(); invalid.insert(ACCEPT_LANGUAGE, HeaderValue::from_bytes(&[0xff]).unwrap());
    assert!(accept_language(&invalid).is_none());
}

#[tokio::test]
async fn coverage_template_errors_keep_status_and_machine_readable_codes() {
    use CollaborationTemplateError as E;
    for (error, status, code) in [
        (E::NotFound("missing".into()), StatusCode::NOT_FOUND, "TEMPLATE_NOT_FOUND"),
        (E::LanguageNotAvailable { id: "fixture".into(), requested: "zh-CN".into() }, StatusCode::NOT_FOUND, "LANGUAGE_NOT_AVAILABLE"),
        (E::InvalidFormat("xml".into()), StatusCode::BAD_REQUEST, "INVALID_TEMPLATE_FORMAT"),
        (E::InvalidTags("bad".into()), StatusCode::BAD_REQUEST, "INVALID_TEMPLATE_TAGS"),
        (E::InvalidLanguage("bad".into()), StatusCode::BAD_REQUEST, "INVALID_TEMPLATE_LANGUAGE"),
        (E::RegistryInvalid("bad".into()), StatusCode::INTERNAL_SERVER_ERROR, "TEMPLATE_REGISTRY_INVALID"),
        (E::YamlInvalid("bad".into()), StatusCode::INTERNAL_SERVER_ERROR, "TEMPLATE_YAML_INVALID"),
        (E::Io("unavailable".into()), StatusCode::INTERNAL_SERVER_ERROR, "TEMPLATE_IO_ERROR"),
    ] {
        let expected = error.to_string();
        let response = TemplateRouteError::from(error).into_response();
        assert_eq!(response.status(), status);
        let body = axum::body::to_bytes(response.into_body(), 4096).await.unwrap();
        let body: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(body["error"]["code"], code);
        assert_eq!(body["error"]["message"], expected);
    }
    assert!(yaml_response("invalid\r\nheader".into(), "en-US".into(), String::new()).is_err());
    assert!(yaml_response("fixture".into(), "bad\nlang".into(), String::new()).is_err());
}
