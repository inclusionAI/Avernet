use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
use async_trait::async_trait;
use axum::{body::{Body, to_bytes}, http::{Request, StatusCode}};
use bcs_api_http::v1::collaboration_drafts::router;
use bcs_service_api::application::collaboration_draft::*;
use bcs_service_api::{CollaborationDefinitionValidationOutcome, CollaborationDefinitionValidationSummary};
use serde_json::{Value, json};
use tower::ServiceExt;

const ID: &str = "750c52c6-60e2-4249-8ee5-1fb8cb9402ab";
struct Drafts { revoked: AtomicBool }
fn revision() -> DraftRevision {
    let fixture: Value = serde_json::from_str(include_str!(concat!(env!("CARGO_MANIFEST_DIR"),
        "/../../../../tests/fixtures/collaboration-definition-draft/indentation-regression.json"))).unwrap();
    let mut value = fixture["revision"].clone(); value["draft_id"] = json!(ID);
    value["created_by"] = json!("author-bot");
    value["created_at"] = json!("2026-10-22T00:00:00Z");
    serde_json::from_value(value).unwrap()
}
#[async_trait]
impl CollaborationDraftService for Drafts {
    async fn read(&self, id: &str, number: u64) -> Result<DraftRevision, DraftError> {
        if self.revoked.load(Ordering::SeqCst) || id != ID || number != 1 {
            return Err(DraftError::Unavailable);
        }
        Ok(revision())
    }
    async fn validate(&self, id: &str, number: u64) -> Result<DraftValidation, DraftError> {
        let revision = self.read(id, number).await?;
        Ok(DraftValidation { draft_id: id.into(), revision: number, content_hash: revision.content_hash,
            validated_at: "2026-10-22T00:00:00Z".into(), validator: "test-validator".into(),
            validation: CollaborationDefinitionValidationOutcome { valid: true, errors: vec![], warnings: vec![],
                summary: CollaborationDefinitionValidationSummary::default(), participants: vec![], graph: None, definition: None } })
    }
    async fn create(&self, _: &str, _: &str, _: DraftContent) -> Result<SavedDraftRevision, DraftError> { panic!("management not exposed") }
    async fn update(&self, _: &str, _: &str, _: u64, _: DraftContent) -> Result<DraftRevision, DraftError> { panic!("management not exposed") }
    async fn metadata(&self, _: &str, _: &str) -> Result<DraftMetadata, DraftError> { panic!("management not exposed") }
    async fn reference(&self, _: &str, _: &str, _: u64) -> Result<DraftReference, DraftError> { panic!("management not exposed") }
    async fn revoke(&self, _: &str, _: &str, _: u64) -> Result<(), DraftError> { panic!("management not exposed") }
    async fn delete(&self, _: &str, _: &str) -> Result<(), DraftError> { panic!("management not exposed") }
}
fn request(method: &str, suffix: &str, body: &str) -> Request<Body> {
    let request = Request::builder().method(method).uri(format!("/api/v1/collaboration/drafts/{ID}{suffix}"));
    request.body(Body::from(body.to_string())).unwrap()
}
async fn body(response: axum::response::Response) -> Value {
    serde_json::from_slice(&to_bytes(response.into_body(), 100_000).await.unwrap()).unwrap()
}

#[tokio::test]
async fn fixed_revision_is_public_and_preserves_source_and_revocation() {
    let service = Arc::new(Drafts { revoked: AtomicBool::new(false) });
    let app = router(Some(service.clone()));
    for (method, path) in [("GET", "/revisions/1"), ("POST", "/revisions/1/validate")] {
        let response = app.clone().oneshot(request(method, path, "")).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()["cache-control"], "no-store");
        let data = body(response).await;
        assert_eq!(data["code"], 20000);
        assert_eq!(data["data"]["draft_id"], ID);
        assert_eq!(data["data"]["revision"], 1);
        assert_eq!(data["data"]["content_hash"], revision().content_hash);
        if method == "GET" {
            assert_eq!(data["data"]["definition_yaml"], revision().definition_yaml);
            assert_eq!(revision().definition_yaml.as_bytes().len(), 2174);
        }
    }
    for number in [0, 2] {
        let response = app.clone().oneshot(request("GET", &format!("/revisions/{number}"), "")).await.unwrap();
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    }
    service.revoked.store(true, Ordering::SeqCst);
    let response = app.oneshot(request("GET", "/revisions/1", "")).await.unwrap();
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn rejects_body_and_management_and_reports_unavailable_service() {
    let app = router(Some(Arc::new(Drafts { revoked: AtomicBool::new(false) })));
    let response = app.clone().oneshot(request("POST", "/revisions/1/validate", "secret")).await.unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(!body(response).await.to_string().contains("secret"));
    for (method, path) in [("PUT", ""), ("DELETE", ""), ("POST", "/revisions"), ("GET", "/revisions/1/reference"), ("DELETE", "/revisions/1/share")] {
        assert_eq!(app.clone().oneshot(request(method, path, "")).await.unwrap().status(), StatusCode::NOT_FOUND);
    }
    let response = router(None).oneshot(request("GET", "/revisions/1", "")).await.unwrap();
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(body(response).await["data"]["error_code"], "DRAFT_SERVICE_UNAVAILABLE");
}
