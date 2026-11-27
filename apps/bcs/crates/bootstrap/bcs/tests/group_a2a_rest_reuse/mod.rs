use super::*;
use bcs_dist_model::{RoundKey, Sid, Tid};
use bcs_store_api::document::DocumentStorePlugin;

fn send_in_context(context: &Sid, message_id: &str, text: &str) -> Request<Body> {
    Request::post(format!("{GROUPS_PREFIX}/{GROUP}/message:send"))
        .header("content-type", "application/json")
        .body(Body::from(json!({
            "message": {
                "messageId": message_id,
                "contextId": context.0,
                "role": "ROLE_USER",
                "parts": [{"text": text}],
            },
            "configuration": {"blocking": false}
        }).to_string()))
        .unwrap()
}

fn receipt_ids(task: Value) -> (Sid, Tid) {
    (
        Sid(task["task"]["contextId"].as_str().unwrap().into()),
        Tid(task["task"]["id"].as_str().unwrap().into()),
    )
}

async fn complete_round(world: &CompanionWorld, app: &axum::Router, tid: &Tid) {
    until_start(world).await;
    world.journal.finish(NODE_RESEARCH);
    until_start(world).await;
    world.journal.finish(NODE_REVIEW);
    until_rest_task_state(world, app, tid, "TASK_STATE_COMPLETED").await;
}

#[tokio::test]
async fn rest_successor_uses_the_same_context_and_a_new_licensed_generation() {
    bounded::<300>(async {
        let world = CompanionWorld::assemble(enabled_config()).await;
        let _connector = world.start_connector();
        let app = world.assembly.router.clone().layer(axum::extract::Extension(caller()));
        let (status, body) = response_of(&app, rest_send("first", "research", false)).await;
        assert_eq!(status, StatusCode::OK, "{body:?}");
        let (sid, first_tid) = receipt_ids(body.unwrap());
        complete_round(&world, &app, &first_tid).await;

        let (status, body) = response_of(&app, send_in_context(&sid, "second", "follow-up")).await;
        assert_eq!(status, StatusCode::OK, "second group round: {body:?}");
        let (second_sid, second_tid) = receipt_ids(body.unwrap());
        assert_eq!(second_sid, sid);
        assert_ne!(second_tid, first_tid);
        let key = RoundKey {
            scope: scope(),
            group: group_id(),
            sid: sid.clone(),
            tid: second_tid.clone(),
            generation: 2.try_into().unwrap(),
        };
        let view = world.round_view(&key).await.expect("successor owns generation 2");
        assert_eq!(view.accepted.input.ordered_parts, vec![bcs_dist_model::RoundPart::Text { text: "follow-up".into() }]);

        // A retry uses its original generation even while another round owns
        // the checkpoint. The first key is now resolved through cold proof.
        for (request, expected) in [
            (rest_send("first", "research", false), first_tid),
            (send_in_context(&sid, "second", "follow-up"), second_tid.clone()),
        ] {
            let (status, body) = response_of(&app, request).await;
            assert_eq!(status, StatusCode::OK, "replay must not answer CONTEXT_BUSY: {body:?}");
            assert_eq!(receipt_ids(body.unwrap()), (sid.clone(), expected));
        }
        let (status, body) = response_of(&app, send_in_context(&sid, "second", "changed")).await;
        assert_eq!(status, StatusCode::CONFLICT, "{body:?}");
        assert_eq!(body.unwrap()["error"]["code"], "CONFLICT");
        complete_round(&world, &app, &second_tid).await;
    }).await;
}

#[tokio::test]
async fn rest_busy_context_does_not_stage_a_second_start() {
    bounded::<300>(async {
        let world = CompanionWorld::assemble(enabled_config()).await;
        let _connector = world.start_connector();
        let app = world.assembly.router.clone().layer(axum::extract::Extension(caller()));
        let (status, body) = response_of(&app, rest_send("active", "research", false)).await;
        assert_eq!(status, StatusCode::OK, "{body:?}");
        let (sid, active_tid) = receipt_ids(body.unwrap());
        let schedule = world.assembly.roles.layout.schedule(&sid).unwrap();
        let before = world.model.read_document(&schedule).await.unwrap().unwrap();

        let (status, body) = response_of(&app, send_in_context(&sid, "waiting", "initial attempt")).await;
        assert_eq!(status, StatusCode::CONFLICT, "{body:?}");
        assert_eq!(body.unwrap()["error"]["code"], "CONTEXT_BUSY");
        let after = world.model.read_document(&schedule).await.unwrap().unwrap();
        assert_eq!(after.version, before.version, "Busy must not stage an Inbox start");

        complete_round(&world, &app, &active_tid).await;
        // Busy is not an accepted or prepared request key, so changed
        // content under that key can still start the next round.
        let (status, body) = response_of(&app, send_in_context(&sid, "waiting", "new attempt")).await;
        assert_eq!(status, StatusCode::OK, "busy loser must be able to retry: {body:?}");
        assert_ne!(receipt_ids(body.unwrap()).1, active_tid);
    }).await;
}
