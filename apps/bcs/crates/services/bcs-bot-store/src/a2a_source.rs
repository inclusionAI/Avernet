//! Provider-scoped source identity guards. No network calls under these locks.
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement, DbTransactionStep, DbValue};
use bcs_service_api::types::a2a_bot::{
    A2aBotRecord,
    a2a_management::{ManagementCode, ManagementError, SOURCE_AGENT_EXTENSION, source_agent},
};
use bcs_service_api::{ServiceError, ServiceResult};

fn storage(error: impl std::fmt::Display) -> ServiceError {
    ServiceError::InternalError(format!("source identity storage: {error}"))
}

pub(crate) fn provider_lock(flavor: DbSqlFlavor, env: &str, provider: &str) -> DbTransactionStep {
    // Provider rows pre-exist registration and all source writes take this lock
    // before the Bot row. SQLite serializes writers at the first write step.
    if flavor == DbSqlFlavor::Sqlite {
        return DbTransactionStep::Query(DbStatement::new("SELECT 1"));
    }
    let suffix = " FOR UPDATE";
    DbTransactionStep::Query(DbStatement::with_params(
        format!("SELECT provider_id FROM bcs_providers WHERE env=? AND provider_id=?{suffix}"),
        vec![env.into(), provider.into()],
    ))
}

pub(crate) fn conflict_query(
    flavor: DbSqlFlavor,
    env: &str,
    record: &A2aBotRecord,
) -> ServiceResult<DbStatement> {
    let source = source_agent(&record.agent_card)?;
    let Some(source) = source else {
        return Ok(DbStatement::new("SELECT 0 AS conflicts"));
    };
    let (join, predicate) = match flavor {
        DbSqlFlavor::Sqlite => (
            "json_each(CASE WHEN JSON_VALID(b.agent_card) THEN b.agent_card ELSE '{}' END,'$.capabilities.extensions') AS ext",
            "json_extract(ext.value,'$.uri')=? COLLATE BINARY AND json_extract(ext.value,'$.params.agentId')=? COLLATE BINARY",
        ),
        DbSqlFlavor::Mysql => (
            "JSON_TABLE(COALESCE(b.agent_card,JSON_OBJECT()), '$.capabilities.extensions[*]' COLUMNS(uri VARCHAR(1024) PATH '$.uri', agent_id VARCHAR(256) PATH '$.params.agentId')) AS ext",
            "CAST(ext.uri AS BINARY)=CAST(? AS BINARY) AND CAST(ext.agent_id AS BINARY)=CAST(? AS BINARY)",
        ),
    };
    Ok(DbStatement::with_params(
        format!(
            "SELECT EXISTS(SELECT 1 FROM bcs_bots b JOIN {join} ON 1=1 WHERE b.env=? AND b.provider_id=? AND b.is_deleted=0 AND b.bot_uuid<>? AND {predicate} LIMIT 1) AS conflicts"
        ),
        vec![
            env.into(),
            record.provider_id.clone().into(),
            record.bot_uuid.clone().into(),
            SOURCE_AGENT_EXTENSION.into(),
            source.agent_id.into(),
        ],
    ))
}

pub(crate) async fn check_available(
    db: &dyn DbPlugin,
    flavor: DbSqlFlavor,
    env: &str,
    record: &A2aBotRecord,
) -> ServiceResult<()> {
    if source_agent(&record.agent_card)?.is_none() {
        return Ok(());
    }
    let rows = db
        .query(conflict_query(flavor, env, record)?)
        .await
        .map_err(storage)?;
    if rows
        .first()
        .map(|r| r.get_i64("conflicts"))
        .transpose()
        .map_err(storage)?
        .flatten()
        != Some(0)
    {
        return Err(ManagementError::new(
            ManagementCode::SourceAgentConflict,
            "agent_card.capabilities.extensions",
            "ALREADY_EXISTS",
        )
        .into());
    }
    Ok(())
}

pub(crate) fn conflict_param(index: usize) -> bcs_db_api::DbTransactionParam {
    bcs_db_api::DbTransactionParam::query_result(index, 0, "conflicts")
}
pub(crate) fn bound_params(values: Vec<DbValue>) -> Vec<bcs_db_api::DbTransactionParam> {
    values.into_iter().map(Into::into).collect()
}
