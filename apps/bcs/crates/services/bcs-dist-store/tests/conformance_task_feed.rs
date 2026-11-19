#![allow(clippy::unwrap_used, reason = "actual TaskFeed conformance")]
mod common;
mod task_feed_support;
use bcs_dist_model::*;
use bcs_dist_store::task_feed::StoreTaskFeed;
use bcs_service_api::port::repo::distributed::{session::*, task_feed::*};
use bcs_test_support::contract::distributed::task_feed::*;
use common::*;
use task_feed_support::*;
#[tokio::test]
async fn conformance_publish_and_restore() {
    let f = Fixture::new().await;
    let RestoredTask::Live { cursor, .. } = f
        .feed
        .restore(&scope(), &Tid(TID.into()))
        .await
        .unwrap()
        .unwrap()
    else {
        panic!()
    };
    let events = text_events(1);
    let snapshot = hello_snapshot(1, event_id(1, 2));
    let sources = f
        .commit(
            1,
            vec![
                EffectKind::AppendEvents {
                    previous: None,
                    events: events.clone(),
                },
                EffectKind::PublishSnapshot {
                    snapshot: snapshot.clone(),
                },
            ],
        )
        .await;
    let append = AppendCommittedEvents {
        source: sources[0].clone(),
        previous: None,
        events,
    };
    let publish = PublishCommittedSnapshot {
        source: sources[1].clone(),
        snapshot,
    };
    let mut changed = append.clone();
    changed.events[0].body = OutputBody::Status {
        status: TaskStatus::Failed,
        interactions: vec![],
    };
    changed.source.content_digest = kind_digest(&EffectKind::AppendEvents {
        previous: None,
        events: changed.events.clone(),
    });
    let port: &StoreTaskFeed = &f.feed;
    task_publisher_port_contract_tests(port, append, changed, publish)
        .await
        .unwrap();
    let RestoredTask::Live { view, .. } = port
        .restore(&scope(), &Tid(TID.into()))
        .await
        .unwrap()
        .unwrap()
    else {
        panic!()
    };
    port.trim(RetentionRequest {
        sid: Sid(SID.into()),
        keep_from: event_id(1, 2),
    })
    .await
    .unwrap();
    task_reader_port_contract_tests(port, &scope(), &view, cursor)
        .await
        .unwrap();
}
