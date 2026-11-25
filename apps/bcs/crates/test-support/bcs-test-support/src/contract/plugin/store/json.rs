use super::applied;
use bcs_store_api::{json::*, *};
use serde_json::json;

pub async fn json_store_plugin_contract_tests<P: JsonStorePlugin + ?Sized>(p: &P, prefix: &str) {
    assert!(p.supports(StoreCapability::AtomicJson));
    let key = format!("{prefix}:json");
    let doc =
        json!({"owner":{"epoch":1},"seq":2,"state":{"keep":true},"nullable":null,"a/b":{"~key":7}});
    let v = applied(
        p.mutate_json(
            &key,
            WriteCondition::Absent,
            JsonMutation::Replace(doc.clone()),
            Expiry::Persistent,
        )
        .await
        .unwrap(),
    );
    let projection = p
        .get_paths(
            &key,
            &[
                "/owner/epoch".into(),
                "/missing".into(),
                "/nullable".into(),
                "/a~1b/~0key".into(),
            ],
        )
        .await
        .unwrap()
        .unwrap();
    assert_eq!(projection.version, v);
    assert_eq!(
        projection.values,
        vec![Some(json!(1)), None, Some(json!(null)), Some(json!(7))]
    );
    let seal = applied(
        p.mutate_json(
            &key,
            WriteCondition::Version(v),
            JsonMutation::Patch(vec![JsonEdit::Set {
                pointer: "/owner/epoch".into(),
                value: json!(2),
            }]),
            Expiry::Preserve,
        )
        .await
        .unwrap(),
    );
    assert!(seal > v);
    assert_eq!(
        p.mutate_json(
            &key,
            WriteCondition::Version(v),
            JsonMutation::Replace(doc),
            Expiry::Preserve
        )
        .await
        .unwrap(),
        WriteOutcome::ConditionNotMet,
        "path write fences whole-document overwrite"
    );
    let before = p.get_json(&key).await.unwrap().unwrap();
    assert_eq!(before.value["state"]["keep"], true);
    assert!(matches!(
        p.mutate_json(
            &key,
            WriteCondition::Version(seal),
            JsonMutation::Patch(vec![
                JsonEdit::Set {
                    pointer: "/seq".into(),
                    value: json!(999)
                },
                JsonEdit::Set {
                    pointer: "/missing/child".into(),
                    value: json!(1)
                },
            ]),
            Expiry::Persistent
        )
        .await,
        Err(StoreError::InvalidInput(_))
    ));
    assert_eq!(
        p.get_json(&key).await.unwrap().unwrap(),
        before,
        "failed JSON patch must roll back every edit"
    );
    let supplied = Version(seal.0.checked_add(1).expect("room for next test write"));
    let newer = applied(
        p.mutate_json(
            &key,
            WriteCondition::Newer(supplied),
            JsonMutation::Patch(vec![
                JsonEdit::Set {
                    pointer: "/seq".into(),
                    value: json!(3),
                },
                JsonEdit::Remove {
                    pointer: "/nullable".into(),
                },
            ]),
            Expiry::Preserve,
        )
        .await
        .unwrap(),
    );
    assert_eq!(newer, supplied);
    assert_eq!(
        p.mutate_json(
            &key,
            WriteCondition::Newer(newer),
            JsonMutation::Delete,
            Expiry::Preserve
        )
        .await
        .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let after = p.get_json(&key).await.unwrap().unwrap();
    assert_eq!(after.value["seq"], 3);
    assert!(after.value.get("nullable").is_none());
    applied(
        p.mutate_json(
            &key,
            WriteCondition::Version(newer),
            JsonMutation::Delete,
            Expiry::Preserve,
        )
        .await
        .unwrap(),
    );
    assert_eq!(
        p.mutate_json(
            &key,
            WriteCondition::Newer(newer),
            JsonMutation::Replace(json!({})),
            Expiry::Persistent
        )
        .await
        .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let race_key = format!("{prefix}:json-race");
    let initial = applied(
        p.mutate_json(
            &race_key,
            WriteCondition::Absent,
            JsonMutation::Replace(json!({"left":0,"right":0})),
            Expiry::Persistent,
        )
        .await
        .unwrap(),
    );
    let (left, right) = tokio::join!(
        p.mutate_json(
            &race_key,
            WriteCondition::Version(initial),
            JsonMutation::Patch(vec![JsonEdit::Set {
                pointer: "/left".into(),
                value: json!(1)
            }]),
            Expiry::Preserve
        ),
        p.mutate_json(
            &race_key,
            WriteCondition::Version(initial),
            JsonMutation::Patch(vec![JsonEdit::Set {
                pointer: "/right".into(),
                value: json!(1)
            }]),
            Expiry::Preserve
        )
    );
    let left = left.unwrap();
    let right = right.unwrap();
    assert!(
        matches!(
            (&left, &right),
            (WriteOutcome::Applied { .. }, WriteOutcome::ConditionNotMet)
                | (WriteOutcome::ConditionNotMet, WriteOutcome::Applied { .. })
        ),
        "JSON CAS must have one winner"
    );
    let expected = if matches!(left, WriteOutcome::Applied { .. }) {
        json!({"left":1,"right":0})
    } else {
        json!({"left":0,"right":1})
    };
    assert_eq!(
        p.get_json(&race_key).await.unwrap().unwrap().value,
        expected
    );
}

/// Worker updates and Connector claims share one revision on a persistent object.
pub async fn persistent_json_merge_contract_tests(p: &dyn JsonStorePlugin, key: &str) {
    assert!(p.supports(StoreCapability::PersistentJsonMerge));
    let patch = |value: serde_json::Value| JsonMutation::Merge(value.as_object().unwrap().clone());
    let merge = |condition, value| p.mutate_json(key, condition, patch(value), Expiry::Preserve);
    let claim = WriteCondition::Newer(Version(1_000_000_000));
    assert_eq!(
        merge(claim, json!({"holder":"B"})).await.unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let original =
        json!({"holder":"A","execution":{"kind":"rejected","code":"busy"},"control":{"inputs":[]}});
    assert_eq!(
        applied(
            p.mutate_json(
                key,
                WriteCondition::Absent,
                JsonMutation::Replace(original),
                Expiry::Preserve
            )
            .await
            .unwrap()
        ),
        Version(1)
    );
    assert_eq!(
        applied(
            merge(
                WriteCondition::Version(Version(1)),
                json!({"control":{"inputs":[null,{"id":1}]}})
            )
            .await
            .unwrap()
        ),
        Version(2)
    );
    assert_eq!(
        applied(
            merge(
                claim,
                json!({"holder":"B","execution":{"kind":"accepted","code":null}})
            )
            .await
            .unwrap()
        ),
        Version(1_000_000_000)
    );
    for stale in [
        claim,
        WriteCondition::Version(Version(2)),
        WriteCondition::Newer(Version(3)),
    ] {
        assert_eq!(
            merge(stale, json!({"holder":"A"})).await.unwrap(),
            WriteOutcome::ConditionNotMet
        );
    }
    let doc = p.get_json(key).await.unwrap().unwrap();
    assert_eq!(doc.version, Version(1_000_000_000));
    assert_eq!(
        doc.value,
        json!({"holder":"B","execution":{"kind":"accepted"},"control":{"inputs":[null,{"id":1}]}})
    );
}
