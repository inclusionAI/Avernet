//! Legacy binding disable/restore must also fence the physical Bot. The normal
//! projection goes through audited retirement, but trusted direct port callers
//! must not leave an old approval reusable after a disabled -> enabled ABA.
use super::*;
use bcs_db_api::{DbTransactionOptions, DbTransactionParam as Param, DbTransactionStep as Step};
impl DbProviderStore {
    pub(super) async fn insert_binding_identity(
        &self,
        binding: ProviderBotBinding,
    ) -> ServiceResult<()> {
        let env = resolve_env();
        let steps = vec![
            Step::ExecuteChecked {
                statement: DbStatement::with_params(
                    Self::insert_binding_sql(),
                    vec![
                        binding.bot_uuid.as_str().into(),
                        binding.provider_id.as_str().into(),
                        binding.provider_bot_ref.as_str().into(),
                        env.as_str().into(),
                        binding.disabled.into(),
                        binding.webhook_url.clone().into(),
                    ],
                ),
                expected_affected_rows: 1,
            },
            Step::Execute(DbStatement::with_params(
                "UPDATE bcs_bots SET agent_invoke_identity_revision=agent_invoke_identity_revision WHERE bot_uuid=? AND env=?",
                vec![binding.bot_uuid.as_str().into(), env.as_str().into()],
            )),
            Step::Query(DbStatement::with_params(
                "SELECT 1 AS ok WHERE NOT EXISTS (SELECT 1 FROM bcs_bots WHERE bot_uuid=? AND env=? AND actor_kind='bot' AND (agent_invoke_identity_revision<1 OR agent_invoke_identity_revision>=9223372036854775807))",
                vec![binding.bot_uuid.as_str().into(), env.as_str().into()],
            )),
            Step::Execute(DbStatement::with_transaction_params(
                "UPDATE bcs_bots SET agent_invoke_identity_revision=agent_invoke_identity_revision+1 WHERE bot_uuid=? AND env=? AND actor_kind='bot' AND ?=1",
                vec![
                    Param::value(binding.bot_uuid.as_str()),
                    Param::value(env.as_str()),
                    Param::query_result(2, 0, "ok"),
                ],
            )),
        ];
        self.db
            .transaction_with_options(
                steps,
                DbTransactionOptions::with_deadline(std::time::Duration::from_secs(5)),
            )
            .await
            .map_err(|e| service_db_error("insert_provider_bot_binding", e))?;
        self.bindings
            .invalidate(&format!("{env}:{}", binding.bot_uuid));
        Ok(())
    }

    pub(super) async fn update_binding_identity_disabled(
        &self,
        bot_uuid: &str,
        disabled: bool,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let env = resolve_env();
        let suffix = if self.flavor == DbSqlFlavor::Mysql {
            " FOR UPDATE"
        } else {
            ""
        };
        // Same binding -> physical Bot lock order as retirement/reattachment.
        // No scope/edge locks, external calls, cache publication or retry here.
        let changed = "EXISTS (SELECT 1 FROM bcs_provider_bot_bindings p WHERE p.bot_uuid=bcs_bots.bot_uuid AND p.env=bcs_bots.env AND p.disabled<>?)";
        let steps = vec![
            Step::Query(DbStatement::with_params(
                format!(
                    "SELECT bot_uuid FROM bcs_provider_bot_bindings WHERE bot_uuid=? AND env=?{suffix}"
                ),
                vec![bot_uuid.into(), env.as_str().into()],
            )),
            Step::Execute(DbStatement::with_params(
                "UPDATE bcs_bots SET agent_invoke_identity_revision=agent_invoke_identity_revision WHERE bot_uuid=? AND env=?",
                vec![bot_uuid.into(), env.as_str().into()],
            )),
            Step::Query(DbStatement::with_params(
                format!(
                    "SELECT 1 AS ok WHERE NOT EXISTS (SELECT 1 FROM bcs_bots WHERE bot_uuid=? AND env=? AND actor_kind='bot' AND {changed} AND (agent_invoke_identity_revision<1 OR agent_invoke_identity_revision>=9223372036854775807))"
                ),
                vec![bot_uuid.into(), env.as_str().into(), disabled.into()],
            )),
            Step::Execute(DbStatement::with_transaction_params(
                format!(
                    "UPDATE bcs_bots SET agent_invoke_identity_revision=agent_invoke_identity_revision+1 WHERE bot_uuid=? AND env=? AND actor_kind='bot' AND {changed} AND ?=1"
                ),
                vec![
                    Param::value(bot_uuid),
                    Param::value(env.as_str()),
                    Param::value(disabled),
                    Param::query_result(2, 0, "ok"),
                ],
            )),
            Step::Execute(DbStatement::with_transaction_params(
                format!(
                    "UPDATE bcs_provider_bot_bindings SET disabled=?,{} WHERE bot_uuid=? AND env=? AND ?=1",
                    self.now_modified_clause()
                ),
                vec![
                    Param::value(disabled),
                    Param::value(bot_uuid),
                    Param::value(env.as_str()),
                    Param::query_result(2, 0, "ok"),
                ],
            )),
        ];
        self.db
            .transaction_with_options(
                steps,
                DbTransactionOptions::with_deadline(std::time::Duration::from_secs(5)),
            )
            .await
            .map_err(|e| service_db_error("update_provider_binding_disabled", e))?;
        self.bindings.invalidate(&format!("{env}:{bot_uuid}"));
        self.get_binding_by_bot_uuid(bot_uuid).await
    }
}
