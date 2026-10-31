//! The Provider candidate path must find Card-backed, offline Gateway Bots.
use std::sync::Arc;
use bcs_db_api::{DbPlugin,DbStatement};
use bcs_service_api::{OrganizationCandidateReadPort,OrganizationCandidateReadQuery,
    ProviderBotBinding,ProviderBotBindingRepoPort,ProviderBotDiscoverySelector,ProviderRecord,ProviderRepoPort};
use serde_json::json;

pub async fn verify<T:ProviderRepoPort+ProviderBotBindingRepoPort+OrganizationCandidateReadPort>(db:Arc<dyn DbPlugin>,store:&T) {
    let env=bcs_config::resolve_env_str();
    let provider="a2a-alias-provider";
    let bot="a2a-alias-bot";
    store.insert_provider(ProviderRecord {provider_id:provider.into(),slug:None,name:"Alias fixture".into(),
        config:"{}".into(),created_by:"fixture-owner".into(),owners:"[\"fixture-owner\"]".into(),
        disabled:false,created_at:1,updated_at:1}).await.unwrap();
    let card=json!({"name":"Remote Agent","description":"cardneedle",
        "skills":[{"id":"refund_lookup","name":"Refund","description":"Remote capability"}]});
    let legacy=json!({"name":"Billing Alias","summary":"legacy-only","skills":[{"name":"legacy-skill"}]});
    db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid,env,name,bot_info,connection_mode,status,agent_card) VALUES (?,?,'Billing Alias',?,'gateway','offline',?)",
        vec![bot.into(),env.clone().into(),legacy.to_string().into(),card.to_string().into()])).await.unwrap();
    store.insert_binding(ProviderBotBinding {bot_uuid:bot.into(),provider_id:provider.into(),provider_bot_ref:"remote".into(),
        webhook_url:None,disabled:false,created_at:1,updated_at:1}).await.unwrap();
    for query in ["BILLING", "cardneedle"] {
        let rows=store.list_discoverable_provider_bot_records(&ProviderBotDiscoverySelector::Query(query.into())).await.unwrap();
        assert_eq!(rows.len(),1,"{query}");assert_eq!(rows[0].bot_uuid,bot);
    }
    assert!(store.list_discoverable_provider_bot_records(&ProviderBotDiscoverySelector::Query("legacy-only".into())).await.unwrap().is_empty());
    for (skill,count) in [("refund_lookup",1),("legacy-skill",0)] {
        assert_eq!(store.list_discoverable_provider_bot_records(&ProviderBotDiscoverySelector::RequiredSkills(vec![skill.into()])).await.unwrap().len(),count);
    }
    for offset in [0,1] {
        let page=store.list_organization_candidates_page(OrganizationCandidateReadQuery {env:env.clone(),
            organization_code:"alias-org".into(),provider_ids:vec![provider.into()],q:Some("BILLING".into()),offset,limit:1}).await.unwrap().unwrap();
        assert_eq!(page.total,1);assert_eq!(page.records.len(),usize::from(offset==0));
        if offset==0 {assert_eq!(page.records[0].bot_uuid,bot);}
    }
    println!("A2A Provider: offline display alias, Card-only skills and organization pagination passed");
}
