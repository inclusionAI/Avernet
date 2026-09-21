//! Opt-in Bot registration contract for repositories supporting strict creation.
//!
//! Kept separate from the legacy BotRepo harness: its fail-closed default does
//! not require unrelated existing test doubles to implement atomic creation.

use bcs_service_api::{BotCapabilities, BotRepoPort};

pub async fn atomic_registration_creation_contract<T: BotRepoPort + ?Sized>(repo: &T) {
    assert!(
        repo.try_load_token("atomic-contract")
            .await
            .unwrap()
            .is_none()
    );
    let original = BotCapabilities {
        name: Some("atomic original".into()),
        ..Default::default()
    };
    assert!(
        repo.create_registration_if_absent(
            "atomic-contract".into(),
            original.clone(),
            "owner-a",
            "atomic-original-token",
        )
        .await
        .unwrap()
    );
    assert!(
        !repo
            .create_registration_if_absent(
                "atomic-contract".into(),
                BotCapabilities::default(),
                "owner-b",
                "atomic-stale-token",
            )
            .await
            .unwrap()
    );
    let stored = repo.try_get("atomic-contract").await.unwrap().unwrap();
    assert_eq!(stored.capabilities.name, original.name);
    assert_eq!(stored.created_by.as_deref(), Some("owner-a"));
    assert!(repo.load_token("atomic-contract").await.as_deref() == Some("atomic-original-token"));
    assert!(
        repo.try_load_token("atomic-contract")
            .await
            .unwrap()
            .as_deref()
            == Some("atomic-original-token")
    );
    repo.save_token("atomic-contract", "atomic-rotated-token")
        .await
        .unwrap();
    assert!(
        !repo
            .create_registration_if_absent(
                "atomic-contract".into(),
                original.clone(),
                "owner-a",
                "atomic-original-token",
            )
            .await
            .unwrap()
    );
    assert!(
        repo.try_load_token("atomic-contract")
            .await
            .unwrap()
            .as_deref()
            == Some("atomic-rotated-token")
    );
    assert!(repo.soft_delete("atomic-contract").await);
    assert!(
        !repo
            .create_registration_if_absent(
                "atomic-contract".into(),
                original,
                "owner-a",
                "atomic-original-token",
            )
            .await
            .unwrap()
    );
    assert!(repo.try_get("atomic-contract").await.unwrap().is_none());
    assert!(
        repo.try_load_token("atomic-contract")
            .await
            .unwrap()
            .is_none()
    );
}
