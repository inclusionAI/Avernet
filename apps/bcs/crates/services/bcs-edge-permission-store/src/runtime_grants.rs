//! Fallible runtime grant reads. SQL and decoding stay store-owned.
use super::*;

impl DbEdgeGrantStore {
    pub(super) async fn read_runtime_grants_checked(
        &self, from: &str, to: &str, env: &str,
    ) -> ServiceResult<Vec<EdgeGrant>> {
        // Indexed prefilters plus byte-exact residuals also protect installations
        // whose identity collation has not yet been upgraded. Unknown kinds are
        // deliberately decoded (and rejected), never silently hidden by an IN
        // allowlist. Only known authority metadata is excluded at SQL level.
        let exact = match self.flavor() {
            EdgeGrantSqlFlavor::Mysql =>
                "CAST(from_id AS BINARY) = CAST(? AS BINARY) \
                 AND CAST(to_id AS BINARY) = CAST(? AS BINARY) \
                 AND CAST(env AS BINARY) = CAST(? AS BINARY)",
            EdgeGrantSqlFlavor::Sqlite =>
                "from_id COLLATE BINARY = ? AND to_id COLLATE BINARY = ? \
                 AND env COLLATE BINARY = ?",
        };
        let runtime_kind = match self.flavor() {
            EdgeGrantSqlFlavor::Mysql => "CAST(grant_kind AS BINARY) NOT IN (CAST('owner' AS BINARY), CAST('manager' AS BINARY))",
            EdgeGrantSqlFlavor::Sqlite => "grant_kind COLLATE BINARY NOT IN ('owner', 'manager')",
        };
        let sql = format!(
            "SELECT id, env, from_id, to_id, grant_kind, grant_ref_id, rules, \
                    status, originator_policy_type, originator_policy_data \
             FROM edge_grants \
             WHERE from_id = ? AND to_id = ? AND env = ? AND status = 'approved' \
               AND {runtime_kind} AND {exact} ORDER BY id"
        );
        let values = [from, to, env, from, to, env].into_iter().map(DbValue::from).collect();
        let rows = self.db.query(DbStatement::with_params(sql, values)).await
            .map_err(|err| service_db_error("list_runtime_grants_checked", err))?;
        // collect<Result<_>> fails the whole operation for even one damaged row.
        rows.iter().map(row_to_edge_grant).collect()
    }
}
