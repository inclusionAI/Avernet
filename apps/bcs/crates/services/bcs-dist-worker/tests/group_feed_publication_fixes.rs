//! Fix round 1 of the strict-group feed publication follower lane (plan T7
//! slice 2): retained obligations externalize like the outbox era (I1),
//! resting deferrals keep their pre-slice dry-scan semantics while only
//! publication-obligated sessions re-drive (I2), both part derivations
//! follow the feed's per-artifact amendment rule (I3), and strict rounds
//! discharge ephemeral thinking at install so a refused feed can never
//! stall them through display traffic (I4).
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "feed-publication fix-round vectors"
)]
#[path = "group_round_recovery_support/mod.rs"]
mod group_round_recovery_support;
mod processor_support;
mod support;

use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    group_round::*, inbox::*, result::*, session::*,
};
use group_round_recovery_support::*;
use std::sync::Arc;

/// Fix round I1: an over-threshold inline artifact registers as a
/// publication obligation and externalizes through the same blob/digest
/// machinery as the outbox era — a persistently refused shared feed can
/// accumulate retained obligations without ever wedging the sealed
/// candidate on `encoded_budget`, and after the lane restores the
/// obligation delivers through its digest-reference frame (the public
/// side dereferences the sealed blob).
#[tokio::test]
async fn oversized_obligation_externalizes_and_never_wedges_under_refusal() {
    bounded::<240>(async {
        let runner = FsmRunner::new(FsmMode::HugeAnswer);
        let world = Arc::new(RoundWorld::new(runner).await);
        let a = round("a", 1);
        let a_key = a.input.key.clone();
        assert!(matches!(
            world.admission.admit(&a).await.unwrap(),
            Admission::New { .. }
        ));

        // The shared feed refuses every write from the first round commit
        // on: nothing delivers, so the obligation registers and STAYS.
        let processor = local_processor(&world);
        wedge_feed(&world);

        // The round runs, closes its authority and retires under the
        // refusal: its retained Ref obligations keep every later candidate
        // inside the sealed budget — the outbox-era externalization now
        // covers the register too, so no `encoded_budget` wedge can exist.
        until(&world, &processor, |c| {
            c.tasks.iter().all(|t| t.tid != a_key.tid)
        })
        .await;
        let view = world.round_view(&a_key).await.expect("A's view");
        assert!(
            view.terminal.is_some(),
            "the huge retained obligation never wedges the round's authority"
        );
        assert!(
            feed_events(&world).await.is_empty(),
            "the refused feed published nothing up to the closed authority"
        );

        // The retained obligation is externalized: the register carries a
        // digest reference of the runner's over-threshold content.
        let retained = publications(&world).await;
        let sealed_reference = retained
            .iter()
            .find_map(|publication| match &publication.kind {
                EffectKind::AppendEvents { events, .. } => events
                    .iter()
                    .find_map(|event| match &event.body {
                        OutputBody::Artifact {
                            update: ArtifactUpdate::Upsert { content },
                            ..
                        } => match content {
                            Payload::Ref(reference) => Some(reference.clone()),
                            _ => None,
                        },
                        _ => None,
                    }),
                _ => None,
            })
            .expect("the over-threshold payload externalized into a Ref frame");

        // The lane restores; the resident generation delivers the retained
        // obligation through the digest-reference frame, and the sealed
        // blob dereferences back to the exact content the runner produced.
        release_feed(&world);
        until(&world, &processor, |c| {
            !c.feed_publications.iter().any(|p| p.tid == a_key.tid)
        })
        .await;
        let events = feed_events(&world).await;
        let published_reference = events
            .iter()
            .find_map(|event| match &event.body {
                OutputBody::Artifact {
                    update: ArtifactUpdate::Upsert { content },
                    ..
                } => match content {
                    Payload::Ref(reference) => Some(reference.clone()),
                    _ => None,
                },
                _ => None,
            })
            .expect("the published frame carries its digest reference");
        assert_eq!(
            published_reference.key, sealed_reference.key,
            "the delivered frame is the sealed obligation's reference"
        );
        let bytes = world
            .f
            .blobs
            .get(&published_reference)
            .await
            .unwrap()
            .expect("the sealed blob of the delivered frame");
        assert_eq!(
            bytes,
            serde_json::to_vec(&serde_json::json!("x".repeat(80_000))).unwrap(),
            "the published frame dereferences to the runner's exact content"
        );
        let view = world.round_view(&a_key).await.expect("A's view");
        assert!(
            view.terminal.is_some(),
            "A stays readable after the huge obligation delivered"
        );
        let processor = local_processor(&world);
        let _ = processor;
    })
    .await;
}

/// Fix round I2 (a): an ordinary resting deferral — inserted by every
/// settle-release since before this slice — must never re-drive on the dry
/// pending path. A non-strict session finishes, settles and releases with
/// zero feed obligations; subsequent runtime ticks must leave it resting,
/// exactly the pre-slice semantics.
#[tokio::test]
async fn resting_defers_do_not_reacquire_without_publication_work() {
    bounded::<240>(async {
        use crate::processor_support::{FinishingRunner, Fixture};
        let f = Fixture::new(Arc::new(FinishingRunner::default())).await;
        f.admit("resting").await;
        f.warm().await;

        // Drive the session to its settle and release: the generic path has
        // no feed publication register at all, so its release keeps an
        // ordinary deferred reacquire only.
        for _ in 0..96 {
            let _ = f.step().await;
            if f.runtime.acquired_sessions().unwrap().is_empty() {
                break;
            }
        }
        assert!(
            f.runtime.acquired_sessions().unwrap().is_empty(),
            "the finished session released its residency"
        );

        // Dry pending-index ticks: the ordinary deferral must NOT re-acquire
        // the resting session (pre-slice resting semantics restored).
        for _ in 0..96 {
            f.runtime.tick().await.unwrap();
            assert!(
                f.runtime.acquired_sessions().unwrap().is_empty(),
                "an ordinary resting deferral never re-drives on a dry scan"
            );
        }
    })
    .await;
}

/// Fix round I3: the part derivations on both sides of the attach seam
/// follow the feed's own per-artifact amendment rule — `Upsert("a")`
/// followed by `AppendText(" b")` yields one part whose content is "a b".
#[tokio::test]
async fn appended_text_amends_the_artifact_part() {
    bounded::<240>(async {
        let runner = FsmRunner::new(FsmMode::AmendAnswer);
        let world = Arc::new(RoundWorld::new(runner).await);
        let a = round("a", 1);
        let a_key = a.input.key.clone();
        assert!(matches!(
            world.admission.admit(&a).await.unwrap(),
            Admission::New { .. }
        ));
        let processor = local_processor(&world);
        until(&world, &processor, |c| {
            c.tasks.iter().all(|t| t.tid != a_key.tid)
        })
        .await;

        // One amended part through the authoritative cut.
        let parts = world
            .artifacts
            .task_artifacts(
                &ArchiveGrants { read: true },
                &scope(),
                &GroupId(GROUP.into()),
                &[a_key.tid.clone()],
            )
            .await
            .unwrap();
        assert_eq!(parts.len(), 1, "one amended artifact part");
        let (_, part) = &parts[0];
        assert_eq!(
            part.part,
            RoundPart::Text {
                text: "a b".into()
            },
            "the append delta amended the upserted base content"
        );

        // The complete public history keeps both frames in committed order.
        let view = world
            .artifacts
            .get_archived(
                &ArchiveGrants { read: true },
                &scope(),
                &GroupId(GROUP.into()),
                &a_key.tid,
            )
            .await
            .unwrap()
            .expect("the round's archive");
        let ArchivedTaskView::Committed { archive, .. } = view else {
            panic!("the archive committed inside the terminal window");
        };
        let frames: Vec<_> = archive
            .public_history
            .iter()
            .filter_map(|event| match &event.body {
                OutputBody::Artifact { update, .. } => Some(update.clone()),
                _ => None,
            })
            .collect();
        assert_eq!(
            frames,
            vec![
                ArtifactUpdate::Upsert {
                    content: Payload::Inline(serde_json::json!("a")),
                },
                ArtifactUpdate::AppendText {
                    delta: Payload::Inline(serde_json::json!(" b")),
                },
            ],
            "the public history preserves the upsert and its amendment"
        );
        let view = world.round_view(&a_key).await.expect("A's view");
        assert!(view.terminal.is_some(), "the amending round closed");
    })
    .await;
}

/// Fix round I4: strict FSM-group rounds discharge ephemeral thinking at
/// install — thinking display traffic never re-enters the blocking outbox,
/// so a persistently refused shared feed cannot stall a strict round
/// through it, and none of its frames ever reach the paired register or
/// the public feed.
#[tokio::test]
async fn thinking_never_stalls_a_strict_round_under_refusal() {
    bounded::<240>(async {
        let runner = FsmRunner::new(FsmMode::WaitOnSibling);
        let world = Arc::new(RoundWorld::new(runner).await);
        let a = round("a", 1);
        let a_key = a.input.key.clone();
        assert!(matches!(
            world.admission.admit(&a).await.unwrap(),
            Admission::New { .. }
        ));
        let processor = local_processor(&world);
        settle_a_round(&world, &processor, &a_key.tid).await;

        // The shared feed refuses every write; the sibling's thinking
        // traffic flows through the inbox (never the feed).
        wedge_feed(&world);
        let did = sibling_did(&a_key.tid);
        for seq in 1..=3u64 {
            world
                .f
                .inbox
                .append_stream(AppendAgentEvent {
                    sid: sid(),
                    tid: a_key.tid.clone(),
                    event: AgentEvent {
                        did: did.clone(),
                        holder_epoch: HolderEpoch(1),
                        seq: InputSeq(seq),
                        kind: AgentEventKind::Thinking,
                        remote_event_id: None,
                        payload: Payload::Inline(serde_json::json!("thinking")),
                    },
                })
                .await
                .unwrap();
        }

        // The round answers and closes: thinking consumed without a frame
        // never blocks the strict spine under the refused feed.
        let checkpoint = until(&world, &processor, |c| {
            c.tasks.iter().any(
                |t| t.tid == a_key.tid && matches!(t.phase, TaskPhase::AwaitingResult { .. }),
            )
        })
        .await;
        let _ = checkpoint;
        until(&world, &processor, |c| {
            c.tasks.iter().all(|t| t.tid != a_key.tid)
        })
        .await;
        let view = world.round_view(&a_key).await.expect("A's view");
        assert!(
            view.terminal.is_some(),
            "discharged thinking never stalls a strict round under a refused feed"
        );

        // None of the thinking frames exists in the register or the feed:
        // every registered and published artifact is the round's own.
        for publication in publications(&world).await {
            if let EffectKind::AppendEvents { events, .. } = &publication.kind {
                for event in events {
                    if let OutputBody::Artifact { class, .. } = &event.body {
                        assert_ne!(
                            class,
                            &OutputClass::Thinking,
                            "thinking traffic never registers as an obligation"
                        );
                    }
                }
            }
        }
        let events = feed_events(&world).await;
        assert!(
            events.iter().all(|event| {
                !matches!(
                    &event.body,
                    OutputBody::Artifact {
                        class: OutputClass::Thinking,
                        ..
                    }
                )
            }),
            "the wedged feed stayed free of thinking while the round closed"
        );
        let a_view = world.round_view(&a_key).await.expect("A's view");
        assert!(a_view.terminal.is_some());
    })
    .await;
}
