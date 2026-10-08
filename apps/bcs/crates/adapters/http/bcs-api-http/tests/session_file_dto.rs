use bcs_api_http::v1::openapi::{
    ListSessionFilesQuery, PrepareSessionFileRequest, ProtectedFileContentQuery,
    SharedFileContentQuery,
};

#[test]
fn prepare_request_rejects_unknown_fields() {
    let error = serde_json::from_value::<PrepareSessionFileRequest>(serde_json::json!({
        "file_name": "report.txt",
        "size": 42,
        "mime_type": "text/plain",
        "owner_id": "client-controlled"
    }))
    .expect_err("unknown identity field is rejected");

    assert!(error.to_string().contains("unknown field"));
}

#[test]
fn list_and_content_queries_have_v1_defaults() {
    let list: ListSessionFilesQuery = serde_json::from_value(serde_json::json!({})).expect("empty list query");
    assert_eq!(list.limit, 100);
    assert_eq!(list.offset, 0);
    assert!(list.prefix.is_none());
    assert!(list.status.is_none());

    let protected: ProtectedFileContentQuery =
        serde_json::from_value(serde_json::json!({})).expect("empty protected query");
    assert!(!protected.show);

    let shared: SharedFileContentQuery = serde_json::from_value(serde_json::json!({
        "token": "abc",
        "show": true
    }))
    .expect("shared query");
    assert_eq!(shared.token, "abc");
    assert!(shared.show);
}
