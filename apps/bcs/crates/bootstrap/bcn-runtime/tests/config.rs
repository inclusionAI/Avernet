use bcn_runtime::*;
use bcn_runtime_model::*;
use serde_json::json;
fn config() -> RuntimeConfig { serde_json::from_value(json!({"scope":{"environment":"local","tenant":"t","namespace":"bcn-v1"},"profile":"local","storage":"memory"})).unwrap() }
#[test]
fn disabled_by_default_and_unknown_configuration_is_rejected() {
    let c = config(); assert!(!c.enabled); assert!(c.validate().is_ok());
    let mut value = serde_json::to_value(&c).unwrap(); value["typo"] = json!(1);
    assert!(serde_json::from_value::<RuntimeConfig>(value).is_err());
}
#[test]
fn every_mvp_limit_rejects_zero_and_nondefault_overrides() {
    let c = config();
    for (key, value) in serde_json::to_value(&c.limits).unwrap().as_object().unwrap() {
        for replacement in [0, value.as_u64().unwrap()+1] {
            let mut value = serde_json::to_value(&c).unwrap(); value["limits"][key] = json!(replacement);
            let changed: RuntimeConfig = serde_json::from_value(value).unwrap();
            assert!(changed.validate().is_err(), "ignored override: {key}={replacement}");
        }
    }
}
#[test]
fn namespace_profile_and_explicit_allowlist_are_enforced() {
    let mut c=config(); c.scope.namespace="".into(); assert!(c.validate().is_err());
    let mut c=config(); c.profile=RuntimeProfile::Production; assert!(c.validate().is_err());
    let mut c=config(); c.enabled=true; assert!(c.validate().is_err());
    c.allowlist.push(ExecutionTarget::Agent { agent_id: AgentId::new(c.scope.clone(), "echo") }); assert!(c.validate().is_ok());
    c.allowlist.push(ExecutionTarget::Agent { agent_id: AgentId::new(Scope::new("x","t","bcn-v1"), "other") }); assert!(c.validate().is_err());
}
