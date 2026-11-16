use bcn_runtime::manifest::*;
use bcn_runtime_model::*;
fn entry(endpoint:&str)->FrozenTransport {
    let mut e=FrozenTransport { binding: FrozenTargetBinding {
        target:AgentId::new(Scope::new("test","tenant","new"),"agent"),binding_id:"binding".into(),binding_version:String::new(),credential_ref:None,
        capabilities:ConnectorCapabilities{streaming:true,query:false,subscribe:false,input_continuation:false,cancellation:CancellationSupport::Unsupported,idempotency:IdempotencySupport::Unsupported}},
        route:TransportRoute::Provider{endpoint:endpoint.into(),provider_id:"p".into(),provider_bot_ref:"b".into(),group_id:"g".into(),timeout_ms:5000}};
    e.binding.binding_version=e.version().unwrap();e
}
#[test]
fn retained_version_is_exact_and_route_changes_cannot_alias() {
    let old=entry("http://localhost:1234");let new=entry("http://localhost:4321");
    assert_ne!(old.binding.binding_version,new.binding.binding_version);
    let archive=TransportManifest{entries:vec![old.clone(),new.clone()]};archive.validate().unwrap();
    assert_eq!(archive.find(&old.binding).unwrap().route,old.route);
    let missing=TransportManifest{entries:vec![new.clone()]};
    assert!(missing.find(&old.binding).is_err());
    let mut tampered=old.clone();tampered.route=new.route;
    assert!(TransportManifest{entries:vec![tampered]}.validate().is_err());
    assert!(TransportManifest{entries:vec![old.clone(),old]}.validate().is_err());
}
#[test]
fn credential_reference_and_capabilities_are_part_of_version() {
    let a=entry("http://localhost:1234");let mut b=a.clone();b.binding.capabilities.query=true;
    assert_ne!(a.version().unwrap(),b.version().unwrap());
    b=a.clone();b.binding.credential_ref=Some(CredentialRef{name:"provider/p".into(),version:"1".into()});
    assert_ne!(a.version().unwrap(),b.version().unwrap());
}
