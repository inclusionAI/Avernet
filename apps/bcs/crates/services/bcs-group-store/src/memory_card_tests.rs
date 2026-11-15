use super::*;
use serde_json::json;

#[tokio::test]
async fn memory_templates_follow_all_name_and_delete_paths() {
    let repo = MemoryGroupRepo::new();
    for (id, strategy) in [("mw", GroupStrategy::ManagerWorker), ("sm", GroupStrategy::StateMachine)] {
        let mut group = GroupBuilder::new("manager").id(id).label("初始群名").build();
        group.group_strategy = strategy;
        repo.upsert(group.clone()).await.unwrap();
        {
            let mut cards = repo.card_templates.write().await;
            let card = cards.get_mut(id).unwrap();
            assert_eq!(card["name"], "初始群名");
            assert!(card.get("version").is_none());
            card["retained"] = json!({"unknown": [1, 2]});
        }
        repo.update_label(id, Some("改名".into())).await.unwrap();
        assert_eq!(repo.card_templates.read().await[id]["skills"][0]["name"], "改名");
        repo.patch_mutable_fields(id, GroupMutableFieldsPatch { label: Some("patch".into()), ..Default::default() }).await.unwrap();
        assert_eq!(repo.card_templates.read().await[id]["name"], "patch");
        repo.commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: id.into(), expected_version: group.version, mutated_at_ms: 100,
            event: None, mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch { label: Some("event".into()), ..Default::default() }),
        }).await.unwrap();
        assert_eq!(repo.card_templates.read().await[id]["name"], "event");
        let failed = repo.commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: id.into(), expected_version: group.version, mutated_at_ms: 101,
            event: None, mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch { label: Some("stale".into()), ..Default::default() }),
        }).await;
        assert!(matches!(failed, Err(ServiceError::Conflict(_))));
        repo.patch_mutable_fields(id, GroupMutableFieldsPatch { context: Some("动态配置".into()), ..Default::default() }).await.unwrap();
        assert_eq!(repo.card_templates.read().await[id]["description"], "动态配置");
        assert_eq!(repo.card_templates.read().await[id]["skills"][0]["description"], "动态配置");
        repo.patch_mutable_fields(id, GroupMutableFieldsPatch {
            label: Some("新名称".into()), context: Some("新目标".into()), ..Default::default()
        }).await.unwrap();
        assert_eq!(repo.card_templates.read().await[id]["name"], "新名称");
        assert_eq!(repo.card_templates.read().await[id]["description"], "新目标");
        repo.commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: id.into(), expected_version: group.version + 1, mutated_at_ms: 102,
            event: None, mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch { context: Some(String::new()), ..Default::default() }),
        }).await.unwrap();
        assert_eq!(repo.card_templates.read().await[id]["description"], "");
        assert_eq!(repo.card_templates.read().await[id]["skills"][0]["description"], "");
        group.label = None;
        repo.upsert(group).await.unwrap();
        assert_eq!(repo.card_templates.read().await[id]["name"], id);
        assert_eq!(repo.card_templates.read().await[id]["retained"], json!({"unknown": [1, 2]}));
    }
    repo.delete("mw").await.unwrap();
    assert!(!repo.card_templates.read().await.contains_key("mw"));
    let group = repo.get("sm").await.unwrap();
    repo.commit_eventful_mutation(CommitGroupEventfulMutation {
        group_id: "sm".into(), expected_version: group.version, mutated_at_ms: 102,
        event: None, mutation: GroupEventfulMutation::Delete,
    }).await.unwrap();
    assert!(repo.card_templates.read().await.is_empty());
    let chat = GroupBuilder::new("driver").id("chat").build();
    repo.upsert(chat.clone()).await.unwrap();
    let mut dm = chat;
    dm.id = "dm".into();
    dm.group_kind = GroupKind::Dm;
    dm.group_strategy = GroupStrategy::ManagerWorker;
    repo.upsert(dm).await.unwrap();
    assert!(repo.card_templates.read().await.is_empty());
}
