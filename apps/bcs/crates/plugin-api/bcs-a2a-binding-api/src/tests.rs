use super::*;
struct Fixture { name: &'static str }
impl Binding for Fixture {
    fn name(&self) -> &str { self.name }
    fn resolve(&self, value:&Value, _:bool)->Result<String,String> {
        value["target"].as_str().map(str::to_owned).ok_or("INVALID_TARGET".into())
    }
    fn fingerprint(&self,key:&str)->Result<String,String> {
        if key=="service" {Ok("version-1".into())} else {Err("UNKNOWN_TARGET".into())}
    }
    fn client(&self,_:&str,_:Option<&str>,_:Option<&str>,_:&str,_:Duration)->Result<Arc<dyn Client>,String> {
        Err("NO_TEST_CLIENT".into())
    }
}
fn fixture()->Registry { Registry::new(vec![Arc::new(Fixture {name:"EXAMPLE"})]).unwrap() }
fn declaration()->Value { json!({"supportedInterfaces":[{"protocolBinding":"EXAMPLE","protocolVersion":"1.0","target":"service"}]}) }

#[test]
fn registry_names_are_unique_valid_and_cannot_override_standard_bindings() {
    for name in ["", "HTTP+JSON", "JSONRPC", "GRPC", "CUSTOM", "invalid name", "非ASCII"] {
        assert!(Registry::new(vec![Arc::new(Fixture {name})]).is_err());
    }
    assert!(Registry::new(vec![Arc::new(Fixture {name:"EXAMPLE"}),Arc::new(Fixture {name:"EXAMPLE"})]).is_err());
}
#[test]
fn unknown_and_forged_bindings_cannot_create_internal_routes() {
    let mut raw=declaration();raw["supportedInterfaces"][0]["protocolBinding"]=json!("CUSTOM");
    assert_eq!(fixture().project(&raw).unwrap()["supportedInterfaces"],json!([]));
    assert_eq!(Registry::default().project(&declaration()).unwrap()["supportedInterfaces"],json!([]));
    raw["supportedInterfaces"][0]["protocolBinding"]=json!("EXAMPLE");raw["supportedInterfaces"][0]["protocolVersion"]=json!("0.3");
    assert_eq!(fixture().project(&raw).unwrap()["supportedInterfaces"],json!([]));
    assert!(fixture().project(&json!({})).is_err());
    assert!(fixture().project(&json!({"supportedInterfaces":[{}]})).is_err());
}
#[test]
fn enabled_binding_errors_fail_closed_and_key_size_is_bounded() {
    for target in [json!(null),json!("unknown"),json!(""),json!("x".repeat(257))] {
        let mut raw=declaration();raw["supportedInterfaces"][0]["target"]=target;
        assert!(fixture().project(&raw).is_err());
    }
}
#[test]
fn opaque_route_round_trip_preserves_card_and_rejects_malformed_or_uninstalled_routes() {
    let registry=fixture();let raw=declaration();let before=raw.clone();
    let projected=registry.project(&raw).unwrap();assert_eq!(raw,before);
    let url=projected["supportedInterfaces"][0]["url"].as_str().unwrap();
    assert_eq!(registry.fingerprint(url).unwrap(),"version-1");
    assert!(registry.client(url,None,None,"",Duration::from_secs(1)).is_err());
    assert!(Registry::default().fingerprint(url).is_err());
    for bad in ["https://example.org", "custom-a2a://route/not-hex", "custom-a2a://route/7b7d"] {
        assert!(registry.fingerprint(bad).is_err());
    }
    assert!(registry.fingerprint(&"x".repeat(1025)).is_err());
}
#[test]
fn startup_installation_is_one_shot() {
    install(fixture()).unwrap();assert!(install(fixture()).is_err());
    assert!(!registry().project(&declaration()).unwrap()["supportedInterfaces"].as_array().unwrap().is_empty());
}
