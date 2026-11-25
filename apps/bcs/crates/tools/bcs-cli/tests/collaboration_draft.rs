use bcs_cli::{
    BcsClient,
    collaboration_draft::{DraftCommands, execute, reference_message},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use wiremock::{
    Mock, MockServer, ResponseTemplate,
    matchers::{body_json, header, method, path},
};

const ID: &str = "01010101-0202-0303-0404-050505050505";

#[tokio::test]
async fn local_input_and_output_failures_leave_no_partial_file_or_upload() {
    let server = MockServer::start().await;
    let client = BcsClient::new(server.uri());
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("input.yaml");
    for (bytes, title) in [
        (vec![0xff], None),
        (vec![b'x'; 65_537], None),
        (vec![b'x'], Some("😀".repeat(121))),
        (vec![b'x'], Some("  ".into())),
    ] {
        std::fs::write(&file, bytes).unwrap();
        assert!(
            !execute(
                &client,
                DraftCommands::Create {
                    file: file.clone(),
                    draft_id: ID.into(),
                    title,
                }
            )
            .await
            .1
        );
    }
    assert!(server.received_requests().await.unwrap().is_empty());
    Mock::given(method("GET"))
        .respond_with(ResponseTemplate::new(200).set_body_json(source("original", 1)))
        .mount(&server)
        .await;
    let output = dir.path().join("missing-parent/out.yaml");
    assert!(
        !execute(
            &client,
            DraftCommands::Get {
                draft_id: ID.into(),
                revision: 1,
                output: output.clone(),
            }
        )
        .await
        .1
    );
    assert!(!output.exists());
    assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
}

#[tokio::test]
async fn truncated_create_response_is_unknown_and_retry_reuses_same_id() {
    let server = MockServer::start().await;
    let client = BcsClient::new(server.uri());
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("input.yaml");
    std::fs::write(&file, "original").unwrap();
    let command = || DraftCommands::Create {
        file: file.clone(),
        draft_id: ID.into(),
        title: None,
    };
    Mock::given(method("PUT"))
        .and(path(format!("/collaboration/drafts/{ID}")))
        .respond_with(ResponseTemplate::new(201).set_body_string("{\"draft_id\":"))
        .expect(1)
        .mount(&server)
        .await;
    let (result, ok) = execute(&client, command()).await;
    assert!(!ok);
    assert!(result["saved"].is_null());
    assert_eq!(result["draft_id"], ID);
    assert!(result.get("message").is_none());
    server.reset().await;
    let r = source("original", 1);
    Mock::given(method("PUT"))
        .and(path(format!("/collaboration/drafts/{ID}")))
        .respond_with(ResponseTemplate::new(200).set_body_json(r.clone()))
        .expect(1)
        .mount(&server)
        .await;
    delivery(&server, &r, true).await;
    let (result, ok) = execute(&client, command()).await;
    assert!(ok);
    assert_eq!(result["draft_id"], ID);
    assert_eq!(result["revision"], 1);
}

fn source(yaml: &str, revision: u64) -> Value {
    json!({"draft_id":ID,"revision":revision,"definition_yaml":yaml,"title":null,
        "content_hash":hex::encode(Sha256::digest(yaml.as_bytes())),"size_bytes":yaml.len()})
}
fn reference(revision: u64) -> Value {
    json!({"schemaVersion":2,"draftId":ID,"revision":revision,"readToken":"A".repeat(43)})
}
async fn delivery(server: &MockServer, r: &Value, valid: bool) {
    let rev = r["revision"].as_u64().unwrap();
    Mock::given(method("POST"))
        .and(path(format!(
            "/collaboration/drafts/{ID}/revisions/{rev}/validate"
        )))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "draft_id":ID,"revision":rev,"content_hash":r["content_hash"],
            "validated_at":"2026-10-20T01:00:00Z","validator":"test",
            "validation":{"valid":valid,"summary":{"participants":0,"nodes":0},"errors":[]}
        })))
        .mount(server)
        .await;
    Mock::given(method("GET"))
        .and(path(format!(
            "/collaboration/drafts/{ID}/revisions/{rev}/reference"
        )))
        .respond_with(ResponseTemplate::new(200).set_body_json(reference(rev)))
        .mount(server)
        .await;
}

#[tokio::test]
async fn create_transfers_incident_file_directly_and_emits_only_short_reference() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/collaboration-definition-draft/indentation-regression.json"
    ))
    .unwrap();
    let yaml = fixture["revision"]["definition_yaml"].as_str().unwrap();
    let server = MockServer::start().await;
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("candidate.yaml");
    std::fs::write(&file, yaml).unwrap();
    let r = source(yaml, 1);
    Mock::given(method("PUT"))
        .and(path(format!("/collaboration/drafts/{ID}")))
        .and(header("authorization", "Bearer owner"))
        .and(body_json(json!({"definition_yaml":yaml,"title":null})))
        .respond_with(ResponseTemplate::new(201).set_body_json(r.clone()))
        .expect(1)
        .mount(&server)
        .await;
    delivery(&server, &r, true).await;
    let (output, ok) = execute(
        &BcsClient::with_token(server.uri(), "owner"),
        DraftCommands::Create {
            file,
            draft_id: ID.into(),
            title: None,
        },
    )
    .await;
    assert!(ok);
    assert_eq!(output["saved"], true);
    assert_eq!(output["size_bytes"], 2174);
    assert!(!output.to_string().contains("readToken"));
    assert!(!output.to_string().contains("definition_yaml"));
    assert!(output["message"].as_str().unwrap().len() < 300);
    let requests = server.received_requests().await.unwrap();
    assert_eq!(requests.len(), 3);
    assert!(
        requests
            .iter()
            .all(|r| !r.url.path().contains("session") && !r.url.path().contains("message"))
    );
}

#[tokio::test]
async fn download_preserves_crlf_unicode_and_never_overwrites_or_leaves_partial_files() {
    let server = MockServer::start().await;
    let yaml = "中:\r\n  text: \"<&'😀\"\r\n";
    Mock::given(method("GET"))
        .and(path(format!("/collaboration/drafts/{ID}/revisions/7")))
        .respond_with(ResponseTemplate::new(200).set_body_json(source(yaml, 7)))
        .mount(&server)
        .await;
    let client = BcsClient::new(server.uri());
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("out.yaml");
    let command = || DraftCommands::Get {
        draft_id: ID.into(),
        revision: 7,
        output: file.clone(),
    };
    assert!(execute(&client, command()).await.1);
    assert_eq!(std::fs::read(&file).unwrap(), yaml.as_bytes());
    assert!(!execute(&client, command()).await.1);
    assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
    assert_eq!(std::fs::read(&file).unwrap(), yaml.as_bytes());
    server.reset().await;
    let mut corrupt = source(yaml, 7);
    corrupt["content_hash"] = json!("0".repeat(64));
    Mock::given(method("GET"))
        .respond_with(ResponseTemplate::new(200).set_body_json(corrupt))
        .mount(&server)
        .await;
    let destination = dir.path().join("bad.yaml");
    assert!(
        !execute(
            &client,
            DraftCommands::Get {
                draft_id: ID.into(),
                revision: 7,
                output: destination.clone()
            }
        )
        .await
        .1
    );
    assert!(!destination.exists());
    assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
}

#[tokio::test]
async fn saved_invalid_revision_keeps_stage_and_explicit_preview_has_no_writes() {
    let server = MockServer::start().await;
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("candidate.yaml");
    std::fs::write(&file, "broken: [").unwrap();
    let r = source("broken: [", 1);
    Mock::given(method("PUT"))
        .respond_with(ResponseTemplate::new(201).set_body_json(r.clone()))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path(format!("/collaboration/drafts/{ID}/revisions/1")))
        .respond_with(ResponseTemplate::new(200).set_body_json(r.clone()))
        .mount(&server)
        .await;
    delivery(&server, &r, false).await;
    let client = BcsClient::new(server.uri());
    let (output, ok) = execute(
        &client,
        DraftCommands::Create {
            file,
            draft_id: ID.into(),
            title: None,
        },
    )
    .await;
    assert!(!ok);
    assert_eq!(output["saved"], true);
    assert_eq!(output["phase"], "validate");
    assert!(output.get("message").is_none());
    let (preview, ok) = execute(
        &client,
        DraftCommands::Preview {
            draft_id: ID.into(),
            revision: 1,
        },
    )
    .await;
    assert!(ok);
    assert_eq!(preview["validation"]["validation"]["valid"], false);
    assert!(preview.get("message").is_some());
    let requests = server.received_requests().await.unwrap();
    assert_eq!(
        requests
            .iter()
            .filter(|r| r.method.as_str() == "PUT")
            .count(),
        1
    );
}

#[tokio::test]
async fn uncertain_update_checks_only_next_revision_without_attribution_or_base_changes() {
    for (stored, expected_phase, ok) in [
        (Some("new"), "ready", true),
        (Some("different"), "conflict", false),
        (None, "save", false),
    ] {
        let server = MockServer::start().await;
        let client = BcsClient::new(server.uri());
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("candidate.yaml");
        std::fs::write(&file, "new").unwrap();
        Mock::given(method("POST"))
            .and(path(format!("/collaboration/drafts/{ID}/revisions")))
            .and(body_json(
                json!({"definition_yaml":"new","title":null,"expected_revision":5}),
            ))
            .respond_with(ResponseTemplate::new(503))
            .expect(1)
            .mount(&server)
            .await;
        let r = source(stored.unwrap_or(""), 6);
        Mock::given(method("GET"))
            .and(path(format!("/collaboration/drafts/{ID}/revisions/6")))
            .respond_with(if stored.is_some() {
                ResponseTemplate::new(200).set_body_json(r.clone())
            } else {
                ResponseTemplate::new(404)
            })
            .expect(1)
            .mount(&server)
            .await;
        delivery(&server, &r, true).await;
        let (output, success) = execute(
            &client,
            DraftCommands::Update {
                draft_id: ID.into(),
                file,
                expected_revision: 5,
                title: None,
            },
        )
        .await;
        assert_eq!(success, ok);
        assert_eq!(output["phase"], expected_phase);
        if ok {
            assert_eq!(output["matched_existing_revision"], true);
        }
        if stored.is_none() {
            assert!(output["saved"].is_null());
        }
        let requests = server.received_requests().await.unwrap();
        assert!(requests.iter().all(|r| !r.url.path().ends_with(ID)));
    }
}

#[tokio::test]
async fn malformed_save_response_recovers_only_fixed_next_revision() {
    let server = MockServer::start().await;
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("candidate.yaml");
    std::fs::write(&file, "new").unwrap();
    Mock::given(method("POST"))
        .and(path(format!("/collaboration/drafts/{ID}/revisions")))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"revision":999})))
        .expect(1)
        .mount(&server)
        .await;
    let r = source("new", 6);
    Mock::given(method("GET"))
        .and(path(format!("/collaboration/drafts/{ID}/revisions/6")))
        .respond_with(ResponseTemplate::new(200).set_body_json(r.clone()))
        .expect(1)
        .mount(&server)
        .await;
    delivery(&server, &r, true).await;
    let (output, success) = execute(
        &BcsClient::new(server.uri()),
        DraftCommands::Update {
            draft_id: ID.into(),
            file,
            expected_revision: 5,
            title: None,
        },
    )
    .await;
    assert!(success);
    assert_eq!(output["matched_existing_revision"], true);
    assert_eq!(output["revision"], 6);
}

#[test]
fn reference_serializer_rejects_extra_fields_bad_ids_and_wrong_versions() {
    let maximum: Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/collaboration-definition-draft/maximum-reference.json"
    ))
    .unwrap();
    let r = &maximum["reference"];
    let (_, message) = reference_message(
        r.clone(),
        r["draftId"].as_str().unwrap(),
        9_007_199_254_740_991,
    )
    .unwrap();
    assert_eq!(message, maximum["message"].as_str().unwrap());
    assert_eq!(message.len() as u64, maximum["size_bytes"].as_u64().unwrap());
    // Both server shapes produce the same new card, without forwarding the legacy field.
    for placeholder in ["A".repeat(43), "previous-server-value".to_string()] {
        let mut response = r.clone();
        response["readToken"] = json!(placeholder);
        let (normalized, compatible_message) = reference_message(
            response, r["draftId"].as_str().unwrap(), 9_007_199_254_740_991,
        ).unwrap();
        assert_eq!(compatible_message, message);
        assert!(serde_json::to_value(normalized).unwrap().get("readToken").is_none());
    }
    let mut extra = reference(1);
    extra["title"] = json!("not allowed");
    assert!(reference_message(extra, ID, 1).is_err());
    let mut bad = reference(1);
    bad["draftId"] = json!("invalid");
    assert!(reference_message(bad, ID, 1).is_err());
    assert!(reference_message(reference(1), ID, 2).is_err());
}
