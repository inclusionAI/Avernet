use bcs_api_http::v1::openapi::SessionFileUrlProjector;
use serde_json::json;

fn projector() -> SessionFileUrlProjector {
    SessionFileUrlProjector::new(
        "https://gateway.example.com/api/v1/collaboration".to_string(),
    )
    .expect("valid base")
}

#[test]
fn constructs_encoded_protected_and_shared_content_urls() {
    let projector = projector();

    assert_eq!(
        projector.content_url("group/a session", "file-1"),
        "https://gateway.example.com/api/v1/collaboration/sessions/group%2Fa%20session/files/file-1/content"
    );
    assert_eq!(
        projector.shared_content_url("token/with+symbols="),
        "https://gateway.example.com/api/v1/collaboration/sessions/shared-file/content?token=token%2Fwith%2Bsymbols%3D"
    );
}

#[test]
fn projects_only_proxy_single_upload_targets() {
    let projector = projector();
    let target = json!({
        "mode": "single",
        "method": "PUT",
        "upload_url": "http://legacy.test/sessions/s/files/f/content",
        "expires_at": 3600
    });

    let projected = projector.project_upload_target(target.clone(), true, "s", "f");
    assert_eq!(
        projected["upload_url"],
        "https://gateway.example.com/api/v1/collaboration/sessions/s/files/f/content"
    );
    assert_eq!(
        projector.project_upload_target(target.clone(), false, "s", "f"),
        target,
        "direct storage presigned URL remains untouched"
    );
}

#[test]
fn projects_each_proxy_multipart_part_from_its_server_part_number() {
    let target = json!({
        "mode": "multipart",
        "method": "PUT",
        "part_size": 10,
        "part_count": 2,
        "parts": [
            {"part_number": 1, "upload_url": "http://legacy/one"},
            {"part_number": 2, "upload_url": "http://legacy/two"}
        ]
    });

    let projected = projector().project_upload_target(target, true, "s", "f");

    assert_eq!(
        projected["parts"][0]["upload_url"],
        "https://gateway.example.com/api/v1/collaboration/sessions/s/files/f/content?part=1"
    );
    assert_eq!(
        projected["parts"][1]["upload_url"],
        "https://gateway.example.com/api/v1/collaboration/sessions/s/files/f/content?part=2"
    );
}

#[test]
fn content_url_and_shared_content_url_use_internal_base() {
    let projector = SessionFileUrlProjector::new(
        "https://gateway.example.com/api/v1/collaboration".to_string(),
    )
    .expect("valid base");

    assert_eq!(
        projector.content_url("s", "f"),
        "https://gateway.example.com/api/v1/collaboration/sessions/s/files/f/content"
    );
    assert_eq!(
        projector.shared_content_url("tok"),
        "https://gateway.example.com/api/v1/collaboration/sessions/shared-file/content?token=tok"
    );
}
