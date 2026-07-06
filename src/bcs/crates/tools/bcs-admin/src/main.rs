mod migrate;
mod seed;
mod seed_loader;

use std::path::PathBuf;

use anyhow::{Result, bail};
use clap::{Args, Parser, Subcommand};

#[derive(Debug, Parser)]
#[command(name = "bcs-admin")]
#[command(about = "Operator tools for Bot Coordination Service")]
struct Cli {
    #[command(flatten)]
    global: GlobalArgs,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Debug, Args)]
struct GlobalArgs {
    /// BCS config directory or standalone config file, matching the bcs service behavior.
    #[arg(long, env = "BCS_CONFIG_DIR", global = true)]
    config_dir: Option<PathBuf>,

    /// Legacy single-file BCS config path. Prefer --config-dir.
    #[arg(long, global = true, hide = true)]
    config: Option<PathBuf>,

    /// Environment namespace used by seed data.
    #[arg(long, default_value = "dev", global = true)]
    env: String,

    /// Print extra diagnostic information.
    #[arg(long, global = true)]
    debug: bool,
}

#[derive(Debug, Subcommand)]
enum Commands {
    /// Database administration commands.
    Db {
        #[command(subcommand)]
        command: DbCommands,
    },

    /// Collaboration template administration commands.
    Template {
        #[command(subcommand)]
        command: TemplateCommands,
    },
}

#[derive(Debug, Subcommand)]
enum DbCommands {
    /// Emit or apply BCS SQL migrations.
    Migrate(migrate::MigrateArgs),
}

#[derive(Debug, Subcommand)]
enum TemplateCommands {
    /// Load collaboration template seed YAML and emit or apply catalog DML.
    Seed(seed::SeedArgs),
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    if cli.global.debug {
        eprintln!("bcs-admin env={}", cli.global.env);
        if let Some(config_dir) = &cli.global.config_dir {
            eprintln!("config_dir={}", config_dir.display());
        }
        if let Some(config) = &cli.global.config {
            eprintln!("config={}", config.display());
        }
    }

    match cli.command {
        Commands::Db { command } => match command {
            DbCommands::Migrate(args) => migrate::run_migrate(
                &args,
                &migrate::MigrateGlobalArgs {
                    config_dir: cli.global.config_dir.clone(),
                    config_file: cli.global.config.clone(),
                },
            )
            .await,
        },
        Commands::Template { command } => match command {
            TemplateCommands::Seed(args) => {
                if args.dry_run {
                    let summary = seed::dry_run_seed(&args)?;
                    println!("{summary}");
                    Ok(())
                } else if args.emit_sql {
                    let sql = seed::emit_seed_sql(&args, &cli.global.env)?;
                    print!("{sql}");
                    Ok(())
                } else {
                    bail!("template seed execution mode is not implemented yet; use --emit-sql")
                }
            }
        },
    }
}

pub(crate) fn bcs_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .and_then(|path| path.parent())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}
