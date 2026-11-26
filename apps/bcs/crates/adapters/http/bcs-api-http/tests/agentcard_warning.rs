//! Real HTTP -> V1 facade -> management validation -> failure-only warning scope.
use async_trait::async_trait;
use axum::{body::Body, http::{Request, StatusCode}};
use bcs_app_a2a_bot::v1::A2aApiService;
use bcs_service_api::{ServiceResult, application::{a2a_bot::*, v1::*}, types::a2a_bot::{A2aBotRecord, A2aCardSource}};
use serde_json::{Value, json};
use std::sync::{Arc, Mutex};
use tower::ServiceExt;
use tracing::instrument::WithSubscriber;
use tracing_subscriber::fmt::MakeWriter;
#[allow(dead_code)]
mod register_support;

struct Unused;
#[async_trait]
impl RegisterService for Unused {
    async fn issue_register_token(&self, _: IssueRegisterToken) -> Result<RegisterTokenView, ApplicationError> { unreachable!() }
    async fn register_bot(&self, _: RegisterBot) -> Result<BotRegistration, ApplicationError> { unreachable!() }
}
struct Management;
#[async_trait]
impl A2aBotManagementService for Management {
    async fn register_agent(&self, command: RegisterAgentCommand) -> ServiceResult<ManagedA2aCard> {
        let A2aCardSource::Inline(card) = command.source else { unreachable!() };
        bcs_service_api::types::a2a_bot::a2a_management::validate_management_card(&card)?;
        Ok(ManagedA2aCard { catalog_labels:Default::default(), provider_bot_ref:command.provider_bot_ref,
            provider_name:"demo".into(), agent_name:"Agent".into(), bot_id:"bot".into(), agent_id:None,
            visibility:bcs_service_api::types::a2a_bot::A2aRegistrationVisibility::Protected,
            status:bcs_service_api::ActorStatus::Online, user_visibility:UserVisibility::Protected,
            agent_card_url:None, agent_card:card })
    }
    async fn get(&self, _: &str, _: &str, _: &str) -> ServiceResult<Option<A2aBotRecord>> { unreachable!() }
    async fn configure(&self, _: ConfigureA2aBotCommand) -> ServiceResult<A2aBotRecord> { unreachable!() }
    async fn refresh(&self, _: &str, _: &str, _: &str, _: i64) -> ServiceResult<A2aBotRecord> { unreachable!() }
    async fn refresh_due(&self, _: i64) -> ServiceResult<usize> { unreachable!() }
}
#[derive(Clone, Default)]
struct Output(Arc<Mutex<Vec<u8>>>);
impl std::io::Write for Output {
    fn write(&mut self, bytes:&[u8])->std::io::Result<usize> { self.0.lock().unwrap().extend_from_slice(bytes); Ok(bytes.len()) }
    fn flush(&mut self)->std::io::Result<()> { Ok(()) }
}
impl<'a> MakeWriter<'a> for Output { type Writer = Self; fn make_writer(&'a self)->Self { self.clone() } }
struct Provider;
impl bcs_tracing_api::NetworkTracer for Provider {
    fn start(&self, _: &bcs_tracing_api::RequestInfo<'_>, _: &bcs_tracing_api::Carrier, _: bcs_tracing_api::ParentContext<'_>) -> bcs_tracing_api::StartedTrace { unreachable!() }
    fn log_correlation(&self, ctx:&bcs_tracing_api::Context)->Option<bcs_tracing_api::LogCorrelation> {ctx.downcast_ref::<bcs_tracing_api::LogCorrelation>().cloned()}
}
fn app() -> axum::Router {
    bcs_api_http::router(register_support::test_state(Arc::new(Unused))
        .with_a2a_service(Arc::new(A2aApiService::new(Arc::new(Management)))))
}
fn card(name:&str)->Value { json!({"name":name,"description":"Demo","version":"1.0",
    "defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],"capabilities":{},"skills":[],
    "supportedInterfaces":[{"url":"https://example.test/a2a","protocolBinding":"JSONRPC","protocolVersion":"1.0"}],
    "unknown":{"keep":42,"token":"nested-value"}}) }
async fn call(body:String, id:&str)->(StatusCode,Vec<Value>) {
    let output=Output::default(); let reader=output.clone();
    let request=Request::builder().method("POST").uri("/api/v1/collaboration/a2a/agents")
        .header("x-request-id",id).header("content-type","application/json")
        .header("authorization","Bearer aaaaaaaaaaaa").body(Body::from(body)).unwrap();
    let ids=bcs_tracing_api::LogCorrelation::new("sofa_trace_id","trace-register","sofa_rpc_id","0.8").unwrap();
    let response=bcs_tracing_api::with_provider(Some(Arc::new(Provider)),bcs_tracing_api::with_context(Some(Arc::new(ids)),async {
        app().oneshot(request).await.unwrap()
    })).with_subscriber(tracing_subscriber::fmt().json().with_writer(output).finish()).await;
    let text=String::from_utf8(reader.0.lock().unwrap().clone()).unwrap();
    assert!(!text.contains("aaaaaaaaaaaa")); assert!(!text.contains("nested-value"));
    let rows=text.lines().map(|line|serde_json::from_str::<Value>(line).unwrap())
        .filter(|row|row["target"]=="bcs_business_warning").collect::<Vec<_>>();
    if let Some(first) = rows.first() {
        assert_eq!(first["fields"]["request_id"].as_str().unwrap(), response.headers()["x-request-id"].to_str().unwrap());
    }
    (response.status(),rows)
}
#[tokio::test]
async fn invalid_card_keeps_typed_reason_and_original_unknown_fields_with_trace_and_request_ids() {
    let raw=json!({"provider_bot_ref":"demo","agent_card":card(" ")}).to_string();
    let (status,rows)=call(raw,"request-register").await;
    assert_eq!(status,StatusCode::UNPROCESSABLE_ENTITY); assert_eq!(rows.len(),1);
    let fields=&rows[0]["fields"];
    for (key,value) in [("request_id","request-register"),("trace_id","trace-register"),("rpc_id","0.8"),
        ("error_code","INVALID_CARD"),("field","agent_card.name"),("reason","INVALID_NAME"),("provider_token_prefix","aaaa****")] {
        assert_eq!(fields[key],value,"{rows:?}");
    }
    let payload:Value=serde_json::from_str(fields["request_payload"].as_str().unwrap()).unwrap();
    assert_eq!(payload["agent_card"]["name"]," ");
    assert_eq!(payload["agent_card"]["unknown"]["keep"],42);
    assert_eq!(payload["agent_card"]["unknown"]["token"],"<redacted>");
}
#[tokio::test]
async fn parsing_failure_logs_safe_input_and_malformed_json_never_leaks_raw_credentials() {
    let (status,rows)=call(json!({"provider_bot_ref":42,"agent_card":card("Agent")}).to_string(),"bad-fields").await;
    assert_eq!(status,StatusCode::BAD_REQUEST); assert_eq!(rows.len(),1);
    assert_eq!(rows[0]["fields"]["error_code"],"invalid_request");
    assert!(rows[0]["fields"]["reason"].as_str().unwrap().contains("Invalid request fields"));
    let (status,rows)=call(r#"{"token":"nested-value",bad"#.into(),"bad-json").await;
    assert_eq!(status,StatusCode::BAD_REQUEST); assert_eq!(rows.len(),1);
    assert_eq!(rows[0]["fields"]["payload_redacted"],false);
}
#[tokio::test]
async fn valid_registration_does_not_emit_payload_or_warning() {
    let (status,rows)=call(json!({"provider_bot_ref":"demo","agent_card":card("Agent")}).to_string(),"success").await;
    assert_eq!(status,StatusCode::CREATED); assert!(rows.is_empty());
}

#[tokio::test]
async fn generated_request_id_is_the_same_in_warning_and_response() {
    let (status,rows)=call(json!({"provider_bot_ref":"demo","agent_card":card(" ")}).to_string(),"").await;
    assert_eq!(status,StatusCode::UNPROCESSABLE_ENTITY);
    assert!(!rows[0]["fields"]["request_id"].as_str().unwrap().is_empty());
}

#[tokio::test]
async fn diagnostics_do_not_read_oversized_bodies_before_protocol_or_credential_checks() {
    for (method,accept,token,status) in [
        ("POST","text/html",Some("admin"),StatusCode::NOT_ACCEPTABLE),
        ("DELETE","application/json",Some("admin"),StatusCode::METHOD_NOT_ALLOWED),
        ("POST","application/json",None,StatusCode::UNAUTHORIZED),
    ] {
        let mut request=Request::builder().method(method).uri("/api/v1/collaboration/a2a/agents")
            .header("accept",accept).header("content-type","application/json");
        if let Some(token)=token { request=request.header("authorization",format!("Bearer {token}")); }
        let response=app().oneshot(request.body(Body::from("x".repeat(2*1024*1024+1))).unwrap()).await.unwrap();
        assert_eq!(response.status(),status);
    }
}

#[tokio::test]
async fn invalid_card_response_preserves_safe_validation_location_and_reason() {
    for (field, value, expected) in [
        ("name", json!(" "), "agent_card.name: name must contain 2 to 64 characters"),
        ("description", json!(""), "agent_card.description: a non-empty string is required"),
        ("supportedInterfaces", json!([]), "agent_card.supportedInterfaces: a non-empty array is required"),
    ] {
        let mut invalid = card("Agent");
        invalid[field] = value;
        let request = Request::builder().method("POST").uri("/api/v1/collaboration/a2a/agents")
            .header("content-type", "application/json").header("authorization", "Bearer private-credential")
            .header("x-request-id", "request-validation")
            .body(Body::from(json!({"provider_bot_ref":"demo","agent_card":invalid}).to_string())).unwrap();
        let response = app().oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
        let body: Value = serde_json::from_slice(&axum::body::to_bytes(response.into_body(), 16_384).await.unwrap()).unwrap();
        assert_eq!(body["data"]["error_code"], "INVALID_CARD");
        assert_eq!(body["request_id"], "request-validation");
        assert!(body["message"].as_str().unwrap().contains(expected), "{body}");
        assert!(!body.to_string().contains("private-credential") && !body.to_string().contains("nested-value"));
    }
}
