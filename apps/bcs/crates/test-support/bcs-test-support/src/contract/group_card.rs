//! Shared Group/Card snapshot and display-field contract for repository implementations.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use bcs_service_api::port::repo::GroupRepoPort;
use bcs_service_api::{Group, GroupKind, GroupMutableFieldsPatch, GroupStrategy};

pub async fn group_card_snapshot_contract(repo: &dyn GroupRepoPort) {
    assert!(repo.read_card_snapshot("card-contract-absent").await.unwrap().is_none());
    for (id, strategy) in [("card-contract-mw", GroupStrategy::ManagerWorker), ("card-contract-sm", GroupStrategy::StateMachine)] {
        let mut group = Group::new(id, "manager", vec![]);
        group.group_strategy = strategy;
        group.label = Some("协作名称".into());
        group.context = Some("目标与背景".into());
        repo.upsert(group).await.unwrap();
        let snapshot = repo.read_card_snapshot(id).await.unwrap().unwrap();
        assert_eq!(snapshot.group_id, id);
        assert_eq!(snapshot.group_kind, GroupKind::Normal);
        assert_eq!(snapshot.group_strategy, strategy);
        assert_eq!(snapshot.visibility, "private");
        assert_eq!(snapshot.record_status, "active");
        let template = snapshot.card_template.unwrap();
        assert_eq!(template["name"], "协作名称");
        assert_eq!(template["description"], "目标与背景");
        assert_eq!(template["skills"][0]["description"], "目标与背景");
        for context in ["更新目标", ""] {
            repo.patch_mutable_fields(id, GroupMutableFieldsPatch { context: Some(context.into()), ..Default::default() }).await.unwrap();
            repo.update_label(id, Some("改名不改目标".into())).await.unwrap();
            let template = repo.read_card_snapshot(id).await.unwrap().unwrap().card_template.unwrap();
            assert_eq!(template["name"], "改名不改目标");
            assert_eq!(template["description"], context);
            assert_eq!(template["skills"][0]["description"], context);
        }
        repo.update_visibility(id, "public").await.unwrap();
        assert_eq!(repo.read_card_snapshot(id).await.unwrap().unwrap().visibility, "public");
        repo.delete(id).await.unwrap();
        assert!(repo.read_card_snapshot(id).await.unwrap().is_none());
    }
    for (id, kind, strategy) in [
        ("card-contract-chat", GroupKind::Normal, GroupStrategy::Chat),
        ("card-contract-dm", GroupKind::Dm, GroupStrategy::ManagerWorker),
    ] {
        let mut group = Group::new(id, "manager", vec![]);
        group.group_kind = kind;
        group.group_strategy = strategy;
        repo.upsert(group).await.unwrap();
        let snapshot = repo.read_card_snapshot(id).await.unwrap().unwrap();
        assert_eq!(snapshot.group_kind, kind);
        assert!(snapshot.card_template.is_none());
        repo.delete(id).await.unwrap();
    }
}
