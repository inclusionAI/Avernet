use std::sync::Arc;

use bcs_app_group_context::GroupContextApplicationServiceImpl;
use bcs_domain::{GroupContextOwners, GroupContextPolicy, ScopeLevel};
use bcs_group_context_store::{GroupContextRepo, MemoryGroupContextRepo, NewGroupContextEntry};
use bcs_service_api::GroupContextApplicationService;

#[tokio::test]
async fn inheritance_requires_every_requested_name_to_be_current_and_readable() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let service = GroupContextApplicationServiceImpl::new(repo.clone());
    let now = chrono::Utc::now();
    for (name, scope_level, visible_to) in [
        (
            "readable",
            ScopeLevel::Session,
            GroupContextPolicy::users(["reader"]),
        ),
        (
            "restricted",
            ScopeLevel::Session,
            GroupContextPolicy::users(["other"]),
        ),
        (
            "round-only",
            ScopeLevel::Round,
            GroupContextPolicy::users(["reader"]),
        ),
    ] {
        repo.create_active(NewGroupContextEntry {
            is_slot: false,
            context_id: format!("ctx-{name}"),
            name: name.into(),
            scope_level,
            scope: "group:12345678".into(),
            content: name.into(),
            creator: "owner".into(),
            visible_to,
            collect_from: GroupContextPolicy::users(["reader"]),
            owners: GroupContextOwners::new(["owner"]),
            supersedes: None,
            change_reason: None,
            valid_from: now,
            valid_to: None,
            tx_time: now,
        })
        .await
        .unwrap();
    }
    let source = "group:12345678";
    for name in ["restricted", "round-only", "missing"] {
        let error = service
            .inheritance_sources("group", source, "reader", Some(&["readable".into(), name.into()]))
            .await
            .unwrap_err();
        assert_eq!(error.code(), "permission_denied");
    }
    for names in [vec![], vec!["readable".into(), "readable".into()]] {
        assert_eq!(
            service
                .inheritance_sources("group", source, "reader", Some(&names))
                .await
                .unwrap_err()
                .code(),
            "invalid_param"
        );
    }
    let selected = service
        .inheritance_sources("group", source, "reader", Some(&["readable".into()]))
        .await
        .unwrap();
    assert_eq!(selected.len(), 1);
    assert_eq!(selected[0].name, "readable");
    let all = service.inheritance_sources("group", source, "reader", None).await.unwrap();
    assert_eq!(all, selected);
    let empty = service.inheritance_sources("group", source, "nobody", None).await.unwrap();
    assert!(empty.is_empty());
}

#[tokio::test]
async fn inheritance_rejects_more_than_500_entries() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let service = GroupContextApplicationServiceImpl::new(repo.clone());
    let source = "group:12345678";
    let now = chrono::Utc::now();
    let mut names = Vec::new();
    for index in 0..=501 {
        let name = format!("context-{index}");
        repo.create_active(NewGroupContextEntry {
            is_slot: false,
            context_id: name.clone(),
            name: name.clone(),
            scope_level: ScopeLevel::Session,
            scope: source.into(),
            content: String::new(),
            creator: "owner".into(),
            visible_to: GroupContextPolicy::users([if index == 0 { "other" } else { "reader" }]),
            collect_from: GroupContextPolicy::users(["owner"]),
            owners: GroupContextOwners::new(["owner"]),
            supersedes: None,
            change_reason: None,
            valid_from: now,
            valid_to: None,
            tx_time: now,
        }).await.unwrap();
        if index == 0 {
            continue;
        }
        names.push(name);
        if index >= 500 {
            for selection in [None, Some(names.as_slice())] {
                let result = service.inheritance_sources("group", source, "reader", selection).await;
                if index == 500 {
                    assert_eq!(result.unwrap().len(), 500);
                } else {
                    assert_eq!(result.unwrap_err().code(), "payload_too_large");
                }
            }
        }
    }
}
