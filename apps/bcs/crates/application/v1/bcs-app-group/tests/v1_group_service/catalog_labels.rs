use super::*;
use bcs_service_api::types::catalog_labels::{CatalogLabelPolicy,CatalogLabelRule};
fn configure(mut fixture:Fixture)->Fixture {
    fixture.service=fixture.service.with_catalog_label_policy(Arc::new(CatalogLabelPolicy::new([("stage".into(),CatalogLabelRule {
        allowed_values:vec!["trial".into(),"running".into()],allow_multiple:false,
    })].into()).unwrap()));fixture
}
fn detail_labels(detail:GroupDetail)->bcs_service_api::types::catalog_labels::CatalogLabels {
    match detail {GroupDetail::Collaboration(detail)=>detail.catalog_labels,GroupDetail::DirectMessage(detail)=>detail.catalog_labels}
}
fn input(value:serde_json::Value)->Option<bcs_service_api::types::catalog_labels::CatalogLabelsInput> {Some(serde_json::from_value(value).unwrap())}
#[tokio::test]
async fn create_patch_omission_replacement_and_public_transition(){
    let fixture=configure(Fixture::new().await);fixture.add_public_bot("driver").await;
    let caller=authenticated_bot_principal("driver","owner");
    let mut command=collaboration_create_command();command.caller=caller.clone();
    let CreateGroupSpec::Collaboration(create)=&mut command.group else {panic!()};
    create.collaboration=CollaborationConfiguration::ManagerWorker(Default::default());
    create.catalog_labels=input(serde_json::json!({"stage":["trial","trial"]}));
    let created=fixture.service.create(command).await.unwrap();let id=detail_group_id(&created).to_string();
    assert_eq!(detail_labels(created).as_map()["stage"],["trial"]);
    let changed=fixture.service.update(UpdateGroup {caller:caller.clone(),group_id:id.clone(),patch:GroupPatch {
        name:Some("renamed".into()),context:Some("new goal".into()),visibility:Some(GroupVisibility::Public),..Default::default()
    }}).await.unwrap();assert_eq!(detail_labels(changed).as_map()["stage"],["trial"]);
    let invalid=fixture.service.update(UpdateGroup {caller:caller.clone(),group_id:id.clone(),patch:GroupPatch {
        name:Some("must not save".into()),catalog_labels:input(serde_json::json!({"unknown":null})),..Default::default()
    }}).await;assert!(invalid.is_err());assert_eq!(fixture.groups.try_get(&id).await.unwrap().unwrap().label.as_deref(),Some("renamed"));
    for labels in [serde_json::json!({"stage":["running"]}),serde_json::json!({}),serde_json::json!({"stage":null}),serde_json::json!({"stage":[]})] {
        let updated=fixture.service.update(UpdateGroup {caller:caller.clone(),group_id:id.clone(),patch:GroupPatch {catalog_labels:input(labels.clone()),..Default::default()}}).await.unwrap();
        assert_eq!(detail_labels(updated).is_empty(),labels!=serde_json::json!({"stage":["running"]}));
    }
    assert!(detail_labels(fixture.service.get(GetGroup {caller,group_id:id}).await.unwrap()).is_empty());
}
#[tokio::test]
async fn chat_rejects_nonempty_labels_and_invalid_combined_creation_creates_nothing(){
    let fixture=configure(Fixture::new().await);fixture.add_public_bot("driver").await;
    for labels in [serde_json::json!({"stage":["trial"]}),serde_json::json!({"unknown":null}),serde_json::json!({"stage":["trial","running"]})] {
        let mut command=collaboration_create_command();command.caller=authenticated_bot_principal("driver","owner");
        let CreateGroupSpec::Collaboration(create)=&mut command.group else {panic!()};create.catalog_labels=input(labels);
        assert!(fixture.service.create(command).await.is_err());
    }
    assert!(fixture.groups.list().await.is_empty());
}
