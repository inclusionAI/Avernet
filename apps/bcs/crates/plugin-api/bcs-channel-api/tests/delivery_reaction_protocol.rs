use bcs_channel_api::{DELIVERY_REACTION_EVENT_TYPE, DeliveryReactionEvent, DeliveryReactionState};
use serde_json::json;

#[test]
fn delivery_reaction_states_match_the_wire_contract() {
    for (state, wire_state) in [
        (DeliveryReactionState::Queued, "queued"),
        (DeliveryReactionState::Processing, "processing"),
        (DeliveryReactionState::Expired, "expired"),
        (DeliveryReactionState::Clear, "clear"),
    ] {
        let payload = serde_json::to_value(DeliveryReactionEvent::new(state, "message-1"))
            .expect("typed reaction event serializes");
        assert_eq!(
            payload,
            json!({
                "type": DELIVERY_REACTION_EVENT_TYPE,
                "state": wire_state,
                "message_id": "message-1",
            })
        );
        let decoded: DeliveryReactionEvent =
            serde_json::from_value(payload).expect("contract payload round trips");
        assert_eq!(decoded.state, state);
        assert_eq!(decoded.message_id, "message-1");
    }
}

#[test]
fn delivery_reaction_rejects_unknown_type_or_state() {
    for payload in [
        json!({"type":"message.delivery.unknown","state":"queued","message_id":"message-1"}),
        json!({"type":DELIVERY_REACTION_EVENT_TYPE,"state":"waiting","message_id":"message-1"}),
        json!({"type":DELIVERY_REACTION_EVENT_TYPE,"state":"queued"}),
        json!({"type":DELIVERY_REACTION_EVENT_TYPE,"message_id":"message-1"}),
    ] {
        assert!(serde_json::from_value::<DeliveryReactionEvent>(payload).is_err());
    }
}
