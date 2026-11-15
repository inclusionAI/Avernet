use super::*;

#[tokio::test]
async fn interaction_resolve_uses_current_exact_session_relationships() {
    let fixture = Fixture::new()
        .with_bot("alice-bot", "Alice Bot", "public", Some("alice"))
        .with_bot("bob-bot", "Bob Bot", "public", Some("bob"))
        .with_human("human_carol", "Carol");
    let session = test_session(
        "group-under-test:session-1",
        "group-under-test",
        vec![
            Participant::bot("alice-bot", ParticipantRole::Driver),
            Participant::bot("bob-bot", ParticipantRole::Consultant),
            {
                let mut carol = Participant::human("human_carol", ParticipantRole::Observer);
                carol.mode = Some(ParticipantMode::Present);
                carol
            },
        ],
    );
    let service = fixture.service_with_limits_and_session(
        5,
        10,
        10,
        Arc::new(StaticSessionManagement::new(session)),
    );

    for actor_id in ["human_alice", "human_carol"] {
        assert!(
            service
                .can_resolve(CanResolveInteractionCommand {
                    actor_id: actor_id.to_string(),
                    bcs_session_id: "group-under-test:session-1".to_string(),
                    group_id: "group-under-test".to_string(),
                })
                .await
                .unwrap(),
            "{actor_id} should resolve through exact-session ownership or participation"
        );
    }

    assert!(
        !service
            .can_resolve(CanResolveInteractionCommand {
                actor_id: "human_dave".to_string(),
                bcs_session_id: "group-under-test:session-1".to_string(),
                group_id: "group-under-test".to_string(),
            })
            .await
            .unwrap()
    );
    assert!(
        !service
            .can_resolve(CanResolveInteractionCommand {
                actor_id: "human_alice".to_string(),
                bcs_session_id: "group-under-test:missing".to_string(),
                group_id: "group-under-test".to_string(),
            })
            .await
            .unwrap()
    );
}

#[tokio::test]
async fn interaction_resolve_rejects_absent_human_and_non_human_actor() {
    let fixture = Fixture::new().with_human("human_alice", "Alice");
    let session = test_session(
        "group-under-test:session-1",
        "group-under-test",
        vec![Participant::human("human_alice", ParticipantRole::Observer)],
    );
    let service = fixture.service_with_limits_and_session(
        5,
        10,
        10,
        Arc::new(StaticSessionManagement::new(session)),
    );

    for actor_id in ["human_alice", "bot-actor", "human_"] {
        assert!(
            !service
                .can_resolve(CanResolveInteractionCommand {
                    actor_id: actor_id.to_string(),
                    bcs_session_id: "group-under-test:session-1".to_string(),
                    group_id: "group-under-test".to_string(),
                })
                .await
                .unwrap()
        );
    }
}
