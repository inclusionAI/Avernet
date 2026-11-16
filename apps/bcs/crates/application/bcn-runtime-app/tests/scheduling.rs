mod common;
use bcn_runtime_api::{application::*, port::repo::*};
use common::*;
#[tokio::test]
async fn waiting_sessions_do_not_starve_unrelated_accepted_session() {
    let f = Fixture::new(); let g = f.gateway(); let w = f.worker("w");
    for i in 0..64 {
        let task = g.submit(caller(), request(&format!("waiting-{i}"))).await.unwrap();
        w.step(&task.session_id).await.unwrap();
    }
    let task = g.submit(caller(), request("unrelated")).await.unwrap();
    for tick in 0..4 {
        f.clock.set(1000 + tick * 500);
        w.tick().await.unwrap();
        assert!(w.status().await.unwrap().active_sessions <= 64);
    }
    let cp = f.repo.load(&task.session_id).await.unwrap();
    assert!(cp.is_some_and(|cp| cp.consumed_input > 0), "waiting sessions cannot permanently consume selection slots");
}
