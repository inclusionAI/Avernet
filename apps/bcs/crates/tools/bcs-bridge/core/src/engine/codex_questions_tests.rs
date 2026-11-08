use super::*;

fn form() -> Value {
    json!({"questions":[
        {"id":"choice","header":"Choice","question":"Which?","isOther":true,
         "options":[{"label":"A","description":"first"},{"label":"B","description":"second"}]},
        {"id":"text","header":"Text","question":"Details?","options":null}]})
}

#[test]
fn maps_choices_descriptions_and_free_text_without_engine_ids() {
    let questions = map_questions(&form()).unwrap();
    assert_eq!(questions[0]["questionId"], "choice");
    assert_eq!(questions[0]["options"][0], json!({"label":"A","value":"A","description":"first"}));
    assert_eq!(questions[0]["allowOther"], true);
    assert_eq!(questions[0]["multiSelect"], false);
    assert!(questions[1].get("options").is_none());
    let response = map_answers(&questions, &json!({"action":"submit","answers":{
        "choice":{"values":["custom"]},"text":{"values":["details"]},"unknown":{"values":["ignored"]}}}));
    assert_eq!(response, json!({"choice":{"answers":["custom"]},"text":{"answers":["details"]}}));
}

#[test]
fn refuses_secrets_duplicate_ids_and_malformed_forms() {
    for key in ["isSecret", "secret"] {
        let mut raw = form(); raw["questions"][0][key] = json!(true);
        assert!(map_questions(&raw).is_err());
    }
    let mut raw = form(); raw["questions"][1]["id"] = json!("choice");
    assert!(map_questions(&raw).is_err());
    for raw in [json!({"questions":[]}),json!({"questions":[{"id":"x","question":" "}]}),
        json!({"questions":[{"id":"x","question":"q","options":[{"label":""}]}]})] {
        assert!(map_questions(&raw).is_err());
    }
}

#[test]
fn cancellation_skips_and_unoffered_answers_never_invent_choices() {
    let mut raw = form();raw["questions"][0]["isOther"] = json!(false);
    let questions = map_questions(&raw).unwrap();
    assert_eq!(map_answers(&questions,&json!({"action":"cancel"})), json!({}));
    assert_eq!(map_answers(&questions,&json!({"action":"submit","answers":{
        "choice":{"values":["unoffered"]},"text":{"values":[]}}})), json!({}));
    assert_eq!(map_answers(&questions,&json!({"action":"submit","answers":{
        "choice":{"values":["A"]},"text":{"values":[]}}})),
        json!({"choice":{"answers":["A"]},"text":{"answers":[]}}));
}

#[test]
fn question_and_resolution_pass_the_real_bcn_v3_encoder_and_parser() {
    use crate::{encoder::PluginEncoder, run::RunEvent};
    use bcs_protocol::{BcsFrame, stream::parse_run_event_v3};
    let encoder = PluginEncoder::for_protocol(true,"provider","bot","group","group:session");
    for (phase, extra) in [(InteractionPhase::Requested,json!({"questions":map_questions(&form()).unwrap()})),
        (InteractionPhase::Resolved,json!({"action":"submit"}))] {
        let event = sse::interaction_event("run",phase,InteractionKind::AskUser,"int-question",extra);
        let frame = encoder.encode(&RunEvent {run_id:"run".into(),seq:1,ts:1,event}).unwrap().unwrap();
        let BcsFrame::Event(frame) = frame else { panic!("expected event") };
        let StreamEvent::Interaction(event) = parse_run_event_v3(&frame.event,frame.payload.unwrap()).unwrap()
            else { panic!("expected interaction") };
        assert_eq!(event.phase,phase);
        assert_eq!(event.kind,InteractionKind::AskUser);
    }
}

#[test]
fn maps_real_async_agent_message_question_shape_to_bcn() {
    let mapped = map_async_questions(&[json!({"title":"你今天更想喝什么？","options":["咖啡","茶","白水"]})]).unwrap();
    assert_eq!(mapped[0]["questionId"],"question_1");
    assert_eq!(mapped[0]["options"][1],json!({"label":"茶","value":"茶"}));
    assert_eq!(mapped[0]["allowOther"],true);
    assert!(map_async_questions(&[json!({"title":"secret","secret":true})]).is_err());
}
