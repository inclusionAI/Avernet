use bcs_db_api::{DbSqlFlavor, DbValue};
use bcs_service_api::port::repo::EventRepoError;
use chrono::{DateTime, TimeZone, Utc};

const TIMESTAMP_PARAM_MARKER: &str = "__bcs_timestamp_ms__";

pub(crate) fn sql_with_timestamp_params(flavor: DbSqlFlavor, sql: &str) -> String {
    let expression = match flavor {
        DbSqlFlavor::Mysql => "FROM_UNIXTIME(? / 1000.0)",
        DbSqlFlavor::Sqlite => "?",
    };
    sql.replace(TIMESTAMP_PARAM_MARKER, expression)
}

pub(crate) fn timestamp_value_from_ms(
    flavor: DbSqlFlavor,
    timestamp_ms: u64,
) -> Result<DbValue, EventRepoError> {
    let timestamp_ms = i64::try_from(timestamp_ms).map_err(|_| {
        EventRepoError::InvalidInput("timestamp is outside supported range".to_string())
    })?;
    timestamp_value(flavor, timestamp_ms)
}

pub(crate) fn optional_timestamp_value_from_ms(
    flavor: DbSqlFlavor,
    timestamp_ms: Option<u64>,
) -> Result<DbValue, EventRepoError> {
    timestamp_ms
        .map(|timestamp_ms| timestamp_value_from_ms(flavor, timestamp_ms))
        .transpose()
        .map(|value| value.unwrap_or(DbValue::Null))
}

pub(crate) fn timestamp_value_from_rfc3339(
    flavor: DbSqlFlavor,
    timestamp: &str,
) -> Result<DbValue, EventRepoError> {
    let parsed = DateTime::parse_from_rfc3339(timestamp).map_err(|error| {
        EventRepoError::InvalidInput(format!("invalid RFC3339 timestamp {timestamp:?}: {error}"))
    })?;
    timestamp_value(flavor, parsed.timestamp_millis())
}

fn timestamp_value(flavor: DbSqlFlavor, timestamp_ms: i64) -> Result<DbValue, EventRepoError> {
    let timestamp = Utc
        .timestamp_millis_opt(timestamp_ms)
        .single()
        .ok_or_else(|| {
            EventRepoError::InvalidInput("timestamp is outside supported range".to_string())
        })?;
    Ok(match flavor {
        // Bind the epoch rather than a timezone-free UTC string. FROM_UNIXTIME
        // renders it in the current MySQL session timezone before TIMESTAMP
        // converts it back to the same instant for storage.
        DbSqlFlavor::Mysql => DbValue::from(timestamp_ms),
        DbSqlFlavor::Sqlite => {
            DbValue::from(timestamp.format("%Y-%m-%d %H:%M:%S%.3f").to_string())
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const TIMESTAMP_MS: u64 = 1_756_367_457_123;

    #[test]
    fn mysql_binds_epoch_inside_from_unixtime() {
        assert_eq!(
            sql_with_timestamp_params(
                DbSqlFlavor::Mysql,
                "VALUES (__bcs_timestamp_ms__)"
            ),
            "VALUES (FROM_UNIXTIME(? / 1000.0))"
        );
        assert_eq!(
            timestamp_value_from_ms(DbSqlFlavor::Mysql, TIMESTAMP_MS).expect("timestamp"),
            DbValue::from(i64::try_from(TIMESTAMP_MS).expect("signed timestamp"))
        );
    }

    #[test]
    fn sqlite_keeps_canonical_utc_text() {
        assert_eq!(
            sql_with_timestamp_params(
                DbSqlFlavor::Sqlite,
                "VALUES (__bcs_timestamp_ms__)"
            ),
            "VALUES (?)"
        );
        assert_eq!(
            timestamp_value_from_ms(DbSqlFlavor::Sqlite, TIMESTAMP_MS).expect("timestamp"),
            DbValue::from("2025-08-28 07:50:57.123")
        );
    }

    #[test]
    fn rfc3339_offsets_normalize_to_the_same_epoch() {
        assert_eq!(
            timestamp_value_from_rfc3339(DbSqlFlavor::Mysql, "2026-08-28T15:50:57+08:00")
                .expect("offset timestamp"),
            timestamp_value_from_rfc3339(DbSqlFlavor::Mysql, "2026-08-28T07:50:57Z")
                .expect("UTC timestamp")
        );
    }
}
