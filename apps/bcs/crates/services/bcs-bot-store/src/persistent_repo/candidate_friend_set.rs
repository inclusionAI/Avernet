//! Bound JSON friend-set expansion for candidate list and search.
//!
//! The candidate stores must not build one `UNION ALL SELECT ?` per friend:
//! that shape grows the statement and its placeholder count with the friend
//! set and fails on SQLite at 501 friends. The sorted, de-duplicated friend
//! IDs are serialized once into one bound JSON array and expanded inside the
//! query by the dialect capability already used by `a2a_source.rs`.
//!
//! This reduces statement items and placeholders only. Database scanning and
//! set construction still cost O(F) for F friends. Bot UUIDs are canonical
//! lowercase identities; Rust de-duplicates the input before serialization, so
//! SQL-level DISTINCT is unnecessary and must not apply a connection collation.

use bcs_db_api::{DbSqlFlavor, DbValue};
use bcs_service_api::{ServiceError, ServiceResult};

/// `WITH` clause that exposes the bound friend IDs as `friend_uuids(bot_uuid)`.
/// An empty array naturally yields zero rows on both dialects.
pub(super) fn friend_uuids_cte<'a>(
    flavor: DbSqlFlavor,
    friend_ids: impl IntoIterator<Item = &'a String>,
) -> ServiceResult<(String, DbValue)> {
    let mut ids = friend_ids.into_iter().cloned().collect::<Vec<_>>();
    ids.sort_unstable();
    ids.dedup();
    let json = serde_json::to_string(&ids).map_err(|error| {
        ServiceError::InternalError(format!("serialize candidate friend set: {error}"))
    })?;
    let cte = match flavor {
        DbSqlFlavor::Sqlite => {
            "WITH friend_uuids AS (SELECT value AS bot_uuid FROM json_each(?)) "
        }
        DbSqlFlavor::Mysql => {
            "WITH friend_uuids AS (SELECT bot_uuid FROM JSON_TABLE(?, '$[*]' \
             COLUMNS(bot_uuid VARCHAR(256) PATH '$' ERROR ON ERROR)) AS friends) "
        }
    };
    Ok((cte.to_string(), DbValue::from(json)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn json_of(value: &DbValue) -> serde_json::Value {
        serde_json::from_str(value.as_str().expect("JSON string parameter"))
            .expect("valid friend JSON")
    }

    #[test]
    fn deduplicates_and_keeps_quoted_backslash_and_longest_ids() {
        let quoted = "quote'o\\backslash".to_string();
        let longest = "x".repeat(256);
        let alpha = "alpha".to_string();
        let (cte, value) = friend_uuids_cte(
            DbSqlFlavor::Sqlite,
            [&quoted, &longest, &quoted, &alpha],
        )
        .expect("serialize friend set");
        assert!(cte.contains("json_each(?)"));
        assert!(!cte.contains("DISTINCT"));
        assert!(!cte.contains("UNION ALL"));
        assert_eq!(
            json_of(&value),
            serde_json::json!(["alpha", quoted, longest]),
        );
        let (mysql_cte, _) = friend_uuids_cte(DbSqlFlavor::Mysql, [&longest])
            .expect("serialize mysql friend set");
        assert!(mysql_cte.contains("JSON_TABLE(?, '$[*]'"));
        assert!(!mysql_cte.contains("DISTINCT"));
        assert!(mysql_cte.contains("VARCHAR(256)"));
    }

    #[test]
    fn empty_set_is_one_empty_json_array() {
        let (_, value) = friend_uuids_cte(DbSqlFlavor::Sqlite, std::iter::empty::<&String>())
            .expect("serialize empty friend set");
        assert_eq!(json_of(&value), serde_json::json!([]));
    }
}
