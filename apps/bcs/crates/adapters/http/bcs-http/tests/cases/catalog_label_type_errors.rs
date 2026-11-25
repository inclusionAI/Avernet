use axum::{body::{Body, to_bytes}, http::{Request, StatusCode}};
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_services_container::Services;
use serde_json::json;
use tower::ServiceExt;

#[tokio::test]
async fn label_type_errors_are_structured_across_legacy_entries() {
    let app=build_router(HttpAppState::new(Services::builder().build_for_test()));
    for (method,path,mut body) in [
        ("POST","/providers/provider/bots",json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card":{"name":"Inline"}})),
        ("PUT","/providers/provider/bots/bot/a2a",json!({})),
        ("POST","/a2a/providers/orders/bots",json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card":{"name":"Inline"}})),
        ("PATCH","/groups/group",json!({})),
    ] {
        for (labels,field) in [
            (json!({"stage":[1]}),"catalog_labels.stage[0]"),
            (json!({"stage":[null]}),"catalog_labels.stage[0]"),
            (json!({"stage":1}),"catalog_labels.stage"),
            (json!({"stage":{}}),"catalog_labels.stage"),
            (json!(null),"catalog_labels"),
            (json!([]),"catalog_labels"),
            (json!(false),"catalog_labels"),
        ] {
            body["catalog_labels"]=labels;
            let response=app.clone().oneshot(Request::builder().method(method).uri(path)
                .header("content-type","application/json").header("authorization","Bearer test-admin")
                .body(Body::from(body.to_string())).unwrap()).await.unwrap();
            assert_eq!(response.status(),StatusCode::BAD_REQUEST,"{method} {path}");
            assert!(response.headers().get("content-type").unwrap().to_str().unwrap().starts_with("application/json"));
            let bytes=to_bytes(response.into_body(),8192).await.unwrap();
            let body:serde_json::Value=serde_json::from_slice(&bytes).unwrap();
            assert_eq!(body["params"]["code"],"invalid_request");
            assert_eq!(body["params"]["field"],field);
            assert_eq!(body["params"]["reason"],"INVALID_TYPE");
        }
    }
}

#[tokio::test]
async fn unrelated_legacy_json_rejections_keep_their_protocol_statuses() {
    let app=build_router(HttpAppState::new(Services::builder().build_for_test()));
    for (method,path,unrelated,expected) in [
        ("POST","/providers/provider/bots",json!({"owners":1}),StatusCode::UNPROCESSABLE_ENTITY),
        ("PUT","/providers/provider/bots/bot/a2a",json!({"expected_revision":"bad"}),StatusCode::UNPROCESSABLE_ENTITY),
        ("POST","/a2a/providers/orders/bots",json!({"owners":1}),StatusCode::UNPROCESSABLE_ENTITY),
        ("PATCH","/groups/group",json!({"name":1}),StatusCode::BAD_REQUEST),
    ] {
        for (body,content_type,status) in [
            (unrelated.to_string(),"application/json",expected),
            ("{".into(),"application/json",StatusCode::BAD_REQUEST),
            ("{}".into(),"text/plain",if method=="PATCH" {StatusCode::BAD_REQUEST}else {StatusCode::UNSUPPORTED_MEDIA_TYPE}),
        ] {
            let response=app.clone().oneshot(Request::builder().method(method).uri(path).header("content-type",content_type)
                .body(Body::from(body)).unwrap()).await.unwrap();
            assert_eq!(response.status(),status,"{method} {path} {content_type}");
        }
    }
}
