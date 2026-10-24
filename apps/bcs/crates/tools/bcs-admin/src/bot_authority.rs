//! `bot-authority` — the bcs-admin ownership
//! governance entry (bot-authority plan Task 4.6,
//! spec §10.4/§10.5).
//!
//! `inspect` is the zero-write dry-run: it classifies
//! each explicitly listed Bot from the SAME facts the
//! apply classification consumes (live state,
//! `created_by`, whether the owner's Human ACTOR row is
//! live in the instance env, the current version, the
//! approved owner edges — read through the SAME
//! `BotRegistryCoreService` facade, never raw SQL in the
//! CLI) into
//! backfillable / missing-Human / relation-conflict /
//! already-initialized; the missing/conflict output is
//! the manual-governance list.
//!
//! `initialize` applies the backfill per Bot ATOMICALLY
//! (`initialize_ownership` composes owner edge + version
//! + audit in ONE store transaction): only explicitly
//! confirmed IDs, at most 100 deduplicated per batch,
//! inside a maintenance window (`--maintenance`; the
//! old writers must be frozen first). Repeats on an
//! initialized Bot return its CURRENT owner without
//! rewriting `created_by` or resetting a
//! since-transferred owner. Every write goes through
//! the governance facade; there is no migration ledger.
//!
//! Environment: the store binds the PROCESS env
//! (`SERVER_ENV`, the same resolution the server uses)
//! — the instance env cannot be overridden per call.

use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{Result, anyhow, bail};
use bcs::{BcsConfig, DatabaseType};
use bcs_bot::BotCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{DbPlugin, DbSqlFlavor};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityMutation, BotOwnershipGovernance,
    MUTATION_ID_MAX_LEN,
};
use bcs_service_api::{ActorKind, BotRegistryCoreService};
use clap::Subcommand;

use crate::migrate::{
    ConfiguredMysqlDb, MigrateGlobalArgs, load_bcs_config,
    open_configured_mysql_db, open_sqlite_db,
};

/// spec §10.5: apply 仅处理显式确认的 Bot ID，
/// 每批最多 100 去重.
pub(crate) const MAX_BATCH_IDS: usize = 100;

#[derive(Debug, Subcommand)]
pub enum BotAuthorityCommand {
    /// Classify Bot ownership-backfill state per Bot.
    /// ZERO writes: missing/conflict bots feed the
    /// manual-governance list.
    Inspect {
        /// Bot ID to inspect. Repeatable; at most 100
        /// deduplicated IDs per batch.
        #[arg(long = "bot-id")]
        bot_ids: Vec<String>,
        /// SQLite database file (overrides the
        /// configured `[database.sqlite].path`).
        #[arg(long)]
        sqlite_path: Option<PathBuf>,
    },
    /// Apply the ownership backfill per Bot.
    /// Requires `--maintenance`.
    Initialize {
        /// Bot ID to initialize. Repeatable; at most
        /// 100 deduplicated IDs per batch.
        #[arg(long = "bot-id")]
        bot_ids: Vec<String>,
        /// Confirm the maintenance window: the legacy
        /// writers were frozen BEFORE this command
        /// runs (spec §10.4).
        #[arg(long)]
        maintenance: bool,
        /// SQLite database file (overrides the
        /// configured `[database.sqlite].path`).
        #[arg(long)]
        sqlite_path: Option<PathBuf>,
    },
}

/// Database/config selection, mirroring the
/// `db migrate` handling of `--config-dir` /
/// `--config` / `--sqlite-path`.
#[derive(Debug, Clone)]
pub(crate) struct BotAuthorityEnvArgs {
    pub config_dir: Option<PathBuf>,
    pub config_file: Option<PathBuf>,
    pub sqlite_path: Option<PathBuf>,
}

impl From<&MigrateGlobalArgs> for BotAuthorityEnvArgs {
    fn from(global: &MigrateGlobalArgs) -> Self {
        Self {
            config_dir: global.config_dir.clone(),
            config_file: global.config_file.clone(),
            sqlite_path: None,
        }
    }
}

pub(crate) enum GovernanceDb {
    Sqlite(Arc<LocalSqliteDbPlugin>),
    MySql {
        plugin: Arc<MysqlDbPlugin>,
        /// Kept alive on purpose: the manager owns
        /// the pool the plugin borrows.
        #[allow(dead_code, reason = "keeps the MySQL pool alive")]
        manager: MysqlDbManager,
        datasource_name: String,
    },
}

impl GovernanceDb {
    /// Test/driver constructor for an already-opened
    /// SQLite database.
    #[cfg(test)]
    pub(crate) fn sqlite(db: Arc<LocalSqliteDbPlugin>) -> Self {
        GovernanceDb::Sqlite(db)
    }

    fn plugin(&self) -> Arc<dyn DbPlugin> {
        match self {
            GovernanceDb::Sqlite(db) => db.clone(),
            GovernanceDb::MySql { plugin, .. } => plugin.clone(),
        }
    }

    fn flavor(&self) -> DbSqlFlavor {
        match self {
            GovernanceDb::Sqlite(_) => DbSqlFlavor::Sqlite,
            GovernanceDb::MySql { .. } => DbSqlFlavor::Mysql,
        }
    }
}

/// The governance facade: the SAME
/// `BotRegistryCoreService` the apply uses
/// (`BotCore` over the configured persistent store)
/// plus the instance env for the report header.
pub(crate) struct GovernanceContext {
    pub(crate) registry: Arc<dyn BotRegistryCoreService>,
    pub(crate) env: String,
}

fn mutation_for(_bot_id: &str) -> AuthorityMutation {
    AuthorityMutation {
        // Server-generated idempotence key (never
        // client data). A bounded random id, NOT a
        // bot-id-mixed one: `backfill_{bot_id}_{ms}`
        // exceeded the audit table's VARCHAR(64)
        // `mutation_id` for long Bot IDs (a real
        // MySQL run would roll back the whole
        // backfill on ERROR 1406). The Bot identity
        // already lives in the audit row's own
        // `bot_id` column.
        mutation_id: format!(
            "backfill-{}",
            uuid::Uuid::new_v4().simple()
        ),
        actor: AuthorityActor::System {
            name: "bcs-admin".to_string(),
        },
    }
}

/// The four-way governance classification
/// (`report`-stable) plus the reason model.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum BackfillState {
    /// Created record consistent with the historical
    /// relations and pointing at a valid Human (a live
    /// Human ACTOR row in the instance env),
    /// version 0 — `initialize` may backfill (spec
    /// §10.4).
    Backfillable,
    /// The `created_by` owner has no live Human ACTOR
    /// row in the instance env (or no created record
    /// `created_by`).
    MissingHuman,
    /// The historical relations contradict the
    /// created record (created record mixed with
    /// owner edges, multiple owner edges, Human row
    /// mis-shape).
    RelationConflict,
    /// Version > 0: initialize is a no-op that
    /// reports the CURRENT owner (never resets).
    AlreadyInitialized,
    /// Tombstoned rows never resurrect.
    NotLive,
    /// The ID is unknown in the instance env.
    Unknown,
}

impl BackfillState {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            BackfillState::Backfillable => "backfillable",
            BackfillState::MissingHuman => "missing_human",
            BackfillState::RelationConflict => "relation_conflict",
            BackfillState::AlreadyInitialized => "already_initialized",
            BackfillState::NotLive => "not_live",
            BackfillState::Unknown => "unknown",
        }
    }
}

pub(crate) struct Classification {
    pub state: BackfillState,
    pub reason: String,
}

/// Pure classification over the governance read.
/// The facts come from the same Core facade that
/// `initialize_ownership` guards, so the dry-run and
/// the apply always agree about the world.
pub(crate) fn classify(
    governance: &BotOwnershipGovernance,
) -> Classification {
    if !governance.live {
        return Classification {
            state: BackfillState::NotLive,
            reason: "the Bot row is tombstoned and never \
                     re-enters governance"
                .to_string(),
        };
    }
    if governance.actor_kind != ActorKind::Bot {
        return Classification {
            state: BackfillState::RelationConflict,
            reason: "the record is a Human actor row, not a \
                     physical Bot"
                .to_string(),
        };
    }
    if governance.ownership_version > 0 {
        return Classification {
            state: BackfillState::AlreadyInitialized,
            reason: "ownership is already initialized at \
                     version > 0; repeats report the current \
                     owner and never reset it"
                .to_string(),
        };
    }
    let Some(created_by) = governance.created_by.as_deref() else {
        return Classification {
            state: BackfillState::MissingHuman,
            reason: "the version-0 Bot carries no created record \
                     (created_by) to backfill from"
                .to_string(),
        };
    };
    if !governance.owner_actor_present {
        return Classification {
            state: BackfillState::MissingHuman,
            reason: format!(
                "the created record '{created_by}' has no live Human \
                 actor row in the instance env"
            ),
        };
    }
    // The historical relations must not contradict the
    // created record: every approved owner edge has to
    // point at THAT Human, and exactly one owner edge
    // may exist.
    match governance.owner_edges.len() {
        0 => Classification {
            state: BackfillState::Backfillable,
            reason: format!(
                "version 0, owner '{created_by}' is a valid Human, \
                 no contradicting owner edge"
            ),
        },
        1 => {
            let edge_actor = &governance.owner_edges[0];
            if edge_actor == &format!("human_{created_by}") {
                Classification {
                    state: BackfillState::Backfillable,
                    reason: format!(
                        "version 0, owner '{created_by}' is a \
                         valid Human and the single owner edge \
                         already agrees"
                    ),
                }
            } else {
                Classification {
                    state: BackfillState::RelationConflict,
                    reason: format!(
                        "the created record '{created_by}' is \
                         contradicted by an approved owner edge \
                         for '{edge_actor}'"
                    ),
                }
            }
        }
        _ => Classification {
            state: BackfillState::RelationConflict,
            reason: format!(
                "{} approved owner edges exist; the owner \
                 integrity is broken and needs the manual \
                 governance",
                governance.owner_edges.len()
            ),
        },
    }
}

/// Current owner of an already-initialized Bot
/// (current state echo for the repeat report); a
/// broken multi-owner state reports nothing.
fn current_owner_user(
    governance: &BotOwnershipGovernance,
) -> Option<String> {
    match governance.owner_edges.as_slice() {
        [edge_actor] => {
            edge_actor.strip_prefix("human_").map(str::to_string)
        }
        _ => None,
    }
}

pub(crate) fn normalize_ids(raw: &[String]) -> Result<Vec<String>> {
    let mut ids = Vec::new();
    for id in raw {
        let id = id.trim();
        if id.is_empty() {
            bail!("--bot-id values must be non-empty");
        }
        if !ids.iter().any(|seen| seen == id) {
            ids.push(id.to_string());
        }
    }
    if ids.is_empty() {
        bail!("list at least one --bot-id");
    }
    if ids.len() > MAX_BATCH_IDS {
        bail!(
            "each batch carries at most {MAX_BATCH_IDS} deduplicated \
             Bot IDs; got {}",
            ids.len()
        );
    }
    Ok(ids)
}

async fn open_governance_db(
    args: &BotAuthorityEnvArgs,
) -> Result<GovernanceDb> {
    if let Some(path) = args.sqlite_path.as_ref() {
        if !path.exists() {
            bail!(
                "sqlite database '{}' does not exist; open the \
                 migrated server database, never a fresh file",
                path.display()
            );
        }
        return Ok(GovernanceDb::Sqlite(Arc::new(open_sqlite_db(
            path,
        )?)));
    }
    let config: BcsConfig = {
        let global = MigrateGlobalArgs {
            config_dir: args.config_dir.clone(),
            config_file: args.config_file.clone(),
        };
        load_bcs_config(&global)?
    };
    match config.database.database_type {
        DatabaseType::Sqlite => {
            let path = PathBuf::from(config.database.sqlite.path);
            if !path.exists() {
                bail!(
                    "sqlite database '{}' does not exist; open the \
                     migrated server database, never a fresh file",
                    path.display()
                );
            }
            Ok(GovernanceDb::Sqlite(Arc::new(open_sqlite_db(
                &path,
            )?)))
        }
        DatabaseType::Mysql => {
            let global = MigrateGlobalArgs {
                config_dir: args.config_dir.clone(),
                config_file: args.config_file.clone(),
            };
            let ConfiguredMysqlDb {
                manager,
                plugin,
                datasource_name,
            } = open_configured_mysql_db(&global).await?;
            Ok(GovernanceDb::MySql {
                manager,
                datasource_name,
                plugin: Arc::new(plugin),
            })
        }
        DatabaseType::Other(provider) => bail!(
            "configured database type '{provider}' is not supported \
             by bcs-admin bot-authority"
        ),
    }
}

pub(crate) fn build_context(db: &GovernanceDb) -> GovernanceContext {
    // BotCore over the configured persistent store —
    // the same facade the apply writes through, no
    // direct SQL in the CLI.
    let repo = Arc::new(PersistentBotRepo::with_sql_flavor(
        db.plugin(),
        db.flavor(),
    ));
    GovernanceContext {
        registry: Arc::new(BotCore::with_repo(repo)),
        env: bcs_config::resolve_env_str(),
    }
}

pub(crate) async fn run_bot_authority(
    args: &BotAuthorityCommand,
    env_args: &BotAuthorityEnvArgs,
) -> Result<()> {
    match args {
        BotAuthorityCommand::Inspect {
            bot_ids,
            sqlite_path,
        } => {
            let ids = normalize_ids(bot_ids)?;
            let env_args = BotAuthorityEnvArgs {
                sqlite_path: sqlite_path.clone(),
                ..env_args.clone()
            };
            let db = open_governance_db(&env_args).await?;
            let context = build_context(&db);
            let report = inspect(&context, &db, &ids).await?;
            print!("{report}");
            Ok(())
        }
    BotAuthorityCommand::Initialize {
            bot_ids,
            maintenance,
            sqlite_path,
        } => {
            if !maintenance {
                bail!(
                    "--maintenance is required: freeze the legacy \
                     writers inside the maintenance window first and \
                     run `bot-authority inspect` as the dry-run \
                     (spec §10.4)"
                );
            }
            let ids = normalize_ids(bot_ids)?;
            let env_args = BotAuthorityEnvArgs {
                sqlite_path: sqlite_path.clone(),
                ..env_args.clone()
            };
            let db = open_governance_db(&env_args).await?;
            let context = build_context(&db);
            let report = initialize(&context, &db, &ids).await?;
            print!("{report}");
            Ok(())
        }
    }
}

/// One row-shaped result of the per-Bot reads.
struct GovernanceItem {
    bot_id: String,
    classification: Option<Classification>,
    governance: Option<BotOwnershipGovernance>,
    read_error: Option<String>,
}

async fn read_items(
    context: &GovernanceContext,
    db: &GovernanceDb,
    ids: &[String],
) -> Result<Vec<GovernanceItem>> {
    // A smoke read before the batch so connection/config
    // failures surface once, up front, with the
    // existing admin error conventions.
    if let Some(first) = ids.first() {
        context
            .registry
            .ownership_governance_state(first)
            .await
            .map_err(|error| {
                anyhow!(
                    "read governance state from the instance \
                     database (flavor {}, datasource {}): {error}",
                    match db {
                        GovernanceDb::Sqlite(_) => "sqlite",
                        GovernanceDb::MySql {
                            datasource_name, ..
                        } => datasource_name.as_str(),
                    },
                    match db {
                        GovernanceDb::Sqlite(_) => "sqlite".to_string(),
                        GovernanceDb::MySql {
                            datasource_name, ..
                        } => datasource_name.clone(),
                    }
                )
            })?;
    }
    let mut items = Vec::with_capacity(ids.len());
    for bot_id in ids {
        let item = match context
            .registry
            .ownership_governance_state(bot_id)
            .await
        {
            Ok(Some(governance)) => GovernanceItem {
                bot_id: bot_id.clone(),
                classification: Some(classify(&governance)),
                governance: Some(governance),
                read_error: None,
            },
            Ok(None) => GovernanceItem {
                bot_id: bot_id.clone(),
                classification: Some(Classification {
                    state: BackfillState::Unknown,
                    reason: format!(
                        "no Bot row in env {} carries this ID",
                        bcs_config::resolve_env_str()
                    ),
                }),
                governance: None,
                read_error: None,
            },
            Err(error) => GovernanceItem {
                bot_id: bot_id.clone(),
                classification: None,
                governance: None,
                read_error: Some(error.to_string()),
            },
        };
        items.push(item);
    }
    Ok(items)
}

pub(crate) async fn inspect(
    context: &GovernanceContext,
    db: &GovernanceDb,
    ids: &[String],
) -> Result<String> {
    let items = read_items(context, db, ids).await?;
    let mut output = format!(
        "bot-authority inspect (dry-run, ZERO writes; env={})\n",
        context.env
    );
    let mut counts = String::new();
    for item in &items {
        match (&item.classification, &item.read_error) {
            (Some(classification), _) => {
                let governance = item.governance.as_ref();
                let owner = governance
                    .and_then(current_owner_user)
                    .or_else(|| governance.and_then(|g| g.created_by.clone()))
                    .unwrap_or_else(|| "-".to_string());
                output.push_str(&format!(
                    "bot_id={} state={} reason=\"{}\" owner={} \
                     version={}\n",
                    item.bot_id,
                    classification.state.as_str(),
                    classification.reason,
                    owner,
                    item.governance
                        .as_ref()
                        .map(|governance| governance.ownership_version)
                        .unwrap_or(0),
                ));
            }
            (None, Some(error)) => {
                output.push_str(&format!(
                    "bot_id={} state=unknown reason=\"read \
                     failed: {error}\"\n",
                    item.bot_id
                ));
            }
            (None, None) => unreachable!("one of the two is set"),
        }
    }
    for state in [
        BackfillState::Backfillable,
        BackfillState::MissingHuman,
        BackfillState::RelationConflict,
        BackfillState::AlreadyInitialized,
        BackfillState::NotLive,
        BackfillState::Unknown,
    ] {
        let count = items
            .iter()
            .filter(|item| {
                item.classification
                    .as_ref()
                    .is_some_and(|found| found.state == state)
            })
            .count();
        counts.push_str(&format!(
            "{}{}={}",
            if counts.is_empty() { "" } else { " " },
            state.as_str(),
            count
        ));
    }
    output.push_str(&format!("summary: {counts}\n"));
    Ok(output)
}

pub(crate) async fn initialize(
    context: &GovernanceContext,
    db: &GovernanceDb,
    ids: &[String],
) -> Result<String> {
    let items = read_items(context, db, ids).await?;
    let mut output = format!(
        "bot-authority initialize (per-Bot atomic, one \
         transaction each; env={})\n",
        context.env
    );
    let mut backfilled = 0usize;
    let mut unchanged = 0usize;
    let mut rejected = 0usize;
    let mut failed = 0usize;
    for item in &items {
        let Some(classification) = item.classification.as_ref() else {
            failed += 1;
            output.push_str(&format!(
                "bot_id={} state=error reason=\"read failed: {}\" (no \
                 write)\n",
                item.bot_id,
                item.read_error.clone().unwrap_or_default()
            ));
            continue;
        };
        match classification.state {
            BackfillState::Backfillable => {
                // created_by IS the backfill owner; the
                // initialize guard re-validates the
                // Human identity inside its transaction.
                let owner = item
                    .governance
                    .as_ref()
                    .and_then(|governance| {
                        governance.created_by.clone()
                    })
                    .ok_or_else(|| {
                        anyhow!(
                            "bot {}: backfillable without a created \
                             record",
                            item.bot_id
                        )
                    })?;
                match context
                    .registry
                    .initialize_ownership(
                        &item.bot_id,
                        &owner,
                        mutation_for(&item.bot_id),
                    )
                    .await
                {
                    Ok(view) => {
                        backfilled += 1;
                        output.push_str(&format!(
                            "bot_id={} state=backfilled owner={} \
                             version=1\n",
                            item.bot_id, view.owner_user_id
                        ));
                    }
                    Err(error) => {
                        failed += 1;
                        output.push_str(&format!(
                            "bot_id={} state=error reason=\"{}\" (no \
                             partial write; the transaction rolled \
                             back)\n",
                            item.bot_id, error
                        ));
                    }
                }
            }
            BackfillState::AlreadyInitialized => {
                // Repeat execution: return the CURRENT
                // state — the same-owner initialize is
                // the idempotent echo, no created_by
                // rewrite, no owner reset, no second
                // initialize audit.
                let owner = item
                    .governance
                    .as_ref()
                    .and_then(current_owner_user);
                match owner {
                    Some(owner) => {
                        match context
                            .registry
                            .initialize_ownership(
                                &item.bot_id,
                                &owner,
                                mutation_for(&item.bot_id),
                            )
                            .await
                        {
                            Ok(view) => {
                                unchanged += 1;
                                output.push_str(&format!(
                                    "bot_id={} \
                                     state=already_initialized \
                                     owner={} version=1 \
                                     (current state echoed; nothing \
                                     written)\n",
                                    item.bot_id, view.owner_user_id
                                ));
                            }
                            Err(error) => {
                                failed += 1;
                                output.push_str(&format!(
                                    "bot_id={} state=error \
                                     reason=\"{}\" (no partial \
                                     write)\n",
                                    item.bot_id, error
                                ));
                            }
                        }
                    }
                    None => {
                        rejected += 1;
                        output.push_str(&format!(
                            "bot_id={} state=error \
                             reason=\"initialized owner integrity is \
                             broken ({} owner edges); needs the \
                             manual governance\" (no write)\n",
                            item.bot_id,
                            item.governance
                                .as_ref()
                                .map(|governance| governance
                                    .owner_edges
                                    .len())
                                .unwrap_or_default()
                        ));
                    }
                }
            }
            // The missing/conflict output IS the manual
            // governance list — explicit per-item
            // rejection, never a partial write.
            BackfillState::MissingHuman
            | BackfillState::RelationConflict
            | BackfillState::NotLive
            | BackfillState::Unknown => {
                rejected += 1;
                output.push_str(&format!(
                    "bot_id={} state={} reason=\"{}\" (no write; \
                     manual governance required)\n",
                    item.bot_id,
                    classification.state.as_str(),
                    classification.reason
                ));
            }
        }
    }
    output.push_str(&format!(
        "summary: backfilled={backfilled} unchanged={unchanged} \
         rejected={rejected} failed={failed}\n"
    ));
    Ok(output)
}

#[cfg(test)]
#[path = "bot_authority_tests.rs"]
mod authority_backfill_tests;
