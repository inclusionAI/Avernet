mod common;
use bcn_runtime_api::{core::SessionRunner, port::repo::*};
use bcn_runtime_model::*;
use bcn_runtime_store::RuntimeStore;
use bcn_session_runner::DirectA2aRunner;
use common::*;
#[tokio::test]
async fn quarantine_survives_restart_and_only_checkpoint_progress_resets_takeovers() {
    let (repo, atomic, req) = fixture();
    let mut cp = activated(&repo, &req).await;
    let state = repo
        .begin_recovery(&req.spec.session_id, &cp.fence, 3)
        .await
        .unwrap();
    assert_eq!(state.takeovers, 0);
    for i in 1..=3 {
        let fence = repo
            .transfer_shard(cp.fence.clone(), node(&format!("next{i}")))
            .await
            .unwrap();
        let state = repo
            .begin_recovery(&req.spec.session_id, &fence, 3)
            .await
            .unwrap();
        assert_eq!(state.takeovers, i);
        cp = repo
            .activate(req.spec.clone(), cp.state.clone(), fence)
            .await
            .unwrap();
    }
    let restarted = RuntimeStore::new(atomic);
    assert!(
        restarted
            .recovery_status(&req.spec.session_id)
            .await
            .unwrap()
            .unwrap()
            .quarantined
    );
    let duplicate = restarted
        .begin_recovery(&req.spec.session_id, &cp.fence, 3)
        .await
        .unwrap();
    assert_eq!(duplicate.takeovers, 3);
    // A no-op checkpoint is not progress and cannot erase quarantine.
    cp = restarted
        .commit(SessionCommit {
            session_id: req.spec.session_id.clone(),
            fence: cp.fence,
            expected_revision: cp.revision,
            consumed_input: 0,
            transition: Transition {
                next_state: cp.state,
                effects: vec![],
                events: vec![],
                timer_changes: vec![],
            },
        })
        .await
        .unwrap();
    assert!(
        restarted
            .recovery_status(&req.spec.session_id)
            .await
            .unwrap()
            .unwrap()
            .quarantined
    );
    let t = DirectA2aRunner.reduce(&cp.state, &req.input).unwrap();
    restarted
        .commit(SessionCommit {
            session_id: req.spec.session_id.clone(),
            fence: cp.fence,
            expected_revision: cp.revision,
            consumed_input: 1,
            transition: t,
        })
        .await
        .unwrap();
    assert_eq!(
        restarted
            .recovery_status(&req.spec.session_id)
            .await
            .unwrap()
            .unwrap()
            .takeovers,
        0
    );
}
