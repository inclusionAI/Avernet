//! SQL normalization for the canonical object and legacy string-wrapped object.

use super::{DbSqlFlavor, ServiceError, ServiceResult, Value};

/// `column` must be a static, trusted SQL identifier, never request input.
pub(super) fn bot_info_object_sql(flavor: DbSqlFlavor, column: &str) -> String {
    let decoded = match flavor {
        DbSqlFlavor::Mysql => format!("JSON_UNQUOTE({column})"),
        DbSqlFlavor::Sqlite => format!("json_extract({column}, '$')"),
    };
    // Nested CASE guards invalid JSON without relying on AND short-circuiting.
    // All CASE branches are text. Parsing AFTER CASE is essential: OceanBase
    // coerces object text to a JSON string if an ELSE branch is JSON_OBJECT().
    format!(
        "JSON_EXTRACT(CASE WHEN JSON_VALID({column}) THEN \
           CASE WHEN JSON_VALID({decoded}) THEN \
             CASE WHEN LOWER(JSON_TYPE({decoded})) = 'object' \
               THEN {decoded} ELSE '{{}}' END \
             ELSE '{{}}' END \
           ELSE '{{}}' END, '$')"
    )
}

pub(super) fn bot_info_json_set(
    flavor: DbSqlFlavor,
    updates: Vec<(&'static str, serde_json::Value)>,
) -> ServiceResult<(String, Vec<Value>)> {
    let mut expressions = Vec::with_capacity(updates.len());
    let mut params = Vec::with_capacity(updates.len());
    for (path, value) in updates {
        expressions.push(format!("'{path}', json_extract(?, '$')"));
        params.push(Value::from(serde_json::to_string(&value).map_err(|error| {
            ServiceError::InternalError(error.to_string())
        })?));
    }
    let object = bot_info_object_sql(flavor, "bot_info");
    Ok((format!("bot_info = JSON_SET({object}, {})", expressions.join(", ")), params))
}
