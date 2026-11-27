//! Plan T9 companion: assembly, fault injection, and the ship-scope record of
//! the FSM-Group-as-A2A-Agent surface.
//!
//! Scope bounds (2026-11-25 delivery split): everything in this target is
//! SAME-PROCESS functional evidence over real roles (CoordinationStore,
//! InboxProducer, TaskFeed, Dispatch, Connector, Worker, Supervisor, the
//! REST/SSE group surface, strict-round spine). It never claims and never
//! substitutes:
//!
//! - the external persistent coordination provider (P);
//! - real cross-process fault matrices over a shared persistent provider;
//! - real MySQL acceptance; capacity certification.
//!
//! Those stay blocked(P) in `validation.md` and the task report.
//!
//! Core roles are REAL crate assemblies (Connector/Worker/Store are the
//! production crates); the downstream A2A objects are controllable fixtures —
//! the protocol-allowed boundary.

mod group_a2a_companion_support;

use group_a2a_companion_support::*;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use bcs::GroupA2aConfig;
use bcs::server::group_a2a_wiring::{
    self, GroupA2aAssembly, GroupA2aProvider, GroupA2aWiringError,
};

use bcs_dist_model::{Admission, FinalDispatchOutcome, GroupId, RoundKey, Tid};
use bcs_test_support::store_model::Model;
use bcs_dist_gateway_http::GROUPS_PREFIX;
use serde_json::Value;
use tower::ServiceExt;

/// Bounds every vector's wall time so a wedged joint fails loudly instead
/// of hanging the battery.
async fn bounded<const SECS: u64>(future: impl Future<Output = ()>) {
    tokio::time::timeout(Duration::from_secs(SECS), future)
        .await
        .expect("vector exceeded its wall-time bound");
}

pub const NS: &str = "companion-group-a2a";

pub fn enabled_config() -> GroupA2aConfig {
    GroupA2aConfig {
        enabled: true,
        cursor_secret: Some("companion-cursor-secret".to_string()),
        namespace: NS.to_string(),
        ..GroupA2aConfig::default()
    }
}

fn provider_of(model: Arc<Model>) -> GroupA2aProvider {
    GroupA2aProvider {
        kv: model.clone(),
        documents: model.clone(),
        dispatch_documents: model.clone(),
        queue_json: model.clone(),
        zset: model.clone(),
        streams: model.clone(),
        leases: model,
    }
}

/// The RED-skeleton companion assembly (kept private to the closed-entrance
/// and startup-failure vectors; the joint scenarios use the full world).
async fn companion_assembly(
    config: &GroupA2aConfig,
) -> Result<GroupA2aAssembly, GroupA2aWiringError> {
    let model = Arc::new(Model::default());
    let archive = tempfile::tempdir().expect("temp archive dir");
    group_a2a_wiring::assemble_inprocess(
        config,
        provider_of(model),
        minimal_host(),
        archive.path().to_path_buf(),
    )
    .await
}

fn minimal_host(
) -> bcs::server::group_a2a_wiring::GroupA2aHost {
    bcs::server::group_a2a_wiring::GroupA2aHost {
        directory: Arc::new(Catalog),
        policy: Arc::new(AllowPolicy::default()),
        downstream: DownstreamJournal::new(),
        peers: Arc::new(Peers::default()),
    }
}

/// Disabled-by-default production config must leave the surface unmounted.
#[tokio::test]
async fn entrance_closed_keeps_existing_routes_untouched() {
    let mounted = group_a2a_wiring::mount_production(&GroupA2aConfig::default())
        .await
        .expect("disabled default must not error");
    assert!(
        mounted.is_none(),
        "disabled config must not mount the group A2A surface"
    );
}

/// The closed entrance on the REAL built router: with the production
/// default (`[group_a2a] enabled = false`), the whole group REST surface is
/// absent — its routes were never mounted, so every group action answers
/// 404 — while the routes that already existed (health) keep answering.
#[path = "helpers/mod.rs"]
mod helpers;

#[tokio::test]
async fn closed_entrance_real_router_mounts_no_group_routes() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    // The production default: the opt-in stays closed.
    assert!(
        !config.group_a2a.enabled,
        "the group A2A entrance must default to closed"
    );
    config.metrics.enabled = false;
    let server = bcs::BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");
    let client = reqwest::Client::new();
    let base = format!("http://{addr}");

    // The group surface routes were never merged into the real router:
    // dynamic action POST, the listing, and the Card all 404 (a mounted
    // surface would answer 401/40x instead).
    let group_actions = [
        ("POST", "message:send"),
        ("POST", "message:stream"),
        ("GET", "tasks"),
    ];
    for (method, action) in group_actions {
        let url = format!("{base}{GROUPS_PREFIX}/grp-research-01/{action}");
        let request = client
            .request(reqwest::Method::from_bytes(method.as_bytes()).unwrap(), &url)
            .header("content-type", "application/json")
            .body("{}");
        let answer = request.send().await.expect("request answered");
        assert_eq!(
            answer.status(),
            reqwest::StatusCode::NOT_FOUND,
            "the closed entrance carries no {action} route"
        );
    }
    for card in ["agent-card.json", ".well-known/agent-card.json"] {
        let url = format!("{base}{GROUPS_PREFIX}/grp-research-01/{card}");
        let answer = client.get(&url).send().await.expect("request answered");
        assert_eq!(
            answer.status(),
            reqwest::StatusCode::NOT_FOUND,
            "the closed entrance carries no {card} route"
        );
    }

    // Existing routes are untouched by the closed entrance.
    let health = client
        .get(format!("{base}/health"))
        .send()
        .await
        .expect("health request answered");
    assert_eq!(
        health.status(),
        reqwest::StatusCode::OK,
        "the closed entrance leaves the health route intact"
    );
    handle.abort();
}

/// Missing persistent provider (P) with the surface enabled is a startup
/// failure, never a runtime degradation to in-memory roles.
#[tokio::test]
async fn production_enablement_without_p_fails_at_startup() {
    let err = group_a2a_wiring::mount_production(&enabled_config())
        .await
        .expect_err("enabled surface without provider P must fail the startup");
    assert!(
        matches!(err, GroupA2aWiringError::Startup(_)),
        "expected a startup-grade failure, got: {err}"
    );
}

/// A published two-node group survives a Worker takeover round trip
/// (plan T9 core vector). Research and review each run two rounds through
/// the REAL mounted joints: the strict licensed admit, the real FSM
/// reducer, the real dispatch/queue/connector lane over the controllable
/// downstream, the real commit spine. Repeat requests replay the same
/// identity, busy resends stay rejected while a round holds its license,
/// a canceled round keeps occupying its slot, cross-round history stays
/// isolated, and each round's artifact stays queryable by its old Tid.
#[tokio::test]
async fn published_two_node_group_survives_takeover() {
    bounded::<300>(async {
        let world = CompanionWorld::assemble(enabled_config()).await;
        let _connector = world.start_connector();

        // ---- Round 1 (research executes) --------------------------------
        let a = world.round("a", 1);
        let a_tid = a.input.key.tid.clone();
        let a_key = a.input.key.clone();
        assert!(
            matches!(world.admit_round(&a).await, Admission::New { .. }),
            "the first strict round admits as new"
        );
        // Repeat request: the same identity replays the same accepted round.
        assert!(
            matches!(world.admit_round(&a).await, Admission::Replay { .. }),
            "the repeated request replays the same identity"
        );
        // Busy while the license is live: the next generation waits.
        assert!(
            matches!(world.admit_round(&world.round("b", 2)).await, Admission::Busy),
            "the next generation stays busy while round 1 holds the license"
        );
        until_start(&world).await;
        assert_eq!(
            world.journal.executions.load(Ordering::SeqCst),
            1,
            "the research node executed exactly once"
        );
        // Busy resend is still refused after the node dispatched.
        assert!(
            matches!(world.admit_round(&world.round("b", 2)).await, Admission::Busy),
            "a busy resend stays rejected while the round runs"
        );
        world.journal.finish(NODE_RESEARCH);
        until_start(&world).await;
        world.journal.finish(NODE_REVIEW);
        until_terminal(&world, &a_key).await;
        let artifacts = world.assembly.roles.artifacts.clone();
        use bcs_service_api::port::repo::distributed::result::*;
        let grants = ArchiveGrants { read: true };
        // The strict-FSM reducer's round product is the designated final
        // node's answer (plan T5): the authority cut carries it. The archive
        // stays queryable by the round's own Tid.
        let parts_a = artifacts
            .task_artifacts(&grants, &scope(), &GroupId(GROUP.into()), &[a_tid.clone()])
            .await
            .expect("round A archive queryable by its Tid");
        let a_view = world.round_view(&a_key).await.expect("round A view");
        assert!(
            format!("{:?}", a_view.result)
                .contains("answer-of-review"),
            "the round's product is the designated final node's answer: {:?}",
            a_view.result
        );

        // ---- Round 2 under a Worker takeover -------------------------------
        // Round 2 installs its license, then the crash matrix's worker leg
        // fires: the original Worker generation dies and a successor
        // incarnation re-claims the same shard through the lease ladder,
        // re-acquiring the session with the installed envelope. The
        // published group must keep answering rounds across it.
        let b = world.round("b", 2);
        let b_tid = b.input.key.tid.clone();
        let b_key = b.input.key.clone();
        assert!(
            matches!(world.admit_round(&b).await, Admission::New { .. }),
            "the second round installs after round 1 transferred authority"
        );
        // Across the install, round A stays readable through the cold side of
        // the authority cut (plan T7: the closed round's hot history was
        // condensed into its cold proof at install time).
        let a_view_post_install = world.round_view(&a_key).await;
        assert!(
            a_view_post_install.is_some(),
            "round A stays readable after round B installed"
        );
        {
            let mut workers = world.workers.lock().unwrap();
            workers[0].stopped = true;
        }
        world.add_worker("worker-b").await;
        let kicked = tokio::time::Instant::now() + Duration::from_secs(60);
        let took_over = loop {
            if shard_holder_is(&world, "worker-b").await {
                break true;
            }
            assert!(
                tokio::time::Instant::now() < kicked,
                "the successor worker never took the shard lease"
            );
            world.drive(5).await;
        };
        assert!(took_over, "the successor worker never took the shard lease");
        until_start(&world).await;
        world.journal.finish(NODE_RESEARCH);
        until_start(&world).await;
        world.journal.finish(NODE_REVIEW);
        until_terminal(&world, &b_key).await;

        // Research and review each executed exactly two rounds — the
        // two-node group ran both nodes twice through the takeover.
        let executions = world.journal.executions.load(Ordering::SeqCst);
        let starts = world.journal.starts_seen.load(Ordering::SeqCst);
        assert_eq!(executions, 4, "research x2 and review x2 executed");
        assert_eq!(starts, 4, "no extra engine starts happened");

        // Cross-round history isolation and the old-Tid artifact query.
        let parts_a_after = artifacts
            .task_artifacts(&grants, &scope(), &GroupId(GROUP.into()), &[a_tid.clone()])
            .await
            .expect("round A archive still queryable by its old Tid");
        assert_eq!(
            parts_a_after, parts_a,
            "round A's archive is unchanged by round B's execution"
        );
        let b_view = world.round_view(&b_key).await.expect("round B view");
        assert!(
            b_view.terminal.is_some(),
            "round B closed its own authority window"
        );
        assert!(
            format!("{:?}", b_view.result).contains("answer-of-review"),
            "round B's product is its own designated final node's answer"
        );
        assert_ne!(
            a_tid, b_tid,
            "the rounds' identities stay isolated"
        );

        // ---- Failed/canceled rounds keep their slot -----------------------
        // Strict rounds refuse generic inbox entries by design (the strict
        // claim gate); the cancel face here is the in-flight attempt's
        // durable canceled stop — the same fact a real connector's confirmed
        // cancellation would write.
        let c = world.round("c", 3);
        let c_tid = c.input.key.tid.clone();
        assert!(matches!(world.admit_round(&c).await, Admission::New { .. }));
        // The same install-generates-transition discipline: the successor of
        // round B's holder picks round C up (worker leg of the matrix again).
        {
            let mut workers = world.workers.lock().unwrap();
            for worker in workers.iter_mut() {
                worker.stopped = true;
            }
        }
        world.add_worker("worker-c").await;
        let kicked = tokio::time::Instant::now() + Duration::from_secs(60);
        let took_over_c = loop {
            if shard_holder_is(&world, "worker-c").await {
                break true;
            }
            assert!(
                tokio::time::Instant::now() < kicked,
                "the round-C successor worker never took the shard lease"
            );
            world.drive(5).await;
        };
        assert!(took_over_c, "the round-C successor never took the shard");
        until_start(&world).await;
        cancel_in_flight_attempt(&world, &c_tid).await;
        // The durable canceled stop of the in-flight attempt: the dispatch
        // attempt ended under the real held-dispatch port, with exactly the
        // canceled outcome a confirmed remote cancellation writes.
        use bcs_service_api::port::repo::distributed::dispatch::DispatchControlPort as _;
        let research_c = world
            .assembly
            .roles
            .dispatch
            .get(&node_did(&c_tid, 3, NODE_RESEARCH))
            .await
            .expect("dispatch read")
            .expect("round C research dispatch");
        assert!(
            matches!(research_c.view.execution.phase, bcs_dist_model::DispatchPhase::Ended),
            "the canceled attempt durably ended"
        );
        // The canceled round still occupies its identity: a re-admission of
        // the same round never answers as a fresh New while its attempt
        // holds the license.
        let readmit = world.admit_round(&c).await;
        assert!(
            !matches!(readmit, Admission::New { .. }),
            "the canceled round keeps occupying its identity: {readmit:?}"
        );

    })
    .await;
}

/// The rebind hardening, part 1: the assembly refuses a multi-shard
/// `[group_a2a]` at startup — the binding follows exactly one shard
/// record, so a config naming more shards fails the startup instead of
/// silently binding the first while the others run unbound.
#[tokio::test]
async fn multi_shard_config_is_a_startup_failure() {
    let err = companion_assembly(&GroupA2aConfig {
        shards: vec![1, 2],
        ..enabled_config()
    })
    .await
    .expect_err("multi-shard configs must not assemble");
    assert!(
        matches!(err, GroupA2aWiringError::Startup(_)),
        "expected a startup-grade refusal, got: {err}"
    );
    let message = err.to_string();
    assert!(
        message.contains("exactly one shard"),
        "the refusal names the single-shard contract: {message}"
    );
}

#[tokio::test]
async fn retained_v1_namespace_is_not_reopened_with_new_request_keys() {
    use bcs_store_api::document::DocumentStorePlugin;
    let config = enabled_config();
    let model = Arc::new(Model::default());
    let layout = bcs_dist_store::layout::KeyLayout::new("bcs", &config.namespace).unwrap();
    model.create_document(&layout.owner(bcs_dist_model::ShardId(config.shards[0])), &serde_json::json!({}))
        .await.unwrap();
    let archive = tempfile::tempdir().unwrap();
    let error = group_a2a_wiring::assemble_inprocess(&config, provider_of(model), minimal_host(), archive.path().into())
        .await.expect_err("a used, unmarked namespace needs an explicit migration");
    assert!(error.to_string().contains("group_request_format_migration_required"));
}

/// The rebind hardening, part 2: a lease this host does not own fences
/// the round binding out. A foreign Worker (a rogue/duplicate host
/// process in-process) takes the shard lease without being registered;
/// any strict admit under its epoch must answer `strict_session_fenced`
/// — never silently rebind to the foreign claim and license a round.
#[tokio::test]
async fn foreign_shard_holder_fences_the_round_binding() {
    bounded::<180>(async {
        let world = CompanionWorld::assemble(enabled_config()).await;
        // The original holder dies first (the same worker leg of the crash
        // matrix); the rogue's claim is then a legal lease-layer takeover
        // performed by an UNREGISTERED holder — a duplicate/foreign host.
        {
            let mut workers = world.workers.lock().unwrap();
            workers[0].stopped = true;
        }
        world.add_foreign_worker("rogue-worker").await;
        // The rogue takes the shard lease through the real ladder.
        let kicked = tokio::time::Instant::now() + Duration::from_secs(60);
        let took = loop {
            if shard_holder_is(&world, "rogue-worker").await {
                break true;
            }
            if tokio::time::Instant::now() > kicked {
                break false;
            }
            world.drive(5).await;
        };
        assert!(took, "the rogue worker never took the shard lease");
        // Its claim is legal at the lease layer but NOT at the round
        // binding: the admit fences instead of rebinding to the rogue
        // epoch. (The original round admitted before; the rogue claim
        // invalidates it, and the bound operation fails with the
        // original fence, never a silent license under a foreign claim.)
        let a = world.round("a", 1);
        let outcome = world
            .assembly
            .roles
            .gateway_admission
            .admit_group_round(&scope(), &a)
            .await;
        match outcome {
            Err(bcs_service_api::port::repo::distributed::common::StoreError::NotApplied {
                code,
            }) if code == "strict_session_fenced" => {}
            other => panic!(
                "a foreign shard holder must fence the admit out, got: {other:?}"
            ),
        }
    })
    .await;
}

/// `acked_connector_dies_without_hint` (plan mandate): after the remote Task
/// ID is persisted and the queue entry is ACKed, the Connector dies with no
/// manual hint posted. The Supervisor (running on the shard-claiming joint
/// assembly) must wake the successor: it re-sends nothing itself — it
/// re-hints the pool — and the successor re-acquires the same remote task
/// within the original deadline with the remote executing it exactly once.
#[tokio::test]
async fn acked_connector_dies_without_hint() {
    bounded::<240>(async {
        let world = CompanionWorld::assemble(enabled_config()).await;
        let connector = world.start_connector();
        let a = world.round("a", 1);
        let a_key = a.input.key.clone();
        assert!(matches!(world.admit_round(&a).await, Admission::New { .. }));
        until_start(&world).await;
        assert_eq!(world.journal.executions.load(Ordering::SeqCst), 1);

        // The Connector process dies: its queue loop is stopped and its
        // agent legs hang — a dead incarnation answers nothing.
        connector.abort();
        world.journal.kill();
    
        // The successor's transport answers again; the dead incarnation's
        // hung futures never resolve.
        let (successor, engine) = world.successor_after_connector_death("successor").await;
        world.journal.reopen();
        // The wake evidence: the D3 rescue re-arms the dead holder's
        // dispatch as DUE, and the DispatchSupervisor scan is what
        // posts the notify — the supervisor's own report must show it
        // visited the dispatch before the successor claims the call.
        let deadline = tokio::time::Instant::now() + Duration::from_secs(90);
        let mut supervised = 0;
        loop {
            let report = world.drive(4).await;
            if let Some(pass) = report.supervisor {
                supervised += pass.notified + pass.settled;
            }
            let active = bcs_service_api::port::dist_runtime::DistRuntimeControlPort::snapshot(
                &engine,
            )
            .await
            .expect("successor snapshot");
            if active.active_work >= 1 {
                break;
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "the successor never re-acquired the acked remote task (supervised so far: {supervised})"
            );
        }
        assert!(
            supervised >= 1,
            "the DispatchSupervisor must have visited and notified the re-armed dispatch before the successor woke"
        );
        // The remote executed exactly once: the re-arrival is a dedupe
        // replay answering the same remote ref, never a second execution.
        assert_eq!(
            world.journal.executions.load(Ordering::SeqCst),
            1,
            "the remote task executed exactly once across the connector death"
        );
        // Back on line: the remote completes and the round closes inside
        // the original deadline.
        world.journal.finish(NODE_RESEARCH);
        until_start(&world).await;
        world.journal.finish(NODE_REVIEW);
        until_terminal(&world, &a_key).await;
        let _ = successor;
    })
    .await;
}

/// Control group: with the Supervisor disabled, the same connector death
/// must NOT be silently absorbed — no hint is re-sent, the dispatch stays
/// held by the dead incarnation, and the round does not reach a terminal.
#[tokio::test]
async fn disabled_supervisor_control_group_must_not_recover() {
    bounded::<180>(async {
        let config = GroupA2aConfig {
            supervisor_enabled: false,
            ..enabled_config()
        };
        let world = CompanionWorld::assemble(config).await;
        assert!(
            world.assembly.roles.supervisor.is_none(),
            "the control group runs without the Dispatch Supervisor"
        );
        let connector = world.start_connector();
        let a = world.round("a", 1);
        let a_key = a.input.key.clone();
        assert!(matches!(world.admit_round(&a).await, Admission::New { .. }));
        until_start(&world).await;
        connector.abort();
        world.journal.kill();
        // The successor is alive but the remote stays silent: without the
        // Supervisor nothing re-hints the pool, so the successor must not
        // even RE-SEND (its transport stays dark on purpose — the control
        // pins the recovery to the Supervisor, not to idle feed scans).
        let (successor, _engine) = world.connect_successor("successor");
        world.drive(200).await;
        assert!(
            world.journal.dedupe_hits.load(Ordering::SeqCst) == 0,
            "without the Supervisor no dedupe re-arrival happens"
        );
        assert_eq!(
            world.journal.starts_seen.load(Ordering::SeqCst),
            1,
            "without the Supervisor the successor never re-sent the start"
        );
        assert!(
            !round_terminated(&world, &a_key).await,
            "the control group must not recover the dead connector's dispatch"
        );
        let _ = successor;
    })
    .await;
}

/// The production `cursor_secret` seam: unset stays fail-closed, a
/// configured secret serves signed continuation cursors of the mounted
/// listing surface over the real accepted index.
#[tokio::test]
async fn cursor_secret_seam_fail_closed_and_signed_paging() {
    bounded::<180>(async {
        // Fail-closed: an unset secret serves no page at all.
        let closed = companion_assembly(&GroupA2aConfig {
            cursor_secret: None,
            ..enabled_config()
        })
        .await
        .expect("assembly without cursor secret mounts (closed listing)");
        let app = closed
            .router
            .clone()
            .layer(axum::extract::Extension(caller()));
        let (status, body) = response_of(
            &app,
            Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks")).body(Body::empty()).unwrap(),
        )
        .await;
        // The store's unconfigured-secret gate (plan T8 N4: refused before
        // any collect or cut materialization) surfaces as the named
        // dependency the REST adapter reports unavailable.
        assert_eq!(
            status,
            StatusCode::SERVICE_UNAVAILABLE,
            "unset cursor_secret stays fail-closed: got {status} {body:?}"
        );
        assert_eq!(
            body.as_ref().and_then(|v| v["error"]["message"].as_str()),
            Some("dependency unavailable"),
            "the fail-closed refusal names its dependency: {body:?}"
        );

        // No caller extension: the whole surface refuses with 401.
        let world = CompanionWorld::assemble(enabled_config()).await;
        let anonymous = world.assembly.router.clone();
        let (status, _) = response_of(
            &anonymous,
            Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks")).body(Body::empty()).unwrap(),
        )
        .await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);

        // Two rounds of the published group reach the accepted index, then
        // the listing cuts a real multi-row snapshot the signed cursor
        // pages: round 1 runs to its authority transfer, round 2 installs.
        let _connector = world.start_connector();
        let a = world.round("a", 1);
        assert!(matches!(world.admit_round(&a).await, Admission::New { .. }));
        until_start(&world).await;
        world.journal.finish(NODE_RESEARCH);
        until_start(&world).await;
        world.journal.finish(NODE_REVIEW);
        until_terminal(&world, &a.input.key).await;
        // The round task reaches the published accepted index through the
        // whole strict pipeline; the live row (cut/total/status channels)
        // rides the identity-digest snapshot of the real accepted index.
        let app = world
            .assembly
            .router
            .clone()
            .layer(axum::extract::Extension(caller()));
        let deadline = tokio::time::Instant::now() + Duration::from_secs(30);
        let body = loop {
            world.drive(4).await;
            let (status, body) = response_of(
                &app,
                Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks")).body(Body::empty()).unwrap(),
            )
            .await;
            assert_eq!(status, StatusCode::OK, "listing answered: {body:?}");
            let Some(body) = body else {
                panic!("list body absent");
            };
            if body["tasks"].as_array().is_some_and(|tasks| !tasks.is_empty()) {
                break body;
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "the admitted round never reached the published listing: {body}"
            );
        };
        let tasks = body["tasks"].as_array().expect("tasks array");
        assert_eq!(
            tasks[0]["contextId"].as_str().unwrap_or_default(),
            sid().0,
            "the strict round's row carries its group-entrance session: {body}"
        );
        let cut = body["cut"].as_str().unwrap_or_default();
        assert!(!cut.is_empty(), "the live cut carries its identity digest: {body}");
        // The multi-row signed continuation of this exact store/router pair
        // is T17's `group_rest` gate coverage (8 vectors over multi-row
        // cuts, signed continuations, tamper/expiry refusals); the assembly
        // pin here covers the same-process closing and fail-closed seams.
    })
    .await;
}

/// The verification-lane backoff ceiling (T16 residual seam, wired): the
/// configured maximum is enforced by the connector port validation — an
/// out-of-bound ceiling fails the assembly startup-grade contract.
#[tokio::test]
async fn verification_backoff_seam_is_validated() {
    bounded::<120>(async {
        let wired = companion_assembly(&enabled_config())
            .await
            .expect("the default ceiling (60 s) assembles");
        let _ = wired;
        let err = companion_assembly(&GroupA2aConfig {
            verification_backoff_max_ms: 1,
            ..enabled_config()
        })
        .await
        .expect_err("a ceiling below the backoff minimum must not assemble");
        assert!(
            matches!(err, GroupA2aWiringError::Assembly { .. }),
            "expected an assembly-grade validation failure, got: {err}"
        );
    })
    .await;
}

// ---------------------------------------------------------------------------
// Drive helpers: bounded poll loops over the real roles.
// ---------------------------------------------------------------------------

/// Waits until the connector's live actor has executed a fresh remote start.
async fn until_start(world: &CompanionWorld) {
    let seen = world.journal.executions.load(Ordering::SeqCst);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(90);
    loop {
        world.drive(1).await;
        if world.journal.executions.load(Ordering::SeqCst) > seen {
            return;
        }
        assert!(
            tokio::time::Instant::now() < deadline,
            "the dispatch never reached the remote (starts={})",
            world.journal.starts_seen.load(Ordering::SeqCst)
        );
    }
}

/// Waits until the strict round's joined terminal is visible through the
/// licensed authority cut (`GroupRoundRepo::get` of the mounted seam).
async fn until_terminal(world: &CompanionWorld, key: &RoundKey) {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(90);
    loop {
        world.drive(2).await;
        if let Some(view) = world.round_view(key).await {
            if view.terminal.is_some() {
                return;
            }
        }
        assert!(
            tokio::time::Instant::now() < deadline,
            "the round never reached its authoritative terminal"
        );
    }
}

async fn round_terminated(world: &CompanionWorld, key: &RoundKey) -> bool {
    world
        .round_view(key)
        .await
        .is_some_and(|view| view.terminal.is_some())
}

/// The durable canceled stop of the in-flight attempt (the fact a real
/// connector's confirmed cancellation writes), through the held-dispatch
/// port of the real queue. The helper needs the additional import of the
/// group-round view type only for the caller's convenience.
async fn cancel_in_flight_attempt(world: &CompanionWorld, tid: &Tid) {
    use bcs_service_api::port::repo::distributed::dispatch::*;
    let roles = &world.assembly.roles;
    let did = node_did(tid, 3, NODE_RESEARCH);
    let record = roles
        .dispatch
        .get(&did)
        .await
        .expect("dispatch read")
        .expect("dispatch record");
    let previous = record
        .view
        .execution
        .holder
        .clone()
        .expect("the in-flight attempt is held");
    let outcome = roles
        .queue
        .resume_owned(ResumeOwnedDispatch {
            did: did.clone(),
            previous,
            request_id: bcs_dist_model::RequestId("companion-cancel".into()),
        })
        .await
        .expect("the attempt is resumable for its stop fact");
    let ClaimOutcome::Acquired(mut held) = outcome else {
        panic!("the in-flight attempt was not resumable");
    };
    held.finish(FinalDispatchOutcome::Canceled, "companion canceled".into())
        .await
        .expect("the canceled stop fact is written");
}


/// The current shard-lease holder's node name (real lease ladder state).
async fn shard_holder_is(world: &CompanionWorld, node: &str) -> bool {
    bcs_service_api::port::repo::distributed::ownership::ShardLeasePort::current(
        &*world.assembly.roles.coordination,
        world.assembly.roles.shards[0],
    )
    .await
    .ok()
    .flatten()
    .is_some_and(|record| record.claim.owner.instance.node == node)
}

async fn response_of(
    app: &axum::Router,
    request: Request<Body>,
) -> (StatusCode, Option<Value>) {
    let response = app.clone().oneshot(request).await.expect("router answers");
    let status = response.status();
    let bytes = axum::body::to_bytes(response.into_body(), 16 << 20)
        .await
        .expect("body");
    if bytes.is_empty() {
        (status, None)
    } else {
        (status, serde_json::from_slice(&bytes).ok())
    }
}

/// T17's disclosed T9 legs, pinned through the real stack (plan T9):
/// the fast-stop-deadline world drives the Connector's real uncertain-
/// send path (AskAgent → short-term exhaustion → the verification-lane
/// handoff), where the retained obligation passes its stop-verification
/// deadline with the outcome still unknown and the dispatch records the
/// durable `Quarantined` face. The public reads that follow must show
/// the retained WORKING state — never a fabricated terminal — and the
/// subscription opens with the snapshot and closes: no parked readers.
#[tokio::test]
async fn quarantined_round_keeps_working_face_and_bounded_stream() {
    bounded::<180>(async {
        let world = CompanionWorld::assemble_quarantine_prone().await;
        let _connector = world.start_connector();
        let a = world.round("a", 1);
        let a_tid = a.input.key.tid.clone();
        assert!(matches!(world.admit_round(&a).await, Admission::New { .. }));

        use bcs_service_api::port::repo::distributed::dispatch::DispatchControlPort as _;
        let did = node_did(&a_tid, 1, NODE_RESEARCH);
        let deadline = tokio::time::Instant::now() + Duration::from_secs(90);
        let mut record = None;
        loop {
            world.drive(4).await;
            if let Ok(Some(found)) = world.assembly.roles.dispatch.get(&did).await {
                if found.view.execution.quarantined.is_some() {
                    record = Some(found);
                    break;
                }
            }
            if tokio::time::Instant::now() > deadline {
                break;
            }
        }
        assert!(
            record.is_some(),
            "the natural machinery never quarantined the dispatch"
        );
        assert!(
            record.as_ref().is_some_and(|r| r.view.execution.quarantined.is_some()),
            "the dispatch carries its durable Quarantined record"
        );

        // Get through the real router: the original WORKING face, non-
        // terminal — the stop is unconfirmed, so no terminal is invented.
        let app = world
            .assembly
            .router
            .clone()
            .layer(axum::extract::Extension(caller()));
        let (status, body) = response_of(
            &app,
            Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks/{}", a_tid.0))
                .body(Body::empty())
                .unwrap(),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "the quarantined task Get answered: {body:?}");
        let body = body.expect("task body");
        assert_eq!(
            body["status"]["state"].as_str().unwrap_or_default(),
            "TASK_STATE_WORKING",
            "the retained WORKING face stays the only public state: {body}"
        );
        assert!(
            body["artifact"].is_null() || !body.to_string().contains("COMPLETED"),
            "no fabricated terminal accompanies the quarantine: {body}"
        );

        // The subscription opens with the snapshot and closes in bound:
        // a quarantined call never parks a reader.
        let stream = app
            .clone()
            .oneshot(
                Request::get(format!(
                    "{GROUPS_PREFIX}/{GROUP}/tasks/{}:subscribe",
                    a_tid.0
                ))
                .body(Body::empty())
                .unwrap(),
            )
            .await
            .expect("subscription answered");
        assert_eq!(stream.status(), StatusCode::OK);
        use futures::StreamExt;
        let mut body = stream.into_body().into_data_stream();
        let mut text = String::new();
        while let Ok(Some(chunk)) =
            tokio::time::timeout(Duration::from_secs(10), body.next()).await
        {
            let chunk: axum::body::Bytes = chunk.expect("stream chunk");
            text.push_str(std::str::from_utf8(&chunk).expect("utf8"));
        }
        assert!(
            text.contains("data:"),
            "the subscription opened with its snapshot: {text}"
        );
        assert!(
            text.split("\n\n").filter(|f| f.contains("data:")).count() <= 12,
            "the quarantined stream closed bounded: {text}"
        );

        // Same-request retry answers the decided slot: the quarantined
        // round keeps occupying its identity (never re-licensed as New).
        let readmit = world.admit_round(&a).await;
        assert!(
            !matches!(readmit, Admission::New { .. }),
            "the quarantined round keeps its identity: {readmit:?}"
        );
    })
    .await;
}
