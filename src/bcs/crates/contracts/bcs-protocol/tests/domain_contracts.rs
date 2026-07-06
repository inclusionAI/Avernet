use bcs_protocol::{
    A2aRunStatus, BotActor, BotDeliveryKind, CallerContext, FrontendDeliveryTarget, HumanActor,
};

#[test]
fn protocol_exports_typed_principal_value_objects() {
    let caller = CallerContext::Human(HumanActor {
        actor_id: "human_alice".to_string(),
        staff_no: "alice".to_string(),
    });

    let encoded = serde_json::to_value(&caller).unwrap();
    let decoded: CallerContext = serde_json::from_value(encoded).unwrap();

    assert_eq!(caller, decoded);
    assert_eq!(
        CallerContext::Bot(BotActor {
            bot_uuid: "bot-1".to_string(),
        }),
        CallerContext::Bot(BotActor {
            bot_uuid: "bot-1".to_string(),
        })
    );
}

#[test]
fn protocol_exports_delivery_and_a2a_response_value_objects() {
    let target = FrontendDeliveryTarget::Run {
        run_id: "run-1".to_string(),
    };
    let status = A2aRunStatus {
        run_id: "run-1".to_string(),
        status: "running".to_string(),
        response: None,
    };

    assert_eq!(BotDeliveryKind::Send, BotDeliveryKind::Send);
    assert_eq!(
        serde_json::to_value(&target).unwrap(),
        serde_json::json!({"Run": {"run_id": "run-1"}})
    );
    assert_eq!(status.run_id, "run-1");
}
