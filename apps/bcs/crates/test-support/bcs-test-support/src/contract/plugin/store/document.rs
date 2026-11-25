//! Mount this suite for each DocumentCas transport with a fresh persistent prefix.
use super::applied;
use bcs_store_api::{document::DocumentStorePlugin, json::JsonEdit, *};
use futures::future::join_all;
use serde_json::json;

pub async fn document_store_contract_tests<P: DocumentStorePlugin + ?Sized>(p: &P, key: &str) {
    assert!(p.supports(StoreCapability::DocumentCas));
    assert!(p.read_document(key).await.unwrap().is_none());
    assert!(p.read_document_paths(key, &["".into()]).await.unwrap().is_none());
    assert_eq!(p.patch_document(key, Version(1), &[
        JsonEdit::Set { pointer: "/child".into(), value: json!(true) },
    ]).await.unwrap(), WriteOutcome::ConditionNotMet);
    assert!(matches!(p.read_document_paths(key, &["/bad~2".into()]).await,
        Err(StoreError::InvalidInput(_))));
    assert!(matches!(p.patch_document(key, Version(1), &[
        JsonEdit::Remove { pointer: "/bad~2".into() },
    ]).await, Err(StoreError::InvalidInput(_))));
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
    assert_eq!(v, Version(1), "first persistent document version is 1");
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
    document_paths_contract_tests(p, key).await;
    document_concurrency_contract_tests(p, &format!("{key}:concurrent")).await;
}

/// Uses an already-created document, retaining the harness's two-key footprint.
/// Projection, patch and replace share one whole-document version and namespace.
pub async fn document_paths_contract_tests<P: DocumentStorePlugin + ?Sized>(p: &P, key: &str) {
    let before = p.read_document(key).await.unwrap().unwrap();
    let value = json!({"a/b":{"~":null},"arr":[1,2,3],"keep":true});
    let version = applied(p.replace_document(key, before.version, &value).await.unwrap());
    let projection = p.read_document_paths(key, &[
        "".into(), "/a~1b/~0".into(), "/absent".into(), "/arr/2".into(),
    ]).await.unwrap().unwrap();
    assert_eq!(projection.version, version);
    assert_eq!(projection.values, vec![Some(value), Some(json!(null)), None, Some(json!(3))]);
    let next = applied(p.patch_document(key, version, &[
        JsonEdit::Remove { pointer: "/arr/0".into() },
        JsonEdit::Set { pointer: "/arr/1".into(), value: json!(4) },
        JsonEdit::Remove { pointer: "/a~1b/~0".into() },
        JsonEdit::Set { pointer: "/new".into(), value: json!({"leaf":null}) },
        JsonEdit::Set { pointer: "/new/leaf".into(), value: json!(7) },
    ]).await.unwrap());
    assert!(next > version);
    let expected = Versioned {
        version: next,
        value: json!({"a/b":{},"arr":[2,4],"keep":true,"new":{"leaf":7}}),
    };
    assert_eq!(p.read_document(key).await.unwrap().unwrap(), expected);
    let patched_no_op = applied(p.patch_document(key, next, &[
        JsonEdit::Set { pointer: "/keep".into(), value: json!(true) },
    ]).await.unwrap());
    assert!(patched_no_op > next, "identical patch advances the whole-document version");
    let no_op = applied(p.replace_document(key, patched_no_op, &expected.value).await.unwrap());
    assert!(no_op > patched_no_op, "identical replacement advances the whole-document version");
    let before = p.read_document(key).await.unwrap().unwrap();
    for pointer in ["/missing/child", "/arr/-", "/arr/01", "/arr/+1", "/arr/2", "/keep/child"] {
        assert!(matches!(p.patch_document(key, no_op, &[
            JsonEdit::Set { pointer: "/keep".into(), value: json!(false) },
            JsonEdit::Set { pointer: pointer.into(), value: json!(9) },
        ]).await, Err(StoreError::InvalidInput(_))));
        assert_eq!(p.read_document(key).await.unwrap().unwrap(), before,
            "failed ordered patch must not persist its first edit");
    }
    assert!(matches!(p.patch_document(key, no_op, &[
        JsonEdit::Remove { pointer: "/absent".into() },
    ]).await, Err(StoreError::InvalidInput(_))));
    for pointer in ["not-a-pointer", "/bad~", "/bad~2"] {
        assert!(matches!(p.read_document_paths(key, &[pointer.into()]).await,
            Err(StoreError::InvalidInput(_))));
        assert!(matches!(p.patch_document(key, version, &[
            JsonEdit::Remove { pointer: pointer.into() },
        ]).await, Err(StoreError::InvalidInput(_))));
    }
    for edits in [vec![], vec![JsonEdit::Remove { pointer: "".into() }]] {
        assert!(matches!(p.patch_document(key, no_op, &edits).await,
            Err(StoreError::InvalidInput(_))));
    }
    assert_eq!(p.patch_document(key, version, &[
        JsonEdit::Set { pointer: "/missing/child".into(), value: json!(9) },
    ]).await.unwrap(), WriteOutcome::ConditionNotMet, "condition precedes document-dependent edits");
    assert_eq!(p.replace_document(key, version, &json!(0)).await.unwrap(), WriteOutcome::ConditionNotMet);
    assert_eq!(p.read_document(key).await.unwrap().unwrap(), before);
    let scalar = applied(p.replace_document(key, no_op, &json!(null)).await.unwrap());
    assert!(matches!(p.patch_document(key, scalar, &[
        JsonEdit::Set { pointer: "/child".into(), value: json!(9) },
    ]).await, Err(StoreError::InvalidInput(_))));
    assert_eq!(p.read_document(key).await.unwrap().unwrap().value, json!(null));
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
    assert_eq!(initial.version, Version(1));
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
    document_patch_concurrency_contract_tests(p, key).await;
}

/// Call on an existing object document. All patches must use the same observed
/// version; a loser cannot recompute and retry with a newer version internally.
pub async fn document_patch_concurrency_contract_tests<P: DocumentStorePlugin + ?Sized>(p: &P, key: &str) {
    let initial = p.read_document(key).await.unwrap().unwrap();
    assert!(initial.value.is_object());
    let results = join_all((0..8).map(|writer| async move {
        let result = p.patch_document(key, initial.version, &[
            JsonEdit::Set { pointer: "/patch_writer".into(), value: json!(writer) },
        ]).await.unwrap();
        (writer, result)
    })).await;
    let winners = results.iter().filter_map(|(writer, result)| match result {
        WriteOutcome::Applied { version } => Some((*writer, *version)),
        WriteOutcome::ConditionNotMet => None,
    }).collect::<Vec<_>>();
    assert_eq!(winners.len(), 1, "same-version patches have exactly one winner");
    let mut expected = initial.value;
    expected["patch_writer"] = json!(winners[0].0);
    assert!(winners[0].1 > initial.version);
    assert_eq!(p.read_document(key).await.unwrap().unwrap(), Versioned { value: expected, version: winners[0].1 });
}

/// The provider fixture supplies an existing object document at its final usable
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
    assert_eq!(p.patch_document(key, before.version, &[
        JsonEdit::Set { pointer: "/overwrite".into(), value: json!(true) },
    ]).await, Err(StoreError::VersionExhausted));
    assert_eq!(p.read_document(key).await.unwrap().unwrap(), before);
}

/// Use a fresh key. Each arm call makes the next mutation commit and lose its
/// response, leaving reads unaffected. Mount separately for providers with fault
/// injection; ordinary conformance does not claim transport uncertainty coverage.
pub async fn document_unknown_outcome_contract_tests<P, F>(p: &P, key: &str, mut arm: F)
where
    P: DocumentStorePlugin + ?Sized,
    F: FnMut(),
{
    arm();
    assert!(matches!(p.create_document(key, &json!({"operation":"create"})).await,
        Err(StoreError::OutcomeUnknown(_))));
    let created = p.read_document(key).await.unwrap().unwrap();
    assert_eq!(created.value, json!({"operation":"create"}));
    assert_eq!(created.version, Version(1));
    assert_eq!(p.create_document(key, &json!({"operation":"different"})).await.unwrap(),
        WriteOutcome::ConditionNotMet);
    assert_eq!(p.read_document(key).await.unwrap().unwrap(), created);
    arm();
    assert!(matches!(p.replace_document(key, created.version, &json!({"operation":"replace"})).await,
        Err(StoreError::OutcomeUnknown(_))));
    let replaced = p.read_document(key).await.unwrap().unwrap();
    assert_eq!(replaced.value, json!({"operation":"replace"}));
    assert!(replaced.version > created.version);
    assert_eq!(p.replace_document(key, created.version, &json!({"operation":"different"})).await.unwrap(),
        WriteOutcome::ConditionNotMet);
    arm();
    let edits = [JsonEdit::Set { pointer: "/operation".into(), value: json!("patch") }];
    assert!(matches!(p.patch_document(key, replaced.version, &edits).await,
        Err(StoreError::OutcomeUnknown(_))));
    let patched = p.read_document(key).await.unwrap().unwrap();
    assert_eq!(patched.value, json!({"operation":"patch"}));
    assert!(patched.version > replaced.version);
    assert_eq!(p.patch_document(key, replaced.version, &edits).await.unwrap(),
        WriteOutcome::ConditionNotMet);
    assert_eq!(p.read_document(key).await.unwrap().unwrap(), patched);
}
