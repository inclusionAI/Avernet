use bcs_protocol::{DiscoverBotsResponse,DiscoverBotsExtendedResponse,BotInfo};
use serde_json::json;

#[test]
fn both_discovery_clients_and_details_preserve_card_descriptions_and_unknown_fields() {
    let card=json!({"name":"agent","skills":[{"id":"lookup","name":"Lookup","description":"full description","future":{"data":42}}]});
    let payload=json!({"bots":[{"bot_uuid":"bot","visibility":"public","capabilities":{},"agent_card":card}],"count":1});
    let simple:DiscoverBotsResponse=serde_json::from_value(payload.clone()).unwrap();
    assert_eq!(simple.bots[0].agent_card,Some(card.clone()));
    let extended:DiscoverBotsExtendedResponse=serde_json::from_value(payload).unwrap();
    assert_eq!(serde_json::to_value(extended).unwrap()["bots"][0]["agent_card"],card);
    let detail:BotInfo=serde_json::from_value(json!({"bot_uuid":"bot","agent_card":card})).unwrap();
    assert_eq!(serde_json::to_value(detail).unwrap()["agent_card"],card);
}

#[test]
fn legacy_bot_wire_shape_does_not_gain_a_null_card() {
    let bot:BotInfo=serde_json::from_value(json!({"bot_uuid":"legacy"})).unwrap();
    assert!(bot.agent_card.is_none());
    assert!(serde_json::to_value(bot).unwrap().get("agent_card").is_none());
}
