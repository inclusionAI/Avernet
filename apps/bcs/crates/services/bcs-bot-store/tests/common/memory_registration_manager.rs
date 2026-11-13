//! Registration-to-manager authority conformance over the shared Memory store.
use super::*;

#[tokio::test]
async fn memory_registration_creates_the_owner_actor_row_and_the_owner_can_manage() {
    // Ruling T4-1 memory mirror: one fresh
    // registration per owner ENSURES the Human ACTOR
    // row (the authority registry on the shared bots
    // map) in the SAME registration, so the Task-2
    // human-existence guard accepts that owner as a
    // manager target immediately after. No OAuth
    // identity row is required.
    let repo = owned_repo();
    let created = repo
        .create_registration_if_absent_with_ownership(
            "bot_mem_pair".to_string(),
            trusted_capabilities(),
            "owner-a",
            "token-a",
            owned_mutation("m-a", "owner-a"),
        )
        .await
        .expect("first owner commits");
    assert!(created);
    let created = repo
        .create_registration_if_absent_with_ownership(
            "bot_mem_pair2".to_string(),
            trusted_capabilities(),
            "owner-b",
            "token-b",
            owned_mutation("m-b", "owner-b"),
        )
        .await
        .expect("second owner commits");
    assert!(created);
    // Both Human ACTOR rows are present on the shared
    // registry (and nothing else was needed).
    for staff in ["owner-a", "owner-b"] {
        let actor = repo
            .get(&format!("human_{staff}"))
            .await
            .unwrap_or_else(|| panic!("human_{staff} actor row must exist"));
        assert!(
            matches!(actor.actor_kind, bcs_service_api::ActorKind::Human),
            "human_{staff} must be a Human actor row"
        );
    }
    // The FIRST owner (registered Human) grants the
    // second as a manager via the strict Task-2
    // command: the fresh registrations pass its
    // human-existence guards unchanged.
    let change = repo
        .edit_manager(ManagerEdit {
            bot_id: "bot_mem_pair".to_string(),
            user_id: "human_owner-b".to_string(),
            action: ManagerEditAction::Grant,
            mutation: AuthorityMutation {
                mutation_id: "m-grant".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "owner-a".to_string(),
                },
            },
        })
        .await
        .expect("manager grant by the registered owner");
    assert_eq!(change.added, 1);
    assert_eq!(change.total, 1);
}
