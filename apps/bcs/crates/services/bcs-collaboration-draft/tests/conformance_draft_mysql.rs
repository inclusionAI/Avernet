use bcs_collaboration_draft::CollaborationDraftCore;
use bcs_collaboration_store::draft::DbCollaborationDraftStore;
use bcs_config_api::{MysqlDbConfig, StatementProtocol, mysql::MysqlConnectionConfig};
use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
use bcs_service_api::core::collaboration_draft::*;
use std::sync::Arc;

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL and the complete committed MySQL migration chain"]
async fn conformance_mysql_draft_transactions_and_utf8() {
    let url = std::env::var("BCS_TEST_MYSQL_URL").expect("BCS_TEST_MYSQL_URL");
    let options = mysql_async::Opts::from_url(&url).unwrap();
    for protocol in [StatementProtocol::Text, StatementProtocol::Prepared] {
        let pass = options.pass().map(str::to_string);
        let mut config = MysqlDbConfig::new()
            .with_database(options.db_name().unwrap())
            .with_connection(MysqlConnectionConfig {
                connection_type: "direct".into(),
                host: Some(options.ip_or_hostname().into()),
                port: Some(options.tcp_port()),
                user: options.user().map(str::to_string),
                password: pass,
                extra: Default::default(),
            })
            .with_statement_protocol(protocol);
        config.pool_size = 4;
        config.min_pool_size = 1;
        let manager = MysqlDbManager::new(config).await.unwrap();
        let plugin = Arc::new(MysqlDbPlugin::new(manager.clone(), "bcs"));
        let repo = Arc::new(DbCollaborationDraftStore::new(plugin));
        bcs_test_support::contract::collaboration_draft::collaboration_draft_repo_port_contract_tests(repo.as_ref()).await;
        let service = CollaborationDraftCore::new(repo);
        bcs_test_support::contract::collaboration_draft::collaboration_draft_core_service_contract_tests(&service).await;
        let id = uuid::Uuid::new_v4().to_string();
        let yaml = "\0".repeat(MAX_DRAFT_BYTES);
        let content = DraftContent {
            definition_yaml: yaml.clone(),
            title: Some("中😀".repeat(60)),
        };
        service.create("owner", &id, content.clone()).await.unwrap();
        let (a, b) = tokio::join!(
            service.update("owner", &id, 1, content.clone()),
            service.update("owner", &id, 1, content),
        );
        assert_ne!(a.is_ok(), b.is_ok());
        assert!(matches!(
            a.err().or_else(|| b.err()),
            Some(DraftCoreError::Conflict)
        ));
        assert_eq!(
            service
                .read(&id, 2)
                .await
                .unwrap()
                .definition_yaml,
            yaml
        );
        service.delete("owner", &id).await.unwrap();
        assert!(service.metadata("owner", &id).await.unwrap().deleted);
        manager.close().await;
    }
}
