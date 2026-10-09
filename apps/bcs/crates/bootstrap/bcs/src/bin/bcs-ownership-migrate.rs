//! `bcs-ownership-migrate` — governed historical-ownership cutover binary
//! (plan Task 17, runbook `docs/runbooks/bot-authority-cutover.md`).
//!
//! The binary is the ONLY supported entry of the one-shot authority
//! cutover; it contains no HTTP surface and `bcs-cli` gains no new leaf.
//! Discipline it enforces:
//! - `--maintenance` is a required acknowledgment: nothing runs without it;
//! - the datasource comes from the SAME config chain the BCS server loads
//!   (`--config-dir` / `BCS_CONFIG_DIR`), never from ad-hoc flags;
//! - `inspect` is the dry-run: the complete keyset-walked candidate
//!   list with conflict attributions, ZERO writes;
//! - `apply` is the only write path: it consumes an explicit out-of-band
//!   confirmation file of candidate Bot ids and re-derives every decision
//!   from current facts at execution time — the confirm file can never
//!   smuggle in an owner;
//! - exit codes: `0` success (a `failed`-free apply), `1` for any storage
//!   failure or per-Bot failed entry (the committed prefix of a failed
//!   batch stays recoverable by re-running the same `--batch-id`),
//!   `2` for usage errors (clap, unreadable/invalid confirmation file,
//!   refused request shapes).

use std::io::Write as _;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use serde_json::json;

/// Safety bound for the inspect walk (pages of ≤100 candidates): large
/// libraries page fine, an infinite loop anywhere must still terminate the
/// command instead of spinning. 10,000 pages = 1M candidates read.
const MAX_INSPECT_PAGES: usize = 10_000;

/// Run-failure taxonomy (implemented here, documented in the runbook's
/// exit-code table — the two must stay aligned):
/// - [`RunFailure::Usage`] -> exit `2`: operator/input mistakes (unreadable
///   or mis-shaped confirmation file, over-limit page, blank batch id,
///   blank anchors — plus clap's own argument-level errors, which exit `2`
///   before `run` is reached). Fix the invocation/input; never re-run the
///   same batch expecting recovery.
/// - [`RunFailure::Execution`] -> exit `1`: execution failures at the
///   storage/config boundary; a committed batch prefix recovers by re-running
///   the SAME `--batch-id`.
enum RunFailure {
    /// Operator/input error: exit code `2` (same class as clap usage errors).
    Usage(String),
    /// Execution failure (storage/config): exit code `1`.
    Execution(String),
}

impl RunFailure {
    fn exit_code(&self) -> u8 {
        match self {
            Self::Usage(_) => 2,
            Self::Execution(_) => 1,
        }
    }
}

/// Service errors split along the same line: a rejected request shape
/// (`InvalidOperation`: over-limit page, blank batch id, blank ids/anchor)
/// is a usage error, everything else is an execution failure.
fn migration_failure(context: &str, error: bcs_service_api::ServiceError) -> RunFailure {
    match error {
        bcs_service_api::ServiceError::InvalidOperation { .. } => {
            RunFailure::Usage(format!("{context}: rejected request shape: {error}"))
        }
        other => RunFailure::Execution(format!("{context}: {other}")),
    }
}

#[derive(Parser, Debug)]
#[command(
    name = "bcs-ownership-migrate",
    version = bcs::BCS_VERSION,
    about = "Governed one-shot historical ownership cutover (see docs/runbooks/bot-authority-cutover.md)"
)]
struct Args {
    /// Configuration directory or standalone config file, the SAME layout
    /// the BCS server binary uses (bcs-config.toml / bcs-config-{env}.toml).
    #[arg(short, long, value_name = "DIR", env = "BCS_CONFIG_DIR", global = true)]
    config_dir: Option<PathBuf>,
    /// Required maintenance acknowledgment: the cutover is a governed,
    /// one-shot operation on the production authority state.
    #[arg(long, required = true)]
    maintenance: bool,
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// Dry-run: the complete candidate/conflict governance list. Writes NOTHING.
    Inspect {
        /// Candidates per read page (1..=100, the bounded batch default).
        #[arg(long, default_value_t = 100)]
        limit: u32,
        /// Resume strictly after this Bot id (keyset anchor for large walks).
        #[arg(long, value_name = "BOT_ID")]
        after_bot_id: Option<String>,
    },
    /// Initialize one explicitly confirmed batch. The ONLY write path.
    Apply {
        /// The governed batch id: the ledger tag and the replay/recovery key
        /// of this batch.
        #[arg(long, value_name = "ID")]
        batch_id: String,
        /// JSON array of confirmed candidate Bot ids (e.g. produced from the
        /// inspect list). Carries ids only — never owners.
        #[arg(long, value_name = "FILE")]
        confirm_file: PathBuf,
    },
}

fn main() -> ExitCode {
    let args = Args::parse();
    if !args.maintenance {
        // clap already enforces the flag; this branch keeps the contract
        // explicit if the requirement is ever relaxed.
        eprintln!("bcs-ownership-migrate: --maintenance is required for the cutover");
        return ExitCode::from(2);
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("tokio runtime");
    match runtime.block_on(run(args)) {
        Ok(code) => ExitCode::from(code),
        Err(failure) => {
            let code = failure.exit_code();
            match failure {
                RunFailure::Usage(message) | RunFailure::Execution(message) => {
                    eprintln!("bcs-ownership-migrate: {message}")
                }
            }
            if code == 1 {
                eprintln!(
                    "bcs-ownership-migrate: storage failure; re-run the SAME --batch-id to \
                     recover the batch from its committed prefix"
                );
            }
            ExitCode::from(code)
        }
    }
}

async fn run(args: Args) -> Result<u8, RunFailure> {
    // Only the existing configuration chain selects the datasource.
    let config = bcs::BcsConfig::load_with_env(args.config_dir.as_ref());
    let service = bcs::ownership_migration_wiring::ownership_migration_service_from_config(&config)
        .await
        .map_err(|error| RunFailure::Execution(format!("datasource selection failed: {error}")))?;
    match args.command {
        Command::Inspect { limit, after_bot_id } => {
            let mut after = after_bot_id;
            let mut candidates = Vec::new();
            let mut pages = 0usize;
            loop {
                let page = service
                    .inspect_batch(after.clone(), limit)
                    .await
                    .map_err(|error| migration_failure("ownership migration dry-run failed", error))?;
                pages += 1;
                let next = page.next_bot_id.clone();
                candidates.extend(page.candidates);
                match next {
                    Some(anchor) => after = Some(anchor),
                    None => break,
                }
                if pages >= MAX_INSPECT_PAGES {
                    return Err(RunFailure::Usage(format!(
                        "the dry-run walk exceeded {MAX_INSPECT_PAGES} pages; rerun with \
                         a stricter --after-bot-id anchor"
                    )));
                }
            }
            let summary = candidates.iter().fold(std::collections::BTreeMap::new(), |mut acc, candidate| {
                *acc.entry(candidate.reason.as_str().to_string())
                    .or_insert(0usize) += 1;
                acc
            });
            let document = json!({
                "pages": pages,
                "summary": summary,
                "candidates": candidates,
            });
            println!("{document}");
            Ok(0)
        }
        Command::Apply { batch_id, confirm_file } => {
            // Input mistakes are USAGE failures: the batch never started, so
            // the runbook's "re-run the same batch" guidance does not apply.
            let raw = std::fs::read_to_string(&confirm_file)
                .map_err(|error| RunFailure::Usage(format!("cannot read confirmation file: {error}")))?;
            let parsed: serde_json::Value = serde_json::from_str(&raw)
                .map_err(|error| RunFailure::Usage(format!("confirmation file is not valid JSON: {error}")))?;
            let confirmed: Vec<String> = parsed
                .as_array()
                .ok_or_else(|| RunFailure::Usage("confirmation file must be a JSON array".to_string()))?
                .iter()
                .map(|item| {
                    item.as_str()
                        .map(str::to_string)
                        .ok_or_else(|| RunFailure::Usage("confirmation entries must be Bot id strings".to_string()))
                })
                .collect::<Result<Vec<String>, RunFailure>>()?;
            let report = service
                .initialize_batch(confirmed, batch_id)
                .await
                .map_err(|error| migration_failure("ownership migration apply failed", error))?;
            let failed = report.failed.len();
            let document = serde_json::to_string(&report)
                .map_err(|error| RunFailure::Execution(format!("report serialization failed: {error}")))?;
            println!("{document}");
            let _ = std::io::stdout().flush();
            if failed > 0 {
                eprintln!(
                    "bcs-ownership-migrate: {failed} candidate(s) failed at the storage boundary; \
                     re-run the SAME --batch-id to recover the batch from its committed prefix"
                );
                Ok(1)
            } else {
                Ok(0)
            }
        }
    }
}