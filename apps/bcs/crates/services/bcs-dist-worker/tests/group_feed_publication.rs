//! T7 slice 2 (plan task 14): feed publication becomes a follower of the
//! strict FSM-group spine.
//!
//! `AppendEvents` and `PublishSnapshot` of a strict-group round are split out
//! of the blocking session outbox into durable paired obligations registered
//! with the authoritative commit itself. Round retirement and the next
//! round's dispatch never wait on them; an independent bounded publisher
//! retries by stable ID order and proves both steps by durable read-back. The
//! vectors wedge the shared feed (persistent refusal, no client disconnect),
//! drive round A to its closed authority while the feed refuses every write,
//! dispatch round B's first node inside the wedge, crash the Worker in the
//! two named windows, and assert the takeover completes A's pair through the
//! retained obligations: no duplicate public events, no wrong Tid, no
//! unauthorized publication, A always readable, B never blocked.
//!
//! The C3 counterexample pins the conflict class: a persistently conflicting
//! feed identity (second sink) refuses the terminal while the registered
//! intent survives, and the conflict resolving yields exactly-once completion.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "feed-publication decoupling vectors"
)]
#[path = "group_round_recovery_support/mod.rs"]
mod group_round_recovery_support;
mod processor_support;
mod support;

use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    dispatch::DispatchControlPort, group_round::*, result::*, session::*,
};
use group_round_recovery_support::*;
use std::sync::atomic::Ordering;

#[tokio::test]
async fn feed_append_failure_does_not_starve_next_round() {
    bounded::<240>(async {
        let runner = FsmRunner::new(FsmMode::WaitOnSibling);
        let world = std::sync::Arc::new(RoundWorld::new(runner).await);
        let a = round("a", 1);
        let a_key = a.input.key.clone();
        assert!(matches!(
            world.admission.admit(&a).await.unwrap(),
            Admission::New { .. }
        ));
        let processor = local_processor(&world);
        settle_a_round(&world, &processor, &a_key.tid).await;

        // The shared feed refuses every write from here on (not a client
        // disconnect). Nothing of A's publication lane may complete.
        wedge_feed(&world);

        // A closes its authority terminal through the spine without any
        // feed delivery, and retires the round.
        until(&world, &processor, |c| {
            c.tasks.iter().all(|t| t.tid != a_key.tid)
        })
        .await;
        let a_view = world.round_view(&a_key).await.expect("A's view");
        assert!(
            a_view.terminal.is_some(),
            "the refused feed must not starve A's authority"
        );
        // The retired round keeps undelivered obligations registered: they
        // are durable items that neither retirement nor compaction clears.
        let pending = publications(&world).await;
        assert!(!pending.is_empty(), "retirement must retain undelivered items");

        // B admits and actually dispatches its first node inside the wedge.
        assert!(matches!(
            world.admission.admit(&round("b", 2)).await.unwrap(),
            Admission::New { .. }
        ));
        let b_key = round("b", 2).input.key;
        kill_generation(&world).await;
        let processor = local_processor(&world);
        until_dispatch(&world, &processor, &sibling_did(&b_key.tid)).await;
        let b_dispatch = world
            .f
            .dispatch
            .get(&sibling_did(&b_key.tid))
            .await
            .unwrap()
            .expect("B dispatched its first node while the feed refused A");
        assert_eq!(b_dispatch.view.spec.tid, b_key.tid);
        // A's retained items survive untouched beside B's own registrations.
        let after_b = publications(&world).await;
        for publication in &pending {
            assert_eq!(
                find_publication(&after_b, &publication.effect)
                    .expect("A's retained items survive"),
                publication
            );
        }

        // The wedged Worker generations are killed; the successor restores
        // the feed lane and delivers the retained obligations in order: the
        // artifact frame strictly precedes the terminal frame, every public
        // event id appears exactly once, and A stays readable throughout.
        release_feed(&world);
        until(&world, &processor, |c| {
            !c.feed_publications.iter().any(|p| p.tid == a_key.tid)
        })
        .await;
        let events = feed_events(&world).await;
        let artifact = artifact_index(&events, &a_key.tid).expect("A's artifact frame published");
        let terminal = terminal_index(&events, &a_key.tid).expect("A's terminal frame published");
        assert!(
            artifact < terminal,
            "the artifact frame precedes the terminal frame after the takeover"
        );
        let mut seen = std::collections::BTreeSet::new();
        for event in &events {
            assert!(
                seen.insert(event.id),
                "no duplicate public event {:?}",
                event.id
            );
        }
        let a_view = world.round_view(&a_key).await.expect("A's view");
        assert!(a_view.terminal.is_some(), "A stays readable after delivery");
    })
    .await;
}

/// Crash window 1: A's first append never succeeded and B already dispatched.
/// The takeover delivers the retained pair through the original permission —
/// no duplicates, no wrong Tid, no unauthorized publication, A readable, B
/// unblocked. The retained predecessor (feed_tail) survives the next round's
/// checkpoint view: it is never re-derived from the retired round's hot
/// records.
#[tokio::test]
async fn takeover_after_never_appended_completes_with_original_permission() {
    bounded::<240>(async {
        let runner = FsmRunner::new(FsmMode::WaitOnSibling);
        let calls = runner.calls.clone();
        let world = std::sync::Arc::new(RoundWorld::new(runner).await);
        let a = round("a", 1);
        let a_key = a.input.key.clone();
        assert!(matches!(
            world.admission.admit(&a).await.unwrap(),
            Admission::New { .. }
        ));
        let processor = local_processor(&world);
        settle_a_round(&world, &processor, &a_key.tid).await;
        wedge_feed(&world);

        until(&world, &processor, |c| {
            c.tasks.iter().all(|t| t.tid != a_key.tid)
        })
        .await;
        // The retained permission is captured before the takeover: the
        // successor must complete strictly these obligations, not re-issue
        // fresh ones.
        let retained = publications(&world).await;
        assert!(!retained.is_empty());

        assert!(matches!(
            world.admission.admit(&round("b", 2)).await.unwrap(),
            Admission::New { .. }
        ));
        let b_key = round("b", 2).input.key;
        kill_generation(&world).await;
        let processor = local_processor(&world);
        until_dispatch(&world, &processor, &sibling_did(&b_key.tid)).await;

        // Crash: the wedged generation is killed with A's first append never
        // applied. The takeover generation never re-ran the closed round.
        kill_generation(&world).await;
        let before_takeover = calls.load(Ordering::SeqCst);
        release_feed(&world);
        until(&world, &processor, |c| {
            !c.feed_publications.iter().any(|p| p.tid == a_key.tid)
        })
        .await;
        assert!(
            calls.load(Ordering::SeqCst) <= before_takeover + 1,
            "the takeover delivers; it never re-runs the round"
        );

        // Strictly the retained obligations were delivered: the published
        // events match the captured register, no duplicate and no foreign
        // Tid.
        let events = feed_events(&world).await;
        let mut expected = std::collections::BTreeSet::new();
        for publication in &retained {
            if let EffectKind::AppendEvents { events, .. } = &publication.kind {
                for event in events {
                    assert_eq!(event.tid, a_key.tid, "no wrong Tid");
                    expected.insert(event.id);
                }
            }
        }
        let published: std::collections::BTreeSet<_> = events
            .iter()
            .filter(|event| event.tid == a_key.tid)
            .map(|event| event.id)
            .collect();
        assert!(
            expected.is_subset(&published),
            "every retained append event published"
        );
        assert!(
            published.iter().all(|id| {
                retained.iter().any(|p| {
                    matches!(&p.kind, EffectKind::AppendEvents { events, .. }
                        if events.iter().any(|e| e.id == *id))
                })
            }),
            "no unauthorized publication outside the retained permission"
        );
        let a_view = world.round_view(&a_key).await.expect("A's view");
        assert!(a_view.terminal.is_some(), "A stays readable after the takeover");
        let b_view = world.round_view(&b_key).await.expect("B's view");
        assert!(b_view.terminal.is_none(), "B keeps its own open round");
    })
    .await;
}

/// Crash window 2: A's append succeeded but the snapshot publication never
/// ran before the crash. The takeover completes the pair's publish leg with
/// the original permission; the append evidence is never rewritten and no
/// duplicate public event appears.
#[tokio::test]
async fn takeover_after_append_without_publish_completes_the_pair() {
    bounded::<240>(async {
        let runner = FsmRunner::new(FsmMode::WaitOnSibling);
        let world = std::sync::Arc::new(RoundWorld::new(runner).await);
        let a = round("a", 1);
        let a_key = a.input.key.clone();
        assert!(matches!(
            world.admission.admit(&a).await.unwrap(),
            Admission::New { .. }
        ));
        let processor = local_processor(&world);
        settle_a_round(&world, &processor, &a_key.tid).await;

        // Appends are allowed (A's events land durably); every publish is
        // refused — the append succeeded, the snapshot never did.
        wedge_publish(&world);
        until(&world, &processor, |c| {
            c.tasks.iter().all(|t| t.tid != a_key.tid)
        })
        .await;
        let events = feed_events(&world).await;
        assert!(!events.is_empty(), "the append side applied before the crash");
        let retained = publications(&world).await;
        assert!(
            retained
                .iter()
                .any(|p| matches!(p.kind, EffectKind::PublishSnapshot { .. })),
            "the publish obligation survives the crashed generation"
        );

        // Crash inside the window; the takeover generation completes the
        // pair from the retained permission alone.
        kill_generation(&world).await;
        let append_writes = world.f.publisher.append_calls.load(Ordering::SeqCst);
        let processor = local_processor(&world);
        release_publish(&world);
        until(&world, &processor, |c| {
            !c.feed_publications.iter().any(|p| p.tid == a_key.tid)
        })
        .await;
        assert!(
            world.f.publisher.append_calls.load(Ordering::SeqCst)
                <= append_writes + retained.len(),
            "append evidence replays idempotently at most once per obligation"
        );
        let events_after = feed_events(&world).await;
        assert_eq!(
            events, events_after,
            "no new public event appeared for the publish completion"
        );
        let a_view = world.round_view(&a_key).await.expect("A's view");
        assert!(a_view.terminal.is_some());
    })
    .await;
}

/// C3 counterexample: the second feed identity conflicts persistently, so the
/// round's terminal refuses to close while the registered intent survives;
/// once the conflict resolves, the pair completes exactly once.
#[tokio::test]
async fn second_feed_conflict_blocks_terminal_and_retains_intent() {
    bounded::<240>(async {
        let runner = FsmRunner::new(FsmMode::WaitOnSibling);
        let world = std::sync::Arc::new(RoundWorld::new(runner).await);
        let a = round("a", 1);
        let a_key = a.input.key.clone();
        assert!(matches!(
            world.admission.admit(&a).await.unwrap(),
            Admission::New { .. }
        ));
        let processor = local_processor(&world);
        // The persistent conflict (second sink) owns the paired publication
        // identities from the first feed frame on: every append and publish
        // of this session conflicts, so nothing can ever deliver or prune.
        wedge_conflict(&world);
        settle_a_round(&world, &processor, &a_key.tid).await;
        let mut intents = publications(&world).await;
        for _ in 0..64 {
            drive(&world, &processor, 1).await;
            intents = publications(&world).await;
            let view = world.round_view(&a_key).await.expect("A's view");
            assert!(
                view.terminal.is_none(),
                "a conflicting feed must not produce a terminal"
            );
            assert!(
                matches!(
                    world.admission.admit(&round("b", 2)).await,
                    Ok(Admission::Busy)
                ),
                "the disputed round keeps the next round Busy"
            );
            if !intents.is_empty() {
                break;
            }
        }
        assert!(!intents.is_empty(), "the conflict registered intent items");

        // While the identity is disputed, no terminal, no next round. Let
        // obligations that completed BEFORE the conflict reach their durable
        // archives and prune; then the disputed remainder must freeze —
        // nothing can be delivered or cleared while every write conflicts.
        for _ in 0..8 {
            drive(&world, &processor, 1).await;
            assert!(
                !publications(&world).await.is_empty(),
                "the disputed intent stays registered"
            );
        }
        // While the identity is disputed, no terminal, no next round.
        // Obligations whose durable proof predates the conflict still reach
        // their own prune; once that settles, the disputed remainder must be
        // immune — nothing can be delivered or cleared while every write of
        // the session conflicts.
        for _ in 0..24 {
            drive(&world, &processor, 1).await;
            assert!(
                matches!(
                    world.round_view(&a_key).await.expect("A's view").terminal,
                    None
                ),
                "the round must not close over a conflicting sink"
            );
            assert!(
                !publications(&world).await.is_empty(),
                "the registered intent stays durably retained"
            );
        }
        let frozen = publications(&world).await;
        assert!(!frozen.is_empty(), "the conflict froze real pending intent");
        for _ in 0..16 {
            drive(&world, &processor, 1).await;
            let retained = publications(&world).await;
            for publication in &frozen {
                assert_eq!(
                    find_publication(&retained, &publication.effect)
                        .expect("the disputed intent item is never cleared"),
                    publication
                );
            }
            assert!(
                matches!(
                    world.round_view(&a_key).await.expect("A's view").terminal,
                    None
                ),
                "the round must not close over a conflicting sink"
            );
        }

        // The conflict resolves: the round completes exactly once — one
        // settled terminal, every public event id exactly once.
        release_conflict(&world);
        until(&world, &processor, |c| {
            !c.feed_publications.iter().any(|p| p.tid == a_key.tid)
        })
        .await;
        // The authority close follows the delivered pair through the spine's
        // own next visits; the view is fixed once it lands.
        for _ in 0..400 {
            drive(&world, &processor, 1).await;
            if world
                .round_view(&a_key)
                .await
                .expect("A's view")
                .terminal
                .is_some()
            {
                break;
            }
        }
        let events = feed_events(&world).await;
        let mut seen = std::collections::BTreeSet::new();
        for event in &events {
            assert!(
                seen.insert(event.id),
                "exactly-once completion after the conflict resolved"
            );
        }
        let view = world.round_view(&a_key).await.expect("A's view");
        assert!(view.terminal.is_some(), "the round closes after resolution");
    })
    .await;
}

/// The terminal window's archive carries the real artifact parts and the
/// complete ordered public history — content, order and unique non-empty
/// artifact ids — and `get(a)` reads exactly those parts through the
/// authoritative cut, with each part reconstructing digest-consistently
/// from the published feed frame.
#[tokio::test]
async fn terminal_window_archives_real_parts_and_public_history() {
    bounded::<240>(async {
        let runner = FsmRunner::new(FsmMode::WaitOnSibling);
        let world = std::sync::Arc::new(RoundWorld::new(runner).await);
        let a = round("a", 1);
        let a_key = a.input.key.clone();
        assert!(matches!(
            world.admission.admit(&a).await.unwrap(),
            Admission::New { .. }
        ));
        let processor = local_processor(&world);
        settle_a_round(&world, &processor, &a_key.tid).await;
        let retired = until(&world, &processor, |c| {
            c.tasks.iter().all(|t| t.tid != a_key.tid)
        })
        .await;
        let _ = retired;

        // Real parts through the authoritative cut: unique, non-empty
        // artifact ids, order and content.
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
        assert_eq!(parts.len(), 1, "exactly one artifact part");
        let (tid, part) = &parts[0];
        assert_eq!(tid, &a_key.tid);
        assert!(
            !part.artifact_id.0.is_empty(),
            "the artifact id is real and non-empty"
        );
        assert_eq!(part.order.get(), 1, "the first part's order");
        assert_eq!(
            part.part,
            RoundPart::Text {
                text: "answer-of-round".into()
            },
            "the part content is the runner's real artifact"
        );

        // The committed archive exposes the complete ordered public history
        // through the same cut.
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
        assert!(
            !archive.public_history.is_empty(),
            "public history is real content, never a placeholder"
        );
        let mut order = Vec::new();
        for event in &archive.public_history {
            if let OutputBody::Artifact {
                artifact_id,
                update: ArtifactUpdate::Upsert { content },
                ..
            } = &event.body
            {
                assert_eq!(artifact_id, &part.artifact_id);
                match content {
                    Payload::Inline(value) => assert_eq!(
                        part.part,
                        RoundPart::Text {
                            text: value
                                .as_str()
                                .expect("the answer payload is text")
                                .to_owned()
                        },
                        "the archived part restores the feed frame by content"
                    ),
                    other => panic!("expected inline payload, got {other:?}"),
                }
            }
            order.push((event.id.commit, event.id.index));
        }
        assert!(
            order.windows(2).all(|pair| pair[0] < pair[1]),
            "public history preserves the committed order"
        );

        // The feed frames and the archived parts name the same artifact and
        // restore the same digest: exactly one artifact frame exists, the
        // archive declares exactly that part, and the terminal frame is the
        // last public event of the round.
        let events = feed_events(&world).await;
        let artifact_frames: Vec<_> = events
            .iter()
            .filter(|event| {
                event.tid == a_key.tid
                    && matches!(
                        &event.body,
                        OutputBody::Artifact {
                            artifact_id,
                            update: ArtifactUpdate::Upsert { .. },
                            ..
                        } if *artifact_id == part.artifact_id
                    )
            })
            .collect();
        assert_eq!(artifact_frames.len(), 1, "the artifact frame published once");
        assert_eq!(archive.artifacts.len(), 1, "the archive declares the part");
        assert_eq!(archive.record.tid, a_key.tid);
        let artifact = artifact_index(&events, &a_key.tid).unwrap();
        let terminal = terminal_index(&events, &a_key.tid).unwrap();
        assert!(artifact < terminal, "artifact precedes terminal in the feed");
        assert_eq!(
            events.last().unwrap().id,
            archive.public_history.last().unwrap().id,
            "the archive's public history ends where the feed does"
        );
    })
    .await;
}
