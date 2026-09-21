//! Explicit migration utility; never invoked by the server's startup path.
use std::{path::PathBuf, sync::Arc};
use anyhow::{Result, anyhow, ensure};
use bcs_bot_store::DbProviderStore;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_db_mysql::{AsyncMysqlDbManager, MysqlDbConfig, MysqlDbPlugin};
use clap::Parser;

#[derive(Parser)]
struct Args {
    /// Existing SQLite database; schema expansion must already be applied.
    #[arg(long, required_unless_present = "mysql_config", conflicts_with = "mysql_config")]
    sqlite: Option<PathBuf>,
    /// Protected TOML file containing MysqlDbConfig (never printed).
    #[arg(long)]
    mysql_config: Option<PathBuf>,
    /// Safety assertion: must match the environment resolved by SERVER_ENV.
    #[arg(long)]
    env: String,
    /// Apply after a clean audit. Without this flag, no data is changed.
    #[arg(long, requires = "writers_fenced")]
    apply: bool,
    /// Confirm ALL writers, including old binaries, have been stopped/fenced.
    #[arg(long)]
    writers_fenced: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    ensure!(args.env == bcs_config::resolve_env_str(), "--env does not match the configured environment");
    let (store, manager) = if let Some(path) = args.sqlite {
        ensure!(path.is_file(), "SQLite database must already exist");
        let db = LocalSqliteDbPlugin::new_file(path).map_err(|_| anyhow!("cannot open SQLite database"))?;
        (DbProviderStore::sqlite(Arc::new(db)), None)
    } else {
        let path = args.mysql_config.ok_or_else(|| anyhow!("database selection required"))?;
        let contents = std::fs::read_to_string(path).map_err(|_| anyhow!("cannot read MySQL configuration"))?;
        let config: MysqlDbConfig = toml::from_str(&contents).map_err(|_| anyhow!("invalid MySQL configuration"))?;
        let datasource = config.datasource_name();
        let manager = AsyncMysqlDbManager::new(config).await.map_err(|_| anyhow!("cannot connect to MySQL"))?;
        (DbProviderStore::mysql(Arc::new(MysqlDbPlugin::new(manager.clone(), datasource))), Some(manager))
    };
    let result = store.backfill_provider_bots(args.apply, args.writers_fenced).await;
    if let Some(manager) = manager { manager.close().await; }
    let report = result.map_err(|error| anyhow!("{error}"))?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    ensure!(report.issues.is_empty(), "backfill blocked by audit issues; no data changed");
    Ok(())
}
