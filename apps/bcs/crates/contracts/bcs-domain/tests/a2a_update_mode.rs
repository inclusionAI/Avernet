use bcs_domain::a2a::{select_a2a_interface, A2aUpdateMode};
use serde_json::json;

#[test]
fn card_capability_selects_tracking_without_changing_binding_or_endpoint() {
    let mut card=json!({"supportedInterfaces":[{"url":"https://agent.example.org/a2a",
        "protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}],"capabilities":{}});
    for streaming in [None,Some(false),Some(true)] {
        card["capabilities"]=match streaming {Some(value)=>json!({"streaming":value}),None=>json!({})};
        let selected=select_a2a_interface(&card,|_,_|true).unwrap();
        assert_eq!(selected.update_mode,if streaming==Some(true) {A2aUpdateMode::Stream} else {A2aUpdateMode::Poll});
        assert_eq!(selected.url,"https://agent.example.org/a2a");
    }
}
