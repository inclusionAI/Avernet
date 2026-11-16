//! Management-only composition for sibling runtimes. No server, migrations,
//! leader election, sockets, refresh loops or legacy session executors are started.
use super::*;
use bcs_service_api::port::repo::{a2a_bot::A2aBotRepoPort,bot_provider::BotProviderRepoPort};
pub struct ManagementResources {
    pub bots:Arc<dyn BotProviderRepoPort>,
    pub providers:Arc<dyn ProviderRepoPort>,
    pub credentials:Arc<dyn ProviderCredentialRepoPort>,
    pub cards:Arc<dyn A2aBotRepoPort>,
    pub groups:Arc<dyn GroupRepoPort>,
    pub bindings:Arc<dyn bcs_service_api::GroupRuntimeBindingRepoPort>,
    pub definitions:Arc<dyn bcs_service_api::StateMachineDefinitionRepoPort>,
    pub judge:Arc<dyn JudgeEvaluatorPort>,
    pub judge_available:bool,
}
/// Deployment must match this environment to `resolve_env()`: legacy Provider
/// repositories still use the process environment. This is environment lookup,
/// not tenant authorization. No database writes occur during construction.
pub fn management_resources(config:&BcsConfig,db:Arc<dyn bcs_db_api::DbPlugin>,kind:DbPluginKind,environment:String)->crate::Result<ManagementResources> {
    if !matches!(kind,DbPluginKind::LocalSqlite|DbPluginKind::Mysql) {return Err(crate::BcsError::InvalidConfig("management SQL dialect unavailable".into()))}
    let flavor=db_sql_flavor(&kind);
    let provider=db_provider_repos(db.clone(),&kind,config.provider_http.downlink_detection_source);
    let cards=Arc::new(bcs_bot_store::a2a::DbA2aBotStore::new(db.clone(),environment.clone(),flavor));
    let (groups,collaboration)=match kind {
        DbPluginKind::LocalSqlite=>(Arc::new(MySqlGroupStore::sqlite(db.clone(),environment.clone())),Arc::new(MySqlCollaborationStore::sqlite(db,environment))),
        _=>(Arc::new(MySqlGroupStore::new(db.clone(),environment.clone())),Arc::new(MySqlCollaborationStore::new(db,environment))),
    };
    let judge=create_judge_evaluator(config,&BcsServerExtensions::default())?;
    Ok(ManagementResources{bots:provider.bot_providers,providers:provider.provider_repo,credentials:provider.provider_credentials,
        cards,groups,bindings:collaboration.clone(),definitions:collaboration,judge,
        judge_available:!matches!(config.llm.provider_type,LlmProviderType::None)})
}
