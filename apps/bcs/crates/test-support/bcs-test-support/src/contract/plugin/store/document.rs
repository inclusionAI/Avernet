//! Mount this suite for each DocumentCas transport with a fresh persistent prefix.
use super::applied;
use bcs_store_api::{document::DocumentStorePlugin, *};
use futures::future::join_all;
use serde_json::json;

pub async fn document_store_contract_tests<P: DocumentStorePlugin + ?Sized>(p: &P, key: &str) {
    assert!(p.supports(StoreCapability::DocumentCas));
    assert!(p.read_document(key).await.unwrap().is_none());
    assert_eq!(
        p.replace_document(key, Version(1), &json!({"unexpected": true}))
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    assert!(p.read_document(key).await.unwrap().is_none());
    let v = applied(
        p.create_document(key, &json!({"old":1,"nested":{"a":1,"b":2}}))
            .await
            .unwrap(),
    );
    let created = p.read_document(key).await.unwrap().unwrap();
    assert_eq!(
        p.create_document(key, &json!("overwrite")).await.unwrap(),
        WriteOutcome::ConditionNotMet
    );
    assert_eq!(p.read_document(key).await.unwrap().unwrap(), created);
    let value = json!({"nested":{"a":null},"_store":{"version":"business"},"rev":null});
    let next = applied(p.replace_document(key, v, &value).await.unwrap());
    assert!(next > v);
    let replaced = Versioned {
        value,
        version: next,
    };
    assert_eq!(p.read_document(key).await.unwrap().unwrap(), replaced);
    assert_eq!(
        p.replace_document(key, v, &json!(0)).await.unwrap(),
        WriteOutcome::ConditionNotMet
    );
    assert_eq!(p.read_document(key).await.unwrap().unwrap(), replaced);
    let null = applied(p.replace_document(key, next, &json!(null)).await.unwrap());
    assert_eq!(
        p.read_document(key).await.unwrap().unwrap(),
        Versioned {
            value: json!(null),
            version: null
        }
    );
    document_concurrency_contract_tests(p, &format!("{key}:concurrent")).await;
}

/// All writers start with the same observed condition. Providers must compare
/// and mutate atomically, including when each request uses a different connection.
pub async fn document_concurrency_contract_tests<P: DocumentStorePlugin + ?Sized>(
    p: &P,
    key: &str,
) {
    let created = join_all((0..8).map(|writer| async move {
        let value = json!({"writer": writer});
        (value.clone(), p.create_document(key, &value).await.unwrap())
    }))
    .await;
    let winners = created
        .iter()
        .filter_map(|(value, outcome)| match outcome {
            WriteOutcome::Applied { version } => Some(Versioned {
                value: value.clone(),
                version: *version,
            }),
            WriteOutcome::ConditionNotMet => None,
        })
        .collect::<Vec<_>>();
    assert_eq!(winners.len(), 1, "create-if-absent has exactly one winner");
    let initial = p.read_document(key).await.unwrap().unwrap();
    assert_eq!(initial, winners[0]);
    let results = join_all((0..8).map(|writer| async move {
        let value = json!({"replace": writer});
        (
            value.clone(),
            p.replace_document(key, initial.version, &value)
                .await
                .unwrap(),
        )
    }))
    .await;
    let winners = results
        .iter()
        .filter_map(|(value, outcome)| match outcome {
            WriteOutcome::Applied { version } => Some(Versioned {
                value: value.clone(),
                version: *version,
            }),
            WriteOutcome::ConditionNotMet => None,
        })
        .collect::<Vec<_>>();
    assert_eq!(
        winners.len(),
        1,
        "same-version replacements have exactly one winner"
    );
    assert!(winners[0].version > initial.version);
    assert_eq!(p.read_document(key).await.unwrap().unwrap(), winners[0]);
}

/// The provider fixture supplies an existing document at its final usable
/// version. Seeding is outside the production API and may use fault injection.
/// Persistent DocumentCas has no TTL surface; physical TTL checks belong to
/// provider-specific fixtures, not this portable contract.
pub async fn document_version_exhaustion_contract_tests<P: DocumentStorePlugin + ?Sized>(
    p: &P,
    key: &str,
) {
    let before = p
        .read_document(key)
        .await
        .unwrap()
        .expect("seeded final version");
    assert_eq!(
        p.replace_document(key, before.version, &json!({"overwrite":true}))
            .await,
        Err(StoreError::VersionExhausted)
    );
    assert_eq!(p.read_document(key).await.unwrap().unwrap(), before);
}
