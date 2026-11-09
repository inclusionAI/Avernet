use super::*;

#[tokio::test]
async fn add_update_and_systemset_record_version_creators() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());

    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "creator_audit".into(),
        scope_level: ScopeLevel::Session,
        content: "v1".into(),
        visible_to: policy(&[]),
        collect_from: Some(policy(&["judge_bot", "admin"])),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("add succeeds");
    let first = repo
        .get_active("creator_audit", "sess-001")
        .await
        .expect("store ok")
        .expect("first version");
    assert_eq!(first.creator, "judge_bot");

    app.update(UpdateGroupContext {
        actor_id: "admin".into(),
        request_context: ctx(),
        name: "creator_audit".into(),
        scope_level: ScopeLevel::Session,
        content: Some("v2".into()),
        change_reason: None,
        visible_to: None,
        collect_from: None,
        owners: None,
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("update succeeds");

    app.systemset(SystemSetGroupContext {
        request_context: ctx(),
        name: "creator_audit".into(),
        scope_level: ScopeLevel::Session,
        content: "v3".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["judge_bot", "admin"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("systemset succeeds");

    let active = repo
        .get_active("creator_audit", "sess-001")
        .await
        .expect("store ok")
        .expect("active version");
    assert_eq!(active.creator, "system");

    let versions = repo
        .list_versions(&active.context_id)
        .await
        .expect("versions ok");
    assert_eq!(
        versions
            .iter()
            .map(|entry| entry.creator.as_str())
            .collect::<Vec<_>>(),
        vec!["judge_bot", "admin", "system"]
    );
}
