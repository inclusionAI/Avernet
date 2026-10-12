//! MySQL/OceanBase-backed implementation crate for the `bcs-db-api` contract.
//!
//! Dependency-free local implementations live in `bcs-db-local`. Callers
//! outside composition roots should depend on `bcs-db-api`, not on this adapter
//! crate.

use std::collections::BTreeMap;

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use mysql_async::{consts::ColumnFlags, Column as MysqlColumn, Row as MysqlRow, Value as MysqlValue};

mod manager;

pub use bcs_config_api::{DataSourceConfig, MysqlDbConfig, StatementProtocol};
pub use manager::{AsyncMysqlDbManager, MysqlDbManager, MysqlExecuteResult, MysqlTransaction};

// MySQL collation ID 63 denotes the binary character set. BINARY_FLAG also
// applies to text with a _bin collation, so it cannot identify binary values.
const MYSQL_BINARY_CHARSET_ID: u16 = 63;

#[derive(Clone)]
pub struct MysqlDbPlugin {
    mysql: AsyncMysqlDbManager,
    db: String,
}

impl MysqlDbPlugin {
    pub fn new(mysql: AsyncMysqlDbManager, db: impl Into<String>) -> Self {
        Self {
            mysql,
            db: db.into(),
        }
    }

    pub fn db(&self) -> &str {
        &self.db
    }
}

#[async_trait]
impl DbPlugin for MysqlDbPlugin {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let params = statement.standalone_params()?;
        let rows = if params.is_empty() {
            self.mysql.query(&self.db, statement.sql()).await?
        } else {
            self.mysql
                .query_with(&self.db, statement.sql(), mysql_params(params)?)
                .await?
        };
        rows.into_iter().map(row_to_db_row).collect()
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        let params = statement.standalone_params()?;
        let result = if params.is_empty() {
            self.mysql.execute_result(&self.db, statement.sql()).await?
        } else {
            self.mysql
                .execute_with_result(&self.db, statement.sql(), mysql_params(params)?)
                .await?
        };
        Ok(DbExecuteResult {
            affected_rows: result.affected_rows,
            last_insert_id: result.last_insert_id,
        })
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        steps.iter().try_for_each(DbTransactionStep::validate)?;
        let steps = steps
            .into_iter()
            .map(PreparedTransactionStep::from)
            .collect::<Vec<_>>();

        self.mysql
            .with_transaction(&self.db, move |tx| {
                Box::pin(async move {
                    let mut results = Vec::with_capacity(steps.len());
                    for (step_index, step) in steps.into_iter().enumerate() {
                        match step {
                            PreparedTransactionStep::Query(statement) => {
                                let params =
                                    statement.resolve_transaction_params(&results, step_index)?;
                                let rows =
                                    tx.query(statement.sql(), mysql_params(&params)?).await?;
                                let rows = rows
                                    .into_iter()
                                    .map(row_to_db_row)
                                    .collect::<DbResult<Vec<_>>>()?;
                                results.push(DbTransactionStepResult::Rows(rows));
                            }
                            PreparedTransactionStep::Execute(statement) => {
                                let params =
                                    statement.resolve_transaction_params(&results, step_index)?;
                                let result = tx
                                    .execute_result(statement.sql(), mysql_params(&params)?)
                                    .await?;
                                let stop = statement.stops_transaction_on_no_rows()
                                    && result.affected_rows == 0;
                                results.push(DbTransactionStepResult::Executed(DbExecuteResult {
                                    affected_rows: result.affected_rows,
                                    last_insert_id: result.last_insert_id,
                                }));
                                if stop {
                                    break;
                                }
                            }
                            PreparedTransactionStep::ExecuteChecked(statement, expected) => {
                                let params = statement.resolve_transaction_params(&results, step_index)?;
                                let result = tx.execute_result(statement.sql(), mysql_params(&params)?).await?;
                                if result.affected_rows != expected {
                                    return Err(DbError::ConditionFailed { expected, actual: result.affected_rows });
                                }
                                results.push(DbTransactionStepResult::Executed(DbExecuteResult {
                                    affected_rows: result.affected_rows,
                                    last_insert_id: result.last_insert_id,
                                }));
                            }
                        }
                    }
                    Ok(results)
                })
            })
            .await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        let rows = self.query(DbStatement::new("SELECT 1 AS ok")).await?;
        if rows.len() == 1 {
            Ok(DbHealth::healthy())
        } else {
            Ok(DbHealth::unhealthy("database health query returned no rows"))
        }
    }
}

enum PreparedTransactionStep {
    Query(DbStatement),
    Execute(DbStatement),
    ExecuteChecked(DbStatement, u64),
}

impl From<DbTransactionStep> for PreparedTransactionStep {
    fn from(step: DbTransactionStep) -> Self {
        match step {
            DbTransactionStep::Query(statement) => Self::Query(statement),
            DbTransactionStep::Execute(statement) => Self::Execute(statement),
            DbTransactionStep::ExecuteChecked { statement, expected_affected_rows } => {
                Self::ExecuteChecked(statement, expected_affected_rows)
            }
        }
    }
}

fn mysql_params(values: &[DbValue]) -> DbResult<Vec<MysqlValue>> {
    values.iter().map(mysql_value).collect()
}

fn mysql_value(value: &DbValue) -> DbResult<MysqlValue> {
    match value {
        DbValue::Null => Ok(MysqlValue::NULL),
        DbValue::Bool(value) => Ok(MysqlValue::Int(i64::from(*value))),
        DbValue::I64(value) => Ok(MysqlValue::Int(*value)),
        DbValue::U64(value) => Ok(MysqlValue::UInt(*value)),
        DbValue::F64(value) => Ok(MysqlValue::Double(*value)),
        DbValue::String(value) => Ok(MysqlValue::Bytes(value.clone().into_bytes())),
        DbValue::Bytes(value) => Ok(MysqlValue::Bytes(value.clone())),
    }
}

fn row_to_db_row(row: MysqlRow) -> DbResult<DbRow> {
    let mut columns = BTreeMap::new();
    for column in row.columns_ref() {
        let name = column.name_str().to_string();
        let value = row
            .get_opt::<MysqlValue, &str>(&name)
            .transpose()
            .map_err(|err| DbError::Conversion(format!("read mysql column '{}': {}", name, err)))?
            .map(|value| mysql_value_to_db_value_for_column(value, column))
            .unwrap_or(DbValue::Null);
        columns.insert(name, value);
    }
    Ok(DbRow::new(columns))
}

fn mysql_value_to_db_value_for_column(value: MysqlValue, column: &MysqlColumn) -> DbValue {
    match value {
        MysqlValue::Bytes(value) => mysql_bytes_to_db_value(value, column),
        other => mysql_value_to_db_value(other),
    }
}

fn mysql_value_to_db_value(value: MysqlValue) -> DbValue {
    match value {
        MysqlValue::NULL => DbValue::Null,
        MysqlValue::Bytes(value) => utf8_or_bytes(value),
        MysqlValue::Int(value) => DbValue::I64(value),
        MysqlValue::UInt(value) => DbValue::U64(value),
        MysqlValue::Float(value) => DbValue::F64(value as f64),
        MysqlValue::Double(value) => DbValue::F64(value),
        MysqlValue::Date(year, month, day, hour, minute, second, micros) => DbValue::String(
            format!("{year:04}-{month:02}-{day:02} {hour:02}:{minute:02}:{second:02}.{micros:06}"),
        ),
        MysqlValue::Time(is_negative, days, hours, minutes, seconds, micros) => {
            let sign = if is_negative { "-" } else { "" };
            DbValue::String(format!(
                "{sign}{days} {hours:02}:{minutes:02}:{seconds:02}.{micros:06}"
            ))
        }
    }
}

fn mysql_bytes_to_db_value(value: Vec<u8>, column: &MysqlColumn) -> DbValue {
    if column.column_type().is_numeric_type() {
        return numeric_text_bytes_to_db_value(value);
    }
    let binary = if column.column_type().is_character_type() {
        column.character_set() == MYSQL_BINARY_CHARSET_ID
    } else {
        column.flags().contains(ColumnFlags::BINARY_FLAG)
    };
    if binary || column.column_type().is_geometry_type()
    {
        return DbValue::Bytes(value);
    }
    utf8_or_bytes(value)
}

fn numeric_text_bytes_to_db_value(value: Vec<u8>) -> DbValue {
    let text = match String::from_utf8(value) {
        Ok(text) => text,
        Err(err) => return DbValue::Bytes(err.into_bytes()),
    };
    if let Ok(value) = text.parse::<i64>() {
        return DbValue::I64(value);
    }
    if let Ok(value) = text.parse::<u64>() {
        return DbValue::U64(value);
    }
    if let Ok(value) = text.parse::<f64>() {
        return DbValue::F64(value);
    }
    DbValue::String(text)
}

fn utf8_or_bytes(value: Vec<u8>) -> DbValue {
    match String::from_utf8(value) {
        Ok(value) => DbValue::String(value),
        Err(err) => DbValue::Bytes(err.into_bytes()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn must<T>(result: DbResult<T>) -> T {
        match result {
            Ok(value) => value,
            Err(err) => panic!("expected Ok, got {}", err),
        }
    }

    #[test]
    fn db_bool_values_map_to_mysql_ints() {
        assert_eq!(must(mysql_value(&DbValue::Bool(true))), MysqlValue::Int(1));
        assert_eq!(must(mysql_value(&DbValue::Bool(false))), MysqlValue::Int(0));
    }

    #[test]
    fn mysql_int_values_map_to_db_i64() {
        assert_eq!(
            mysql_value_to_db_value(MysqlValue::Int(-1)),
            DbValue::I64(-1)
        );
    }

    #[test]
    fn mysql_date_values_map_to_stable_strings() {
        assert_eq!(
            mysql_value_to_db_value(MysqlValue::Date(2026, 1, 1, 0, 0, 0, 0)),
            DbValue::String("2026-01-01 00:00:00.000000".to_string())
        );
    }

    #[test]
    fn mysql_utf8_bytes_map_to_db_string_without_metadata() {
        assert_eq!(
            mysql_value_to_db_value(MysqlValue::Bytes(b"hello".to_vec())),
            DbValue::String("hello".to_string())
        );
    }

    #[test]
    fn binary_collation_text_columns_decode_as_strings() {
        use mysql_async::consts::{ColumnFlags, ColumnType};
        for (column_type, charset) in [
            (ColumnType::MYSQL_TYPE_VARCHAR, 65), // ascii_bin
            (ColumnType::MYSQL_TYPE_VAR_STRING, 46), // utf8mb4_bin
            (ColumnType::MYSQL_TYPE_BLOB, 46), // TEXT with utf8mb4_bin
        ] {
            let column = MysqlColumn::new(column_type)
                .with_character_set(charset).with_flags(ColumnFlags::BINARY_FLAG);
            assert_eq!(mysql_value_to_db_value_for_column(
                MysqlValue::Bytes(b"first".to_vec()), &column,
            ), DbValue::String("first".to_string()), "{column_type:?}");
        }
    }

    #[test]
    fn binary_charset_columns_preserve_utf8_bytes() {
        use mysql_async::consts::{ColumnFlags, ColumnType};
        for column_type in [ColumnType::MYSQL_TYPE_STRING,
            ColumnType::MYSQL_TYPE_VAR_STRING, ColumnType::MYSQL_TYPE_BLOB] {
            let column = MysqlColumn::new(column_type)
                .with_character_set(63).with_flags(ColumnFlags::BINARY_FLAG);
            assert_eq!(mysql_value_to_db_value_for_column(
                MysqlValue::Bytes(b"first".to_vec()), &column,
            ), DbValue::Bytes(b"first".to_vec()), "{column_type:?}");
        }
    }

    #[test]
    fn text_protocol_temporal_columns_keep_string_values() {
        use mysql_async::consts::ColumnType;
        for (column_type, text) in [
            (ColumnType::MYSQL_TYPE_DATE, "2026-10-09"),
            (ColumnType::MYSQL_TYPE_TIME, "12:34:56"),
            (ColumnType::MYSQL_TYPE_DATETIME, "2026-10-09 12:34:56"),
            (ColumnType::MYSQL_TYPE_TIMESTAMP, "2026-10-09 12:34:56"),
        ] {
            let column = MysqlColumn::new(column_type).with_character_set(63);
            assert_eq!(mysql_value_to_db_value_for_column(
                MysqlValue::Bytes(text.as_bytes().to_vec()), &column,
            ), DbValue::String(text.into()), "{column_type:?}");
        }
    }

    #[test]
    fn text_protocol_numeric_bytes_map_to_numbers() {
        assert_eq!(
            numeric_text_bytes_to_db_value(b"1".to_vec()),
            DbValue::I64(1)
        );
        assert_eq!(
            numeric_text_bytes_to_db_value(b"18446744073709551615".to_vec()),
            DbValue::U64(u64::MAX)
        );
        assert_eq!(
            numeric_text_bytes_to_db_value(b"1.25".to_vec()),
            DbValue::F64(1.25)
        );
    }

}
