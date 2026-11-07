use bcs_a2a_http::{grpc_mesh::{GrpcMeshConfig,GrpcMeshRoute},card::{select_card_with_bindings,CardError}};
use serde_json::json;

fn config()->GrpcMeshConfig {
    GrpcMeshConfig {endpoint:"http://127.0.0.1:50002".into(),routes:vec![GrpcMeshRoute {
        interface_url:"https://agent.example:50051".into(),target_app:"example-agent".into(),
        service_name:"example.Agent:1.0".into(),vip_address:None,
    }]}
}

#[test]
fn mesh_dial_target_must_be_explicit_literal_loopback_without_url_injection() {
    assert!(config().validate().is_ok());
    for endpoint in ["http://localhost:50002","https://127.0.0.1:50002","http://192.0.2.1:50002",
        "http://127.0.0.1","http://127.0.0.1:50002/path","http://user@127.0.0.1:50002",
        "http://127.0.0.1:50002?x=1","http://127.0.0.1:50002#fragment"] {
        let mut config=config();config.endpoint=endpoint.into();assert!(config.validate().is_err(),"{endpoint}");
    }
}

#[test]
fn duplicate_routes_and_invalid_headers_fail_configuration() {
    let mut config=config();config.routes.push(config.routes[0].clone());assert!(config.validate().is_err());
    for target in ["","bad\r\nheader","非ASCII"] {
        let mut config=crate::config();config.routes[0].target_app=target.into();assert!(config.validate().is_err(),"{target:?}");
    }
    assert!(serde_json::from_value::<GrpcMeshConfig>(json!({"endpoint":"http://127.0.0.1:50002",
        "routes":[],"fallback_url":"https://agent.example"})).is_err());
}

#[test]
fn grpc_card_selection_requires_an_enabled_binding_and_exact_trusted_route() {
    let raw=json!({"name":"test","description":"test","version":"1","supportedInterfaces":[{
        "protocolBinding":"GRPC","protocolVersion":"1.0","url":"https://agent.example:50051","tenant":"bot-a"}],
        "capabilities":{"streaming":true},"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],"skills":[]});
    let routes=config();
    assert_eq!(select_card_with_bindings(raw.clone(),&[],false,&["HTTP+JSON"],|_|true).unwrap_err(),CardError::InterfaceUnsupported);
    assert_eq!(select_card_with_bindings(raw.clone(),&[],false,&["GRPC"],|_|false).unwrap_err(),CardError::AddressRejected);
    let card=select_card_with_bindings(raw,&[],false,&["GRPC"],|i|routes.supports(&i.url)).unwrap();
    assert_eq!(card.interface.tenant.as_deref(),Some("bot-a"));
    assert!(!routes.supports("https://another-agent.example:50051"));
}

#[test]
fn grpc_selection_keeps_original_http_uri_despite_sdk_normalization() {
    let raw=json!({"name":"test","description":"test","version":"1","supportedInterfaces":[{
        "protocolBinding":"GRPC","protocolVersion":"1.0","url":"http://agent.example:51051","tenant":"bot-a"}],
        "capabilities":{"streaming":true},"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],"skills":[]});
    let card=select_card_with_bindings(raw,&[],false,&["GRPC"],|i|i.url=="http://agent.example:51051").unwrap();
    assert_eq!(card.interface.url,"http://agent.example:51051");
}

#[test]
fn route_identity_changes_with_remote_binding_but_not_sidecar_port() {
    let original=config();let url=original.routes[0].interface_url.clone();
    let identity=original.fingerprint(&url).unwrap();
    let mut changed=original.clone();changed.endpoint="http://127.0.0.1:51002".into();
    assert_eq!(identity,changed.fingerprint(&url).unwrap());
    for change in ["app","service","vip"] {
        let mut changed=original.clone();
        match change {
            "app"=>changed.routes[0].target_app="different-app".into(),
            "service"=>changed.routes[0].service_name="different.Service:1.0".into(),
            _=>changed.routes[0].vip_address=Some("different.example:51051".into()),
        }
        assert_ne!(identity,changed.fingerprint(&url).unwrap());
    }
}
