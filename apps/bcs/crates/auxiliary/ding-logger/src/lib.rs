pub mod config;
mod dedup;
mod handler;
mod listener;

pub use config::GroupLoggerConfig;

use std::collections::HashSet;

use dedup::DedupStore;

/// Start the group message logger.
///
/// Validates config then runs the DingTalk Stream listener in a loop.
/// Log output is handled by BCS's logging system via the `ding_group_message`
/// tracing target — add an entry in `logging.outputs` to route it to a file.
///
/// This function never returns under normal operation.
pub async fn run(config: GroupLoggerConfig) -> anyhow::Result<()> {
    config.validate()?;
    let group_ids: HashSet<String> = config.group_ids.iter().cloned().collect();
    let dedup = DedupStore::new();
    listener::run_listener(config, group_ids, dedup).await;
    Ok(())
}
