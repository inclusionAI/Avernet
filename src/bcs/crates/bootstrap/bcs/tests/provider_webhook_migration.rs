use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn fresh_and_legacy_databases_expose_bot_endpoint_after_repeated_startup() {
    for legacy in [false, true] {
        let db = LocalSqliteDbPlugin::new().unwrap();
        if legacy {
            db.execute(DbStatement::new("CREATE TABLE bcs_provider_bot_bindings (
                bot_uuid TEXT NOT NULL, provider_id TEXT NOT NULL, provider_bot_ref TEXT NOT NULL,
                env TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0,
                gmt_create TEXT DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP
            )")).await.unwrap();
            db.execute(DbStatement::new("INSERT INTO bcs_provider_bot_bindings
                (bot_uuid, provider_id, provider_bot_ref, env) VALUES ('old', 'provider', 'ref', 'local')"))
                .await.unwrap();
        }
        bcs::migrations::run_sqlite_migrations(&db).await.unwrap();
        bcs::migrations::run_sqlite_migrations(&db).await.unwrap();
        let rows = db.query(DbStatement::new("SELECT webhook_url FROM bcs_provider_bot_bindings"))
            .await.expect("migration must add the nullable Bot endpoint column");
        assert_eq!(rows.len(), usize::from(legacy));
        if legacy {
            assert_eq!(rows[0].get("webhook_url"), Some(&bcs_db_api::DbValue::Null));
        }
    }
}
