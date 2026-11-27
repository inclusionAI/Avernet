//! Node-isolated authority vectors: a dropped reply without dedupe, two
//! nodes of one bot, and revocation versus rebinding between enqueue and
//! dispatch.
use crate::harness::*;
use bcs_dist_model::*;
use bcs_service_api::port::dist_agent::CallOperation;
use rig::*;
use std::sync::Arc;

// --------------------------------------------------------------------------
// T6 case 1: a controlled bot accepts the job and drops the reply. Without
// a query, a task read or a negotiated persistent dedupe, the possibly-sent
// start is never resent, exactly one send attempt is made, and no stop
// fact is fabricated.
// --------------------------------------------------------------------------

#[tokio::test]
async fn downstream_accept_then_drop_reply() {
    let agent = Arc::new(GrantedAgent::all(Arc::new(ScriptedAgent::default())));
    let rig = rig_with(agent.clone()).await;
    let mut spec = node_spec("n2", 1);
    // Idempotency is declared, but no persistent dedupe and no query: a
    // strict source must not resend on the declaration alone.
    spec.capabilities.idempotent_start = true;
    spec.capabilities.idempotent_input = true;
    spec.capabilities.query_by_task = false;
    spec.capabilities.query_by_request = false;
    spec.capabilities.subscribe = false;
    spec.capabilities.persistent_dedupe = false;
    let did = spec.did.clone();
    rig.rig.worker.create_spec(spec).await;
    // The start's outcome stays unknown and the reply never comes.
    rig.agent.inner.on_start(Reply::Unknown);
    rig.rig.worker.notify_did(&did).await;
    let running = rig.start();
    until("one send attempt", || async { rig.agent.inner.count("start") == 1 }).await;
    until("the call hands off and releases the ordinary slot", || async {
        running.active().await == 0
    }).await;
    assert_eq!(rig.agent.inner.count("start"), 1, "an odd start outcome is never blind-resent");
    assert_eq!(rig.phase(&did).await, "sending", "the call stays in its sending phase");
    assert!(rig.inbox_of(&did).await.is_empty(), "no stop fact is fabricated for an unknown delivery");
    running.task.abort();
}

// --------------------------------------------------------------------------
// T6 case 2: the same bot, two nodes of one round — separate dispatches,
// separate request ids, separate remote conversations. No node history is
// concatenated onto another node's input, and a first start reuses nobody's
// context.
// --------------------------------------------------------------------------

#[tokio::test]
async fn same_bot_distinct_node_conversations() {
    let agent = Arc::new(GrantedAgent::all(Arc::new(ScriptedAgent::default())));
    let rig = rig_with(agent.clone()).await;
    // Two nodes, the same bot (the same AgentBinding).
    let n2 = node_spec("n2", 1);
    let n5 = node_spec("n5", 1);
    assert_eq!(n2.target, n5.target, "the same bot is bound by both nodes");
    let did_n2 = n2.did.clone();
    let did_n5 = n5.did.clone();
    rig.rig.worker.create_spec(n2).await;
    rig.rig.worker.create_spec(n5).await;
    // The bot hands each node its own remote conversation.
    rig.agent.inner.on_start(Reply::Accepted);
    rig.agent.inner.on_remote(RemoteTaskRef { task_id: "rt-n2".into(), context_id: Some("ctx-n2".into()) });
    rig.agent.inner.on_start(Reply::Accepted);
    rig.agent.inner.on_remote(RemoteTaskRef { task_id: "rt-n5".into(), context_id: Some("ctx-n5".into()) });
    rig.rig.worker.notify_did(&did_n2).await;
    rig.rig.worker.notify_did(&did_n5).await;
    let running = rig.start();
    until("both dispatches accepted", || async {
        rig.phase(&did_n2).await == "accepted" && rig.phase(&did_n5).await == "accepted"
    })
    .await;

    // Each node is dispatched to under its own identity, and a first start
    // reuses no context.
    let starts: Vec<_> = agent
        .seen()
        .into_iter()
        .filter(|(_, _, op, _)| *op == CallOperation::Start)
        .collect();
    assert_eq!(starts.len(), 2);
    assert!(starts.iter().any(|(round, node, _, _)| round == ROUND && node.0 == "n2"));
    assert!(starts.iter().any(|(round, node, _, _)| round == ROUND && node.0 == "n5"));
    assert!(starts.iter().all(|(_, _, _, r)| r.is_none()), "a first start reuses no conversation");

    // The accepted remote tasks of the two nodes never collide, so one
    // node's continuation can never cross-read the other's.
    let remote_n2 = rig.doc(&did_n2).await["execution"]["remote"]["task_id"].clone();
    let remote_n5 = rig.doc(&did_n5).await["execution"]["remote"]["task_id"].clone();
    assert_ne!(remote_n2, remote_n5, "two nodes of one bot never share one remote session");
    running.task.abort();
}

// --------------------------------------------------------------------------
// T6 case 8: authorization is re-verified between enqueue and dispatch. A
// revoked grant sends nothing; an ordinary rebinding (a new binding version
// of the same bot) is not a revocation and dispatches cleanly.
// --------------------------------------------------------------------------

#[tokio::test]
async fn revocation_between_enqueue_and_dispatch() {
    let agent = Arc::new(GrantedAgent::all(Arc::new(ScriptedAgent::default())));
    let rig = rig_with(agent.clone()).await;
    // A dispatch was enqueued whose grant was revoked before it reached an
    // adapter.
    let revoked = node_spec("n2", 1);
    let credential = revoked.target.credential_ref.0.clone();
    let did = revoked.did.clone();
    agent.grant(
        "n2",
        &credential,
        Grants { start: false, cont: false, query: false, subscribe: false, cancel: false },
    );
    rig.rig.worker.create_spec(revoked).await;
    rig.rig.worker.notify_did(&did).await;
    let running = rig.start();
    until("the revoke was observed", || async { agent.denies() >= 1 }).await;
    tokio::time::sleep(std::time::Duration::from_millis(150)).await;
    assert_eq!(rig.agent.inner.count("start"), 0, "no unauthorized new input or history leaves the process");
    assert!(rig.inbox_of(&did).await.is_empty(), "no fabricated stop for an unauthorized dispatch");

    // An ordinary rebinding of the same bot serves the next attempt under
    // the new binding version.
    let rebound = {
        let mut spec = node_spec("n2", 2);
        spec.target.binding_version = BindingVersion("4".into());
        spec.target.credential_ref = CredentialRef("cred://agent-analyst/v4".into());
        spec
    };
    let did2 = rebound.did.clone();
    rig.rig.worker.create_spec(rebound).await;
    rig.agent.inner.on_start(Reply::Accepted);
    rig.rig.worker.notify_did(&did2).await;
    until("the rebound attempt starts", || async { rig.agent.inner.count("start") >= 1 }).await;
    let seen: Vec<_> = agent
        .seen()
        .into_iter()
        .filter(|(_, node, op, _)| node.0 == "n2" && *op == CallOperation::Start)
        .collect();
    // The revoked dispatch's attempts never reached the scripted agent; the
    // rebinding's attempt is the only start the adapter ever saw.
    assert_eq!(rig.agent.inner.count("start"), 1, "the rebound dispatch started exactly once");
    assert!(seen[0].3.is_none());
    running.task.abort();
}
