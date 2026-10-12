//! Shared DbRow/DbValue plumbing for the edge-permission store modules
//! (`edge_grants` / `permission_profiles` / `permission_requests` /
//! `bcs_bots` and the authority reads).
//!
//! Pure helpers only: module-owned SQL and codecs stay in their
//! responsibility files. Read failures are surfaced as
//! `ServiceError::InternalError` (`service_db_error`); nothing here is
//! allowed to swallow errors into `Option::None`/empty results.

use bcs_db_api::{DbError, DbRow, DbValue};
use bcs_service_api::{ServiceError, ServiceResult};
use tracing::warn;

pub(super) fn required_string(row: &DbRow, column: &'static str) -> ServiceResult<String> {
    optional_string(row, column)?.ok_or_else(|| {
        ServiceError::InternalError(format!("missing edge_grants column {}", column))
    })
}

pub(super) fn optional_string(row: &DbRow, column: &'static str) -> ServiceResult<Option<String>> {
    match row.get(column) {
        None | Some(DbValue::Null) => Ok(None),
        Some(DbValue::String(value)) => Ok(Some(value.clone())),
        Some(DbValue::Bytes(value)) => String::from_utf8(value.clone())
            .map(Some)
            .map_err(|err| {
                service_db_error(
                    column,
                    DbError::Conversion(format!(
                        "column '{}' is not valid UTF-8: {}",
                        column, err
                    )),
                )
            }),
        Some(other) => Err(service_db_error(
            column,
            DbError::Conversion(format!(
                "column '{}' is not a string: {:?}",
                column, other
            )),
        )),
    }
}

pub(super) fn optional_timestamp_text(
    row: &DbRow,
    column: &'static str,
) -> ServiceResult<Option<String>> {
    match row.get(column) {
        None | Some(DbValue::Null) => Ok(None),
        Some(DbValue::String(value)) => Ok(Some(value.clone())),
        Some(DbValue::Bytes(value)) => String::from_utf8(value.clone())
            .map(Some)
            .map_err(|err| {
                service_db_error(
                    column,
                    DbError::Conversion(format!(
                        "column '{}' is not valid UTF-8: {}",
                        column, err
                    )),
                )
            }),
        Some(other) => Err(service_db_error(
            column,
            DbError::Conversion(format!(
                "column '{}' is not a timestamp string: {:?}",
                column, other
            )),
        )),
    }
}

pub(super) fn required_u64(row: &DbRow, column: &'static str) -> ServiceResult<u64> {
    let value = row
        .get_i64(column)
        .map_err(|err| service_db_error(column, err))?
        .ok_or_else(|| {
            ServiceError::InternalError(format!("missing edge_permission column {}", column))
        })?;
    if value < 0 {
        return Err(ServiceError::InternalError(format!(
            "negative edge_permission column {}",
            column
        )));
    }
    Ok(value as u64)
}

pub(super) fn optional_u64(row: &DbRow, column: &'static str) -> ServiceResult<Option<u64>> {
    Ok(row
        .get_i64(column)
        .map_err(|err| service_db_error(column, err))?
        .and_then(|value| if value < 0 { None } else { Some(value as u64) }))
}

pub(super) fn parse_json_opt(value: &Option<String>) -> ServiceResult<Option<serde_json::Value>> {
    match value {
        None => Ok(None),
        Some(s) if s.is_empty() => Ok(None),
        Some(s) => serde_json::from_str::<serde_json::Value>(s).map(Some).map_err(|err| {
            ServiceError::InternalError(format!("edge_grants json parse: {}", err))
        }),
    }
}

/// Parse a DB-managed timestamp back to epoch milliseconds (UTC).
///
/// Accepts `YYYY-MM-DD HH:MM:SS` (SQLite `CURRENT_TIMESTAMP` / MySQL `timestamp`)
/// and the ISO `T` separator variant, with optional fractional seconds. Returns
/// `None` for an unparseable/empty value (e.g. NULL ⇒ already `None` upstream).
pub(super) fn parse_timestamp_epoch_ms(value: &str) -> Option<u64> {
    let s = value.trim();
    if s.is_empty() {
        return None;
    }
    let s = s.replacen('T', " ", 1);
    let mut parts = s.split(' ');
    let date = parts.next()?;
    let time = parts.next()?;
    let d: Vec<&str> = date.split('-').collect();
    if d.len() != 3 {
        return None;
    }
    let t_main = time.split('.').next()?;
    let t: Vec<&str> = t_main.split(':').collect();
    if t.len() != 3 {
        return None;
    }
    let (y, mo, dy) = (
        d[0].parse::<i64>().ok()?,
        d[1].parse::<i64>().ok()?,
        d[2].parse::<i64>().ok()?,
    );
    let (h, mi, se) = (
        t[0].parse::<u64>().ok()?,
        t[1].parse::<u64>().ok()?,
        t[2].parse::<u64>().ok()?,
    );
    if !(1..=12).contains(&mo) || !(1..=31).contains(&dy) {
        return None;
    }
    // Howard Hinnant's days_from_civil — counts days since 1970-01-01 (UTC).
    let y = if mo <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = (y - era * 400) as u64;
    let doy = ((153 * (if mo > 2 { mo - 3 } else { mo + 9 }) + 2) / 5 + dy - 1) as u64;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = (era * 146097 + doe as i64 - 719468) as i64;
    if days < 0 {
        return None;
    }
    let epoch_ms = (days as u64) * 86_400_000 + h * 3_600_000 + mi * 60_000 + se * 1_000;
    Some(epoch_ms)
}

pub(super) fn json_to_db_value(value: &Option<serde_json::Value>) -> DbValue {
    match value {
        None => DbValue::Null,
        Some(v) => match serde_json::to_string(v) {
            Ok(s) => DbValue::from(s),
            Err(err) => {
                warn!(error = %err, "edge_grants: failed to serialize json, storing NULL");
                DbValue::Null
            }
        },
    }
}

/// Map a driver-level error onto the storage/internal failure branch.
///
/// Authority business errors (`AuthorityError`) are decided by strict
/// decoding in the store modules, never here.
pub(super) fn service_db_error(operation: &'static str, err: DbError) -> ServiceError {
    ServiceError::InternalError(format!("edge_grants db {}: {}", operation, err))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_timestamp_epoch_ms_converts_db_timestamp() {
        // SQLite CURRENT_TIMESTAMP / MySQL `timestamp` format, UTC.
        // 2026-08-21 00:00:00 UTC == 1787270400_000 ms.
        assert_eq!(parse_timestamp_epoch_ms("2026-08-21 00:00:00"), Some(1_787_270_400_000));
        assert_eq!(parse_timestamp_epoch_ms("2026-08-21T12:34:56"), Some(1_787_315_696_000));
        // Fractional seconds tolerated; ISO 'T' separator accepted.
        assert_eq!(parse_timestamp_epoch_ms("2026-01-01 00:00:00.000"), Some(1_767_225_600_000));
        // Empty / unparseable / pre-epoch → None (NULL upstream becomes None).
        assert_eq!(parse_timestamp_epoch_ms(""), None);
        assert_eq!(parse_timestamp_epoch_ms("not-a-date"), None);
    }
}