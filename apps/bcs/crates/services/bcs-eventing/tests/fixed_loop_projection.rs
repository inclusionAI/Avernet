use bcs_eventing::{EventCatalog, project_event};
use bcs_service_api::types::{EventEnvelope, EventPayloadMode};
use serde_json::Value;

#[test]
fn fixed_loop_execution_survives_full_and_metadata_only_event_projection() {
    let catalog = EventCatalog::load_embedded().unwrap();
    let cases = [
        include_str!("../../../../api-contracts/events/v1/fixtures/state_machine.node.started.loop.metadata_only.json"),
        include_str!("../../../../api-contracts/events/v1/fixtures/state_machine.node.completed.loop.full.json"),
        include_str!("../../../../api-contracts/events/v1/fixtures/state_machine.node.retry_scheduled.loop.metadata_only.json"),
    ];
    for fixture in cases {
        let event: EventEnvelope = serde_json::from_str(fixture).unwrap();
        for mode in [EventPayloadMode::Full, EventPayloadMode::MetadataOnly] {
            let projected: Value = serde_json::from_slice(&project_event(&event, &catalog, mode, 256 * 1024).unwrap()).unwrap();
            assert_eq!(projected["data"]["execution"], event.data["execution"]);
            assert_eq!(projected["data"]["node_id"], event.data["node_id"]);
            if event.event_type.ends_with("completed") && mode == EventPayloadMode::MetadataOnly {
                assert_eq!(projected["data"]["output"]["included"], false);
                assert!(projected["data"]["output"].get("text").is_none());
                assert!(projected["data"]["output"].get("json").is_none());
            }
        }
    }
}
