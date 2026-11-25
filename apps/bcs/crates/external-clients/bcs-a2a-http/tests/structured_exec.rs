use bcs_a2a_http::structured_exec::{Decision,ExecInput,EXTENSION,PROFILE};
use serde_json::{json,Value};

fn fixture()->Value {serde_json::from_str(include_str!("fixtures/java-structured-exec-input.json")).unwrap()}
fn decode(raw:Value)->Result<ExecInput,&'static str> {
    ExecInput::decode(&serde_json::from_value(raw).unwrap(),"fixture-task","fixture-context",1_000)
}
fn schema(raw:&mut Value)->&mut Value {&mut raw["parts"][0]["data"]}
fn accept(raw:&mut Value,id:&str,decision:&str) {
    schema(raw)["x-bcs-exec"]["accepted"][id]=json!(decision);
    let choice=&mut schema(raw)["properties"]["decisions"]["properties"][id];
    choice.as_object_mut().unwrap().remove("enum");choice["const"]=json!(decision);
}

#[test]
fn native_java_schema_decodes_and_reply_roundtrips_through_sdk() {
    let raw=fixture();let input=decode(raw.clone()).unwrap();
    assert_eq!(input.schema,raw["parts"][0]["data"]);
    assert_eq!(input.id,"fixture-request");
    assert_eq!(input.tools["tool-1"].name,"query_order");
    assert_eq!(input.tools["tool-1"].args,json!({"orderId":"order-1"}));
    for decision in [Decision::AllowOnce,Decision::Deny] {
        let reply=input.reply("tool-1",decision.clone(),"resolution-1","fixture-task","fixture-context",1_000).unwrap();
        let encoded=serde_json::to_value(reply).unwrap();
        assert_eq!(encoded["role"],"ROLE_USER");
        assert_eq!(encoded["taskId"],"fixture-task");assert_eq!(encoded["contextId"],"fixture-context");
        assert_eq!(encoded["parts"][0]["metadata"][EXTENSION],json!({"type":"structured_input","id":"fixture-request"}));
        assert_eq!(encoded["parts"][0]["data"],json!({"decisions":{"tool-1":decision.as_str()}}));
    }
}

#[test]
fn correlation_tags_and_numeric_versions_are_strict() {
    for (key,value) in [("taskId",json!("another-task")),("contextId",json!("another-context")),("role",json!("ROLE_USER"))] {
        let mut raw=fixture();raw[key]=value;assert!(decode(raw).is_err());
    }
    for version in [json!("1"),json!(true),json!(1.1),json!(2)] {
        let mut raw=fixture();schema(&mut raw)["x-bcs-exec"]["version"]=version;assert!(decode(raw).is_err());
    }
    let mut raw=fixture();schema(&mut raw)["x-bcs-exec"]["version"]=json!(1);
    assert!(decode(raw).is_ok());
    let mut raw=fixture();raw["parts"][0]["metadata"][EXTENSION]["id"]=json!("");assert!(decode(raw).is_err());
    let mut raw=fixture();let part=raw["parts"][0].clone();raw["parts"].as_array_mut().unwrap().push(part);assert!(decode(raw).is_err());
    let mut raw=fixture();raw["parts"][0]["metadata"]=json!({});assert!(decode(raw).is_err());
}

#[test]
fn unsupported_schema_constraints_cannot_be_silently_dropped() {
    for extra in [json!({"allOf":[]}),json!({"required":["decisions","secret"]}),
        json!({"additionalProperties":true}),json!({"$schema":"http://json-schema.org/draft-07/schema#"})] {
        let mut raw=fixture();for (key,value) in extra.as_object().unwrap() {schema(&mut raw)[key]=value.clone();}
        assert!(decode(raw).is_err());
    }
    let mut raw=fixture();schema(&mut raw)["properties"]["decisions"]["required"]=json!(["tool-1"]);
    assert!(decode(raw).is_err());
    let mut raw=fixture();schema(&mut raw)["properties"]["decisions"]["properties"]["tool-1"]["enum"]=json!(["approve","deny"]);
    assert!(decode(raw).is_err());
    let mut raw=fixture();schema(&mut raw)["x-bcs-exec"]["accepted"]["tool-1"]=json!("allow_once");
    assert!(decode(raw).is_err(),"accepted receipt must agree with const schema");
    let mut raw=fixture();schema(&mut raw)["x-bcs-exec"]["accepted"]["unknown"]=json!("deny");assert!(decode(raw).is_err());
}

#[test]
fn same_request_accumulates_receipts_without_changing_approved_operations() {
    let first=decode(fixture()).unwrap();let mut raw=fixture();accept(&mut raw,"tool-1","allow_once");
    let accepted=decode(raw.clone()).unwrap();assert!(first.check_update(&accepted).is_ok());
    assert!(accepted.check_update(&first).is_err(),"cannot forget accepted decisions");
    assert!(accepted.reply("tool-1",Decision::Deny,"m","fixture-task","fixture-context",1_000).is_err());
    assert!(accepted.reply("tool-1",Decision::AllowOnce,"m","fixture-task","fixture-context",1_000).is_ok());
    for changed in [json!({"name":"another_tool"}),json!({"args":{"orderId":"someone-else"}}),json!({"argsSha256":"a".repeat(64)})] {
        let mut raw=fixture();for (key,value) in changed.as_object().unwrap() {schema(&mut raw)["x-bcs-exec"]["tools"]["tool-1"][key]=value.clone();}
        assert!(first.check_update(&decode(raw).unwrap()).is_err());
    }
    let mut changed=first.clone();changed.expires_at_ms+=1;assert!(first.check_update(&changed).is_err());
    changed.expires_at_ms=first.expires_at_ms-1;assert!(first.check_update(&changed).is_ok());
    changed.id="other-request".into();assert!(first.check_update(&changed).is_err());
}

#[test]
fn task_receipt_is_required_and_acceptance_is_not_inferred_from_text() {
    let input=decode(fixture()).unwrap();
    assert!(!input.accepted_by_task(&json!({}),"tool-1",&Decision::AllowOnce).unwrap());
    let proof=json!({(PROFILE):{"version":1.0,"requests":{"fixture-request":{"accepted":{"tool-1":"allow_once"}}}}});
    assert!(input.accepted_by_task(&proof,"tool-1",&Decision::AllowOnce).unwrap());
    assert!(input.accepted_by_task(&proof,"tool-1",&Decision::Deny).is_err());
    assert!(input.accepted_by_task(&proof,"unknown",&Decision::AllowOnce).is_err());
    let mut wrong=proof.clone();wrong[PROFILE]["requests"]=json!({"another-request":{"accepted":{"tool-1":"allow_once"}}});
    assert!(!input.accepted_by_task(&wrong,"tool-1",&Decision::AllowOnce).unwrap());
    wrong[PROFILE]["version"]=json!("1");assert!(input.accepted_by_task(&wrong,"tool-1",&Decision::AllowOnce).is_err());
}

#[test]
fn batch_partial_updates_expiry_and_bounds() {
    let mut raw=fixture();let tool=schema(&mut raw)["x-bcs-exec"]["tools"]["tool-1"].clone();
    let choice=schema(&mut raw)["properties"]["decisions"]["properties"]["tool-1"].clone();
    for n in 2..=64 {
        schema(&mut raw)["x-bcs-exec"]["tools"][format!("tool-{n}")]=tool.clone();
        schema(&mut raw)["properties"]["decisions"]["properties"][format!("tool-{n}")]=choice.clone();
    }
    let first=decode(raw.clone()).unwrap();assert_eq!(first.tools.len(),64);
    accept(&mut raw,"tool-1","deny");let next=decode(raw.clone()).unwrap();assert_eq!(next.accepted.len(),1);
    first.check_update(&next).unwrap();
    schema(&mut raw)["x-bcs-exec"]["tools"]["tool-65"]=tool;
    schema(&mut raw)["properties"]["decisions"]["properties"]["tool-65"]=choice;assert!(decode(raw).is_err());
    let mut raw=fixture();schema(&mut raw)["x-bcs-exec"]["expiresAtMs"]=json!(1_000);assert!(decode(raw).is_err());
    let input=decode(fixture()).unwrap();assert!(input.reply("tool-1",Decision::AllowOnce,"m","t","c",input.expires_at_ms).is_err());
    let mut raw=fixture();schema(&mut raw)["x-bcs-exec"]["tools"]["tool-1"]["argsSha256"]=json!("not-a-digest");assert!(decode(raw).is_err());
    let mut raw=fixture();schema(&mut raw)["x-bcs-exec"]["tools"]["tool-1"]["args"]=json!({"large":"x".repeat(131_072)});assert!(decode(raw).is_err());
}

#[test]
fn completed_schema_remains_historical_binding_without_authorizing_an_expired_reply() {
    let mut raw=fixture();schema(&mut raw)["x-bcs-exec"]["expiresAtMs"]=json!(500);
    accept(&mut raw,"tool-1","allow_once");
    let message=serde_json::from_value(raw).unwrap();
    assert!(ExecInput::decode(&message,"fixture-task","fixture-context",1_000).is_err());
    let historical=ExecInput::decode(&message,"fixture-task","fixture-context",0).unwrap();
    assert_eq!(historical.accepted.len(),historical.tools.len());
    assert!(historical.reply("tool-1",Decision::AllowOnce,"m","fixture-task","fixture-context",1_000).is_err());
    // Historical comparison cannot remove or alter a prior accepted choice.
    assert!(historical.check_update(&decode(fixture()).unwrap()).is_err());
}
