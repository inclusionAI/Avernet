use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbError, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
use bcs_domain::{Organization, OrganizationMember};
use bcs_service_api::port::repo::{
    CreateOrganizationRecord, ListOrganizationMembersQuery, ListOrganizationsQuery,
    OrganizationRepoPort, UpdateOrganizationRecord, UpsertOrganizationMemberRecord,
};
use bcs_service_api::{ServiceError, ServiceResult};

pub mod memory;

pub use memory::MemoryOrganizationRepo;

#[derive(Clone)]
pub struct DbOrganizationStore {
    db: Arc<dyn DbPlugin>,
    flavor: DbSqlFlavor,
}

impl DbOrganizationStore {
    pub fn sqlite(db: Arc<dyn DbPlugin>) -> Self {
        Self {
            db,
            flavor: DbSqlFlavor::Sqlite,
        }
    }

    pub fn mysql(db: Arc<dyn DbPlugin>) -> Self {
        Self {
            db,
            flavor: DbSqlFlavor::Mysql,
        }
    }

    fn organization_select(&self) -> String {
        format!(
            "SELECT env, code, name, description, managing_provider_id, disabled, \
             {} AS created_at, {} AS updated_at FROM bcs_organizations",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified")
        )
    }

    fn member_select(&self) -> String {
        format!(
            "SELECT env, organization_code, bot_uuid, role, disabled, \
             {} AS created_at, {} AS updated_at FROM bcs_organization_members",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified")
        )
    }

    async fn execute(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<u64> {
        self.db
            .execute(statement)
            .await
            .map(|result| result.affected_rows)
            .map_err(|error| service_db_error(operation, error))
    }
}

#[async_trait]
impl OrganizationRepoPort for DbOrganizationStore {
    async fn create_organization(
        &self,
        input: CreateOrganizationRecord,
    ) -> ServiceResult<Organization> {
        let env = input.env.clone();
        let code = input.code.clone();
        let result = self
            .db
            .execute(DbStatement::with_params(
                "INSERT INTO bcs_organizations \
                 (env, code, name, description, managing_provider_id, disabled) \
                 VALUES (?, ?, ?, ?, ?, ?)",
                vec![
                    DbValue::from(input.env),
                    DbValue::from(input.code),
                    DbValue::from(input.name),
                    DbValue::from(input.description),
                    DbValue::from(input.managing_provider_id),
                    DbValue::from(0_i64),
                ],
            ))
            .await;
        if let Err(error) = result {
            if error.is_duplicate_key() {
                return Err(ServiceError::Conflict(
                    "organization code already exists".to_string(),
                ));
            }
            return Err(service_db_error("create_organization", error));
        }
        self.get_organization(&env, &code)
            .await?
            .ok_or_else(|| ServiceError::InternalError(
                "organization db create_organization: inserted row not found".to_string(),
            ))
    }

    async fn get_organization(
        &self,
        env: &str,
        code: &str,
    ) -> ServiceResult<Option<Organization>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                format!("{} WHERE env = ? AND code = ? LIMIT 1", self.organization_select()),
                vec![DbValue::from(env), DbValue::from(code)],
            ))
            .await
            .map_err(|error| service_db_error("get_organization", error))?;
        rows.into_iter()
            .next()
            .map(row_to_organization)
            .transpose()
    }

    async fn update_organization(
        &self,
        input: UpdateOrganizationRecord,
    ) -> ServiceResult<Option<Organization>> {
        let mut assignments = Vec::new();
        let mut params = Vec::new();
        if let Some(name) = input.name {
            assignments.push("name = ?");
            params.push(DbValue::from(name));
        }
        if let Some(description) = input.description {
            assignments.push("description = ?");
            params.push(DbValue::from(description));
        }
        if let Some(disabled) = input.disabled {
            assignments.push("disabled = ?");
            params.push(DbValue::from(if disabled { 1_i64 } else { 0_i64 }));
        }
        if assignments.is_empty() {
            return self.get_organization(&input.env, &input.code).await;
        }
        assignments.push(self.flavor.set_modified_now());
        params.push(DbValue::from(input.env.as_str()));
        params.push(DbValue::from(input.code.as_str()));
        self.execute(
            "update_organization",
            DbStatement::with_params(
                format!(
                    "UPDATE bcs_organizations SET {} WHERE env = ? AND code = ?",
                    assignments.join(", ")
                ),
                params,
            ),
        )
        .await?;
        self.get_organization(&input.env, &input.code).await
    }

    async fn list_organizations(
        &self,
        query: ListOrganizationsQuery,
    ) -> ServiceResult<Vec<Organization>> {
        let mut sql = format!(
            "{} WHERE env = ? AND managing_provider_id = ?",
            self.organization_select()
        );
        let params = vec![
            DbValue::from(query.env),
            DbValue::from(query.managing_provider_id),
        ];
        if !query.include_disabled {
            sql.push_str(" AND disabled = 0");
        }
        let rows = self
            .db
            .query(DbStatement::with_params(sql, params))
            .await
            .map_err(|error| service_db_error("list_organizations", error))?;
        rows.into_iter().map(row_to_organization).collect()
    }

    async fn upsert_member(
        &self,
        input: UpsertOrganizationMemberRecord,
    ) -> ServiceResult<OrganizationMember> {
        let sql = match self.flavor {
            DbSqlFlavor::Sqlite => {
                "INSERT INTO bcs_organization_members \
                 (env, organization_code, bot_uuid, role, disabled) VALUES (?, ?, ?, ?, 0) \
                 ON CONFLICT(env, organization_code, bot_uuid) DO UPDATE SET \
                 role = excluded.role, disabled = 0, gmt_modified = CURRENT_TIMESTAMP"
            }
            DbSqlFlavor::Mysql => {
                "INSERT INTO bcs_organization_members \
                 (env, organization_code, bot_uuid, role, disabled) VALUES (?, ?, ?, ?, 0) \
                 ON DUPLICATE KEY UPDATE role = VALUES(role), disabled = 0, gmt_modified = NOW()"
            }
        };
        let env = input.env.clone();
        let organization_code = input.organization_code.clone();
        let bot_uuid = input.bot_uuid.clone();
        self.execute(
            "upsert_member",
            DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(input.env),
                    DbValue::from(input.organization_code),
                    DbValue::from(input.bot_uuid),
                    DbValue::from(input.role),
                ],
            ),
        )
        .await?;
        self.get_member(&env, &organization_code, &bot_uuid)
            .await?
            .ok_or_else(|| ServiceError::InternalError(
                "organization db upsert_member: upserted row not found".to_string(),
            ))
    }

    async fn get_member(
        &self,
        env: &str,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMember>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                format!(
                    "{} WHERE env = ? AND organization_code = ? AND bot_uuid = ? LIMIT 1",
                    self.member_select()
                ),
                vec![
                    DbValue::from(env),
                    DbValue::from(organization_code),
                    DbValue::from(bot_uuid),
                ],
            ))
            .await
            .map_err(|error| service_db_error("get_member", error))?;
        rows.into_iter().next().map(row_to_member).transpose()
    }

    async fn set_member_disabled(
        &self,
        env: &str,
        organization_code: &str,
        bot_uuid: &str,
        disabled: bool,
    ) -> ServiceResult<Option<OrganizationMember>> {
        self.execute(
            "set_member_disabled",
            DbStatement::with_params(
                format!(
                    "UPDATE bcs_organization_members SET disabled = ?, {} \
                     WHERE env = ? AND organization_code = ? AND bot_uuid = ?",
                    self.flavor.set_modified_now()
                ),
                vec![
                    DbValue::from(if disabled { 1_i64 } else { 0_i64 }),
                    DbValue::from(env),
                    DbValue::from(organization_code),
                    DbValue::from(bot_uuid),
                ],
            ),
        )
        .await?;
        self.get_member(env, organization_code, bot_uuid).await
    }

    async fn list_members(
        &self,
        query: ListOrganizationMembersQuery,
    ) -> ServiceResult<Vec<OrganizationMember>> {
        let mut sql = format!(
            "{} WHERE env = ? AND organization_code = ?",
            self.member_select()
        );
        let mut params = vec![
            DbValue::from(query.env),
            DbValue::from(query.organization_code),
        ];
        if !query.include_disabled {
            sql.push_str(" AND disabled = 0");
        }
        if let Some(role) = query.role {
            sql.push_str(" AND role = ?");
            params.push(DbValue::from(role));
        }
        let rows = self
            .db
            .query(DbStatement::with_params(sql, params))
            .await
            .map_err(|error| service_db_error("list_members", error))?;
        rows.into_iter().map(row_to_member).collect()
    }
}

fn row_to_organization(row: DbRow) -> ServiceResult<Organization> {
    Ok(Organization {
        env: required_string(&row, "env")?,
        code: required_string(&row, "code")?,
        name: required_string(&row, "name")?,
        description: optional_string(&row, "description")?,
        managing_provider_id: required_string(&row, "managing_provider_id")?,
        disabled: bool_column(&row, "disabled")?,
        created_at: timestamp_millis(&row, "created_at")?,
        updated_at: timestamp_millis(&row, "updated_at")?,
    })
}

fn row_to_member(row: DbRow) -> ServiceResult<OrganizationMember> {
    Ok(OrganizationMember {
        env: required_string(&row, "env")?,
        organization_code: required_string(&row, "organization_code")?,
        bot_uuid: required_string(&row, "bot_uuid")?,
        role: optional_string(&row, "role")?,
        disabled: bool_column(&row, "disabled")?,
        created_at: timestamp_millis(&row, "created_at")?,
        updated_at: timestamp_millis(&row, "updated_at")?,
    })
}

fn required_string(row: &DbRow, column: &'static str) -> ServiceResult<String> {
    row.get_string(column)
        .map_err(|error| service_db_error(column, error))?
        .ok_or_else(|| ServiceError::InternalError(format!(
            "organization db row: missing column {}",
            column
        )))
}

fn optional_string(row: &DbRow, column: &'static str) -> ServiceResult<Option<String>> {
    row.get_string(column)
        .map_err(|error| service_db_error(column, error))
}

fn bool_column(row: &DbRow, column: &'static str) -> ServiceResult<bool> {
    row.get_bool(column)
        .map_err(|error| service_db_error(column, error))?
        .ok_or_else(|| ServiceError::InternalError(format!(
            "organization db row: missing column {}",
            column
        )))
}

fn timestamp_millis(row: &DbRow, column: &'static str) -> ServiceResult<u64> {
    let seconds = row
        .get_i64(column)
        .map_err(|error| service_db_error(column, error))?
        .ok_or_else(|| ServiceError::InternalError(format!(
            "organization db row: missing column {}",
            column
        )))?;
    Ok(seconds.max(0) as u64 * 1000)
}

fn service_db_error(operation: &'static str, error: DbError) -> ServiceError {
    ServiceError::InternalError(format!("organization db {}: {}", operation, error))
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;
    use std::sync::Mutex;

    use bcs_db_api::{
        DbExecuteResult, DbHealth, DbResult, DbTransactionStep, DbTransactionStepResult,
    };

    use super::*;

    struct RecordingDbPlugin {
        execute_error: Mutex<Option<DbError>>,
        executed: Mutex<Vec<DbStatement>>,
        query_rows: Vec<DbRow>,
    }

    impl RecordingDbPlugin {
        fn with_rows(query_rows: Vec<DbRow>) -> Self {
            Self {
                execute_error: Mutex::new(None),
                executed: Mutex::new(Vec::new()),
                query_rows,
            }
        }

        fn failing(error: DbError) -> Self {
            Self {
                execute_error: Mutex::new(Some(error)),
                executed: Mutex::new(Vec::new()),
                query_rows: Vec::new(),
            }
        }
    }

    #[async_trait]
    impl DbPlugin for RecordingDbPlugin {
        async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
            Ok(self.query_rows.clone())
        }

        async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
            self.executed
                .lock()
                .expect("recording db executed lock")
                .push(statement);
            if let Some(error) = self
                .execute_error
                .lock()
                .expect("recording db error lock")
                .take()
            {
                return Err(error);
            }
            Ok(DbExecuteResult {
                affected_rows: 1,
                last_insert_id: None,
            })
        }

        async fn transaction(
            &self,
            _steps: Vec<DbTransactionStep>,
        ) -> DbResult<Vec<DbTransactionStepResult>> {
            Err(DbError::Unsupported(
                "recording db transactions are not used".to_string(),
            ))
        }

        async fn health_check(&self) -> DbResult<DbHealth> {
            Ok(DbHealth::healthy())
        }
    }

    fn member_row() -> DbRow {
        DbRow::new(BTreeMap::from([
            ("env".to_string(), DbValue::from("contract")),
            (
                "organization_code".to_string(),
                DbValue::from("promo-2026"),
            ),
            ("bot_uuid".to_string(), DbValue::from("bot-b")),
            ("role".to_string(), DbValue::from("traffic_analyst")),
            ("disabled".to_string(), DbValue::from(0_i64)),
            ("created_at".to_string(), DbValue::from(1_i64)),
            ("updated_at".to_string(), DbValue::from(2_i64)),
        ]))
    }

    fn create_record() -> CreateOrganizationRecord {
        CreateOrganizationRecord {
            env: "contract".to_string(),
            code: "promo-2026".to_string(),
            name: "Promo 2026".to_string(),
            description: Some("description".to_string()),
            managing_provider_id: "provider-a".to_string(),
        }
    }

    #[tokio::test]
    async fn mysql_member_upsert_uses_bound_values_and_mysql_conflict_syntax() {
        let db = Arc::new(RecordingDbPlugin::with_rows(vec![member_row()]));
        let repo = DbOrganizationStore::mysql(db.clone());

        let member = repo
            .upsert_member(UpsertOrganizationMemberRecord {
                env: "contract".to_string(),
                organization_code: "promo-2026".to_string(),
                bot_uuid: "bot-b".to_string(),
                role: Some("traffic_analyst".to_string()),
            })
            .await
            .expect("mysql member upsert");

        assert_eq!(member.role.as_deref(), Some("traffic_analyst"));
        let statements = db.executed.lock().expect("recorded statements lock");
        assert_eq!(statements.len(), 1);
        assert!(statements[0].sql().contains("ON DUPLICATE KEY UPDATE"));
        assert_eq!(
            statements[0].params(),
            &[
                DbValue::from("contract"),
                DbValue::from("promo-2026"),
                DbValue::from("bot-b"),
                DbValue::from("traffic_analyst"),
            ]
        );
    }

    #[tokio::test]
    async fn duplicate_create_maps_to_organization_conflict() {
        let db = Arc::new(RecordingDbPlugin::failing(DbError::Backend(
            "1062 Duplicate entry 'contract-promo-2026'".to_string(),
        )));
        let result = DbOrganizationStore::mysql(db)
            .create_organization(create_record())
            .await;

        assert!(matches!(
            result,
            Err(ServiceError::Conflict(message))
                if message == "organization code already exists"
        ));
    }

    #[tokio::test]
    async fn non_duplicate_write_failure_is_propagated() {
        let db = Arc::new(RecordingDbPlugin::failing(DbError::Backend(
            "write unavailable".to_string(),
        )));
        let result = DbOrganizationStore::sqlite(db)
            .set_member_disabled("contract", "promo-2026", "bot-b", true)
            .await;

        assert!(matches!(
            result,
            Err(ServiceError::InternalError(message))
                if message.contains("organization db set_member_disabled")
                    && message.contains("write unavailable")
        ));
    }
}
