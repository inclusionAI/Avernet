//! Run the public boundary contracts against the actual GroupDiscovery and StoreTaskReader.
#![allow(clippy::unwrap_used, reason = "conformance assertions")]
mod rest_common;
use bcs_test_support::contract::distributed::group_public::{
    group_discovery_service_contract_tests, group_listing_port_contract_tests,
};
use rest_common::*;
use serde_json::json;

#[tokio::test]
async fn conformance_group_discovery() {
    let p = ports();
    let stack = group_stack(&p);
    let service: &bcs_app_dist_gateway::GroupDiscovery = &stack.discovery;
    group_discovery_service_contract_tests(
        service,
        &caller(),
        &GroupId("grp-research-01".into()),
        || {
            stack.grants.published.store(false, Ordering::SeqCst);
        },
    )
    .await
    .unwrap();
}

#[tokio::test]
async fn conformance_group_listing() {
    let p = ports();
    let stack = group_stack(&p);
    let app = test_router(&stack);
    let mut ids = [Tid(String::new()), Tid(String::new())];
    for (i, key) in ["a", "b"].into_iter().enumerate() {
        let (status, body) =
            response_of(&app, rest_send(key, key, None, json!({"blocking": false}))).await;
        assert_eq!(status.as_u16(), 200);
        ids[i] = Tid(body.unwrap()["task"]["id"].as_str().unwrap().into());
    }
    ids.sort_by(|a, b| a.0.cmp(&b.0));
    let scope = TaskScope {
        tenant: TenantId("t-ant".into()),
        principal: PrincipalId("bot:pa-001".into()),
    };
    let reader: &bcs_dist_gateway_store::StoreTaskReader = &p.tasks;
    group_listing_port_contract_tests(
        reader,
        &scope,
        &GroupId("grp-research-01".into()),
        &ids,
        TimestampMillis(NOW),
        publish_at(&p, &ids[1].0, TaskStatus::Working, 1000, &[]),
    )
    .await
    .unwrap();
}
