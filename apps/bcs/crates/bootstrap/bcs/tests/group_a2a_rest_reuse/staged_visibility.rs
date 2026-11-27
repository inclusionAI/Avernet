use super::*;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::inbox::{
    AdmitRequest, InboxProducerPort, StrictRoundIdentity, StrictRoundStage,
};
use bcs_store_api::zset::{ZSetRange, ZSetStorePlugin};
use std::{num::NonZeroU32, ops::Bound};

#[tokio::test]
async fn staged_start_with_snapshot_and_index_is_invisible_to_public_get_and_list() {
    let world = CompanionWorld::assemble(enabled_config()).await;
    let app = world.assembly.router.clone().layer(axum::extract::Extension(caller()));
    let round = world.round("staged-only", 1);
    let key = &round.input.key;
    let roles = &world.assembly.roles;
    let list = || Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks")).body(Body::empty()).unwrap();
    let (status, page) = response_of(&app, list()).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(page.unwrap()["totalSize"], 0);

    let stage = roles.inbox.stage_strict_round(AdmitRequest {
        scope: scope(),
        input: InputEnvelope {
            schema_version: 1,
            sid: key.sid.clone(), tid: key.tid.clone(),
            input_id: bcs_dist_store::group_admission::strict_round_input_id(&round).unwrap(),
            business_digest: round.request_digest.clone(),
            body: InputBody::User { payload: Payload::Inline(serde_json::to_value(&round).unwrap()) },
        },
        create_session: None,
        create_task: Some(TaskSnapshot {
            schema_version: 1, scope: scope(), sid: key.sid.clone(), tid: key.tid.clone(),
            content: TaskContent {
                status: TaskStatus::Submitted, interactions: vec![], result_ref: None,
                snapshot: Payload::Inline(json!({"format_version": DISPLAY_FORMAT_VERSION, "units": []})),
            },
            source_commit: CommitSeq(0), output_limits: PHASE_ONE_OUTPUT_LIMITS,
            output_usage: Default::default(), snapshot_event_id: None,
            created_at: TimestampMillis(0), updated_at: TimestampMillis(0),
        }),
    }, StrictRoundIdentity {
        request_id: round.request_id.0.clone(), generation: key.generation.get(),
    }).await.unwrap();
    assert!(matches!(stage, StrictRoundStage::Staged));
    assert!(world.model.read_document(&roles.layout.task(&key.tid).unwrap()).await.unwrap().is_some());
    let candidates = ZSetStorePlugin::range(world.model.as_ref(), &roles.layout.tasks(&scope()).unwrap(),
        &ZSetRange { min: Bound::Unbounded, max: Bound::Unbounded, after: None, limit: NonZeroU32::MIN }).await.unwrap();
    assert_eq!(candidates[0].member, key.tid.0);

    let (status, _) = response_of(&app, Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks/{}", key.tid.0))
        .body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let (status, page) = response_of(&app, list()).await;
    assert_eq!(status, StatusCode::OK);
    let page = page.unwrap();
    assert_eq!(page["tasks"], json!([]));
    assert_eq!(page["totalSize"], 0);
}
