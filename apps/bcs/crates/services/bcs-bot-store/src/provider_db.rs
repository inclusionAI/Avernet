//! Database Provider records, credentials and organization candidates.
use super::*;

#[async_trait]
impl ProviderRepoPort for DbProviderStore {
    async fn get_caller_capabilities(
        &self,
        provider_id: &str,
    ) -> ServiceResult<Option<ProviderCallerCapabilities>> {
        let env = resolve_env();
        // Deliberately uncached: capability projections must observe
        // committed rows immediately, including updates committed by other
        // instances, where the ordinary provider cache may hold an older
        // record still marked enabled.
        let sql = "SELECT config, disabled FROM bcs_providers \
                  WHERE provider_id = ? AND env = ? LIMIT 1";
        let rows = self
            .query(
                "get_caller_capabilities",
                DbStatement::with_params(
                    sql,
                    vec![DbValue::from(provider_id), DbValue::from(env.as_str())],
                ),
            )
            .await?;
        let Some(row) = rows.first() else {
            return Err(ServiceError::ProviderNotFound(provider_id.to_string()));
        };
        if row_bool(row, "disabled") {
            return Err(caller_capabilities_unavailable());
        }
        let config = optional_string(row, "config").unwrap_or_default();
        if config.trim().is_empty() {
            // Legacy row without a config object: never declared.
            return Ok(None);
        }
        let root: serde_json::Value = serde_json::from_str(&config)
            .map_err(|_| invalid_provider_caller_capabilities())?;
        if !root.is_object() {
            return Err(invalid_provider_caller_capabilities());
        }
        match root.get("caller_capabilities") {
            None => Ok(None),
            Some(serde_json::Value::Null) => Err(invalid_provider_caller_capabilities()),
            Some(value) => serde_json::from_value::<ProviderCallerCapabilities>(value.clone())
                .map(Some)
                .map_err(|_| invalid_provider_caller_capabilities()),
        }
    }


    async fn insert_provider(&self, provider: ProviderRecord) -> ServiceResult<()> {
        let env = resolve_env();
        self.execute_insert(
            "insert_provider",
            DbStatement::with_params(
                Self::insert_provider_sql(),
                vec![
                    DbValue::from(provider.provider_id.as_str()),
                    DbValue::from(env.as_str()),
                    DbValue::from(provider.name.as_str()),
                    DbValue::from(provider.config.as_str()),
                    DbValue::from(provider.disabled),
                    DbValue::from(provider.created_by.as_str()),
                    DbValue::from(provider.owners.as_str()),
                    provider.slug.clone().map(DbValue::from).unwrap_or(DbValue::Null),
                ],
            ),
        )
        .await?;
        self.providers.invalidate(&format!("{}:{}", env, provider.provider_id));
        Ok(())
    }

    async fn get_provider(&self, provider_id: &str) -> ServiceResult<Option<ProviderRecord>> {
        let env = resolve_env();
        self.providers.get(&format!("{env}:{provider_id}"), || async {
        let sql = format!(
            "SELECT provider_id, slug, name, config, disabled, created_by, owners, {ts} \
             FROM bcs_providers WHERE provider_id = ? AND env = ? LIMIT 1",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "get_provider",
                DbStatement::with_params(
                    sql,
                    vec![DbValue::from(provider_id), DbValue::from(env.as_str())],
                ),
            )
            .await?;
        rows.first().map(parse_provider).transpose()
        }).await
    }

    async fn get_provider_by_slug(&self, slug: &str) -> ServiceResult<Option<ProviderRecord>> {
        let env = resolve_env();
        let sql = format!(
            "SELECT provider_id, slug, name, config, disabled, created_by, owners, {ts} \
             FROM bcs_providers WHERE env = ? AND slug = ? LIMIT 1",
            ts = self.select_timestamp_columns(),
        );
        let rows = self.query("get_provider_by_slug", DbStatement::with_params(sql,
            vec![DbValue::from(env.as_str()), DbValue::from(slug)])).await?;
        rows.first().map(parse_provider).transpose()
    }

    async fn list_providers_by_ids(
        &self,
        provider_ids: &[String],
    ) -> ServiceResult<Vec<ProviderRecord>> {
        if provider_ids.is_empty() {
            return Ok(Vec::new());
        }

        let env = resolve_env();
        let placeholders = provider_ids.iter().map(|_| "?").collect::<Vec<_>>().join(", ");
        let sql = format!(
            "SELECT provider_id, slug, name, config, disabled, created_by, owners, {ts} \
             FROM bcs_providers WHERE provider_id IN ({placeholders}) AND env = ? ORDER BY provider_id",
            ts = self.select_timestamp_columns(),
        );
        let mut params = provider_ids
            .iter()
            .map(|provider_id| DbValue::from(provider_id.as_str()))
            .collect::<Vec<_>>();
        params.push(DbValue::from(env.as_str()));
        let rows = self
            .query(
                "list_providers_by_ids",
                DbStatement::with_params(sql, params),
            )
            .await?;
        rows.iter().map(parse_provider).collect()
    }

    async fn list_providers(&self) -> ServiceResult<Vec<ProviderRecord>> {
        let env = resolve_env();
        let sql = format!(
            "SELECT provider_id, slug, name, config, disabled, created_by, owners, {ts} \
             FROM bcs_providers WHERE env = ? ORDER BY provider_id",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "list_providers",
                DbStatement::with_params(sql, vec![DbValue::from(env.as_str())]),
            )
            .await?;
        rows.iter().map(parse_provider).collect()
    }

    async fn update_provider_metadata(
        &self,
        provider_id: &str,
        name: Option<&str>,
        config: Option<&str>,
        slug: Option<&str>,
        _updated_at: u64,
    ) -> ServiceResult<Option<ProviderRecord>> {
        let env = resolve_env();
        let now_clause = self.now_modified_clause();
        let mut assignments = Vec::new();
        let mut params = Vec::new();
        for (column, value) in [("name", name), ("config", config), ("slug", slug)] {
            if let Some(value) = value {
                assignments.push(format!("{column} = ?"));
                params.push(DbValue::from(value));
            }
        }
        if assignments.is_empty() { return self.get_provider(provider_id).await; }
        assignments.push(now_clause.to_string());
        params.push(DbValue::from(provider_id));
        params.push(DbValue::from(env.as_str()));
        // Guard at the write boundary, so a stale ordinary PATCH cannot overwrite
        // a concurrently tightened policy. Both dialects evaluate this atomically.
        let guard = if config.is_some_and(|config| bcs_service_api::ProviderCollaborationScope::from_config(config)
            .is_ok_and(|scope| scope == bcs_service_api::ProviderCollaborationScope::Standard)) {
            match self.flavor {
                DbSqlFlavor::Mysql => " AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(config, '$.collaboration_scope')), 'standard') <> 'same_owner'",
                DbSqlFlavor::Sqlite => " AND COALESCE(json_extract(config, '$.collaboration_scope'), 'standard') <> 'same_owner'",
            }
        } else { "" };
        let public_guard = if config.is_some_and(|config| bcs_service_api::ProviderCollaborationScope::from_config(config)
            .is_ok_and(|scope| scope == bcs_service_api::ProviderCollaborationScope::SameOwner)) {
            match self.flavor {
                DbSqlFlavor::Mysql => " AND (JSON_UNQUOTE(JSON_EXTRACT(config, '$.collaboration_scope')) = 'same_owner' OR NOT EXISTS (SELECT 1 FROM bcs_bots b JOIN bcs_group_participants gp ON gp.bot_uuid = b.bot_uuid AND gp.env = b.env JOIN bcs_groups g ON g.group_id = gp.group_id AND g.env = gp.env WHERE b.provider_id = bcs_providers.provider_id AND b.env = bcs_providers.env AND COALESCE(b.is_deleted, 0) = 0 AND g.visibility = 'public' AND g.record_status <> 'deleted'))",
                DbSqlFlavor::Sqlite => " AND (json_extract(config, '$.collaboration_scope') = 'same_owner' OR NOT EXISTS (SELECT 1 FROM bcs_bots b JOIN bcs_group_participants gp ON gp.bot_uuid = b.bot_uuid AND gp.env = b.env JOIN bcs_groups g ON g.group_id = gp.group_id AND g.env = gp.env WHERE b.provider_id = bcs_providers.provider_id AND b.env = bcs_providers.env AND COALESCE(b.is_deleted, 0) = 0 AND g.visibility = 'public' AND g.record_status <> 'deleted'))",
            }
        } else { "" };
        if let Some(config) = config { bcs_service_api::ProviderCollaborationScope::from_config(config)?; }
        let affected = self.execute("update_provider_metadata", DbStatement::with_params(format!(
            "UPDATE bcs_providers SET {} WHERE provider_id = ? AND env = ?{guard}{public_guard}",
            assignments.join(", "),
        ), params)).await?;
        self.providers.invalidate(&format!("{env}:{provider_id}"));
        let updated = self.get_provider(provider_id).await?;
        if affected == 0 {
            if let (Some(current), Some(config)) = (&updated, config) {
                reject_policy_relaxation(&current.config, config)?;
                if bcs_service_api::ProviderCollaborationScope::from_config(&current.config)? == bcs_service_api::ProviderCollaborationScope::Standard
                    && bcs_service_api::ProviderCollaborationScope::from_config(config)? == bcs_service_api::ProviderCollaborationScope::SameOwner {
                    return Err(ServiceError::Conflict("Provider has public groups; change them to private before enabling same_owner".into()));
                }
            }
        }
        Ok(updated)
    }

    async fn update_provider_disabled(
        &self,
        provider_id: &str,
        disabled: bool,
        _updated_at: u64,
    ) -> ServiceResult<Option<ProviderRecord>> {
        let env = resolve_env();
        let sql = format!(
            "UPDATE bcs_providers SET disabled = ?, {now_clause} \
             WHERE provider_id = ? AND env = ?",
            now_clause = self.now_modified_clause(),
        );
        self.execute(
            "update_provider_disabled",
            DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(disabled),
                    DbValue::from(provider_id),
                    DbValue::from(env.as_str()),
                ],
            ),
        )
        .await?;
        self.providers.invalidate(&format!("{env}:{provider_id}"));
        self.get_provider(provider_id).await
    }
}

#[async_trait]
impl ProviderCredentialRepoPort for DbProviderStore {
    async fn insert_credential(&self, credential_record: ProviderCredential) -> ServiceResult<()> {
        let env = resolve_env();
        self.execute_insert(
            "insert_provider_credential",
            DbStatement::with_params(
                Self::insert_credential_sql(),
                vec![
                    DbValue::from(credential_record.provider_id.as_str()),
                    DbValue::from(env.as_str()),
                    DbValue::from(credential_record.credential_kind.as_str()),
                    DbValue::from(credential_record.secret_value.as_str()),
                    DbValue::from(credential_record.disabled),
                ],
            ),
        )
        .await?;
        self.credentials.invalidate(&format!("{}:{}:{}", env, credential_record.provider_id, credential_record.credential_kind));
        Ok(())
    }

    async fn get_credential_by_kind(
        &self,
        provider_id: &str,
        credential_kind: &str,
    ) -> ServiceResult<Option<ProviderCredential>> {
        let env = resolve_env();
        self.credentials.get(&format!("{env}:{provider_id}:{credential_kind}"), || async {
        let sql = format!(
            "SELECT provider_id, credential_kind, secret_value, disabled, {ts} \
             FROM bcs_provider_credentials \
             WHERE provider_id = ? AND credential_kind = ? AND env = ? LIMIT 1",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "get_provider_credential_by_kind",
                DbStatement::with_params(
                    sql,
                    vec![
                        DbValue::from(provider_id),
                        DbValue::from(credential_kind),
                        DbValue::from(env.as_str()),
                    ],
                ),
            )
            .await?;
        rows.first().map(|row| parse_credential(row).ok_or_else(|| ServiceError::InternalError("invalid provider credential".into()))).transpose()
        }).await
    }

    async fn list_credentials_by_kind_for_providers(
        &self,
        provider_ids: &[String],
        credential_kind: &str,
    ) -> ServiceResult<Vec<ProviderCredential>> {
        if provider_ids.is_empty() {
            return Ok(Vec::new());
        }

        let env = resolve_env();
        let placeholders = provider_ids.iter().map(|_| "?").collect::<Vec<_>>().join(", ");
        let sql = format!(
            "SELECT provider_id, credential_kind, secret_value, disabled, {ts} \
             FROM bcs_provider_credentials \
             WHERE provider_id IN ({placeholders}) AND credential_kind = ? AND env = ? ORDER BY provider_id",
            ts = self.select_timestamp_columns(),
        );
        let mut params = provider_ids
            .iter()
            .map(|provider_id| DbValue::from(provider_id.as_str()))
            .collect::<Vec<_>>();
        params.push(DbValue::from(credential_kind));
        params.push(DbValue::from(env.as_str()));
        let rows = self
            .query(
                "list_provider_credentials_by_kind_for_providers",
                DbStatement::with_params(sql, params),
            )
            .await?;
        Ok(rows.iter().filter_map(parse_credential).collect())
    }

    async fn get_credential_by_secret(
        &self,
        credential_kind: &str,
        secret_value: &str,
    ) -> ServiceResult<Option<ProviderCredential>> {
        let env = resolve_env();
        let sql = format!(
            "SELECT provider_id, credential_kind, secret_value, disabled, {ts} \
             FROM bcs_provider_credentials \
             WHERE credential_kind = ? AND secret_value = ? AND env = ? LIMIT 1",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "get_provider_credential_by_secret",
                DbStatement::with_params(
                    sql,
                    vec![
                        DbValue::from(credential_kind),
                        DbValue::from(secret_value),
                        DbValue::from(env.as_str()),
                    ],
                ),
            )
            .await?;
        Ok(rows.first().and_then(parse_credential))
    }

    async fn list_credentials_by_provider(
        &self,
        provider_id: &str,
    ) -> ServiceResult<Vec<ProviderCredential>> {
        let env = resolve_env();
        let sql = format!(
            "SELECT provider_id, credential_kind, secret_value, disabled, {ts} \
             FROM bcs_provider_credentials \
             WHERE provider_id = ? AND env = ? ORDER BY credential_kind",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "list_provider_credentials",
                DbStatement::with_params(
                    sql,
                    vec![DbValue::from(provider_id), DbValue::from(env.as_str())],
                ),
            )
            .await?;
        Ok(rows.iter().filter_map(parse_credential).collect())
    }

    async fn update_credential_disabled(
        &self,
        provider_id: &str,
        credential_kind: &str,
        disabled: bool,
        _updated_at: u64,
    ) -> ServiceResult<Option<ProviderCredential>> {
        let env = resolve_env();
        let sql = format!(
            "UPDATE bcs_provider_credentials SET disabled = ?, {now_clause} \
             WHERE provider_id = ? AND credential_kind = ? AND env = ?",
            now_clause = self.now_modified_clause(),
        );
        self.execute(
            "update_provider_credential_disabled",
            DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(disabled),
                    DbValue::from(provider_id),
                    DbValue::from(credential_kind),
                    DbValue::from(env.as_str()),
                ],
            ),
        )
        .await?;
        self.credentials.invalidate(&format!("{env}:{provider_id}:{credential_kind}"));
        self.get_credential_by_kind(provider_id, credential_kind).await
    }
}

#[async_trait]
impl OrganizationCandidateReadPort for DbProviderStore {
    async fn list_organization_candidates_page(
        &self,
        query: OrganizationCandidateReadQuery,
    ) -> ServiceResult<Option<OrganizationCandidateReadPage>> {
        if query.provider_ids.is_empty() {
            return Ok(Some(OrganizationCandidateReadPage {
                records: Vec::new(),
                total: 0,
            }));
        }

        let from_sql = " FROM bcs_provider_bot_bindings pb \
            JOIN bcs_providers p \
              ON p.provider_id = pb.provider_id \
             AND p.env = pb.env \
            JOIN bcs_bots b \
              ON b.bot_uuid = pb.bot_uuid \
             AND b.env = pb.env \
            LEFT JOIN bcs_organization_members om \
              ON om.env = pb.env \
             AND om.organization_code = ? \
             AND om.bot_uuid = pb.bot_uuid \
             AND om.disabled = 0";
        let mut filter_sql = " WHERE pb.env = ? \
              AND pb.disabled = 0 \
              AND p.disabled = 0 \
              AND b.is_deleted = 0 \
              AND b.actor_kind = 'bot' \
              AND om.bot_uuid IS NULL \
              AND pb.provider_id IN ("
            .to_string();
        filter_sql.push_str(
            &query
                .provider_ids
                .iter()
                .map(|_| "?")
                .collect::<Vec<_>>()
                .join(", "),
        );
        filter_sql.push(')');

        let mut params = vec![
            DbValue::from(query.organization_code.as_str()),
            DbValue::from(query.env.as_str()),
        ];
        params.extend(
            query
                .provider_ids
                .iter()
                .map(|provider_id| DbValue::from(provider_id.as_str())),
        );
        if let Some(q) = query.q.as_deref().map(str::trim).filter(|q| !q.is_empty()) {
            let (card_sql, card_params) = crate::a2a_search::card_text_match(self.flavor, "b.agent_card", q);
            filter_sql.push_str(&format!(" AND (LOWER(pb.bot_uuid) LIKE ? OR \
                LOWER(b.name) LIKE ? OR (b.agent_card IS NULL AND LOWER(COALESCE(b.bot_info, '')) LIKE ?) OR \
                (b.agent_card IS NOT NULL AND ({card_sql})))"));
            let pattern = like_pattern(q);
            for _ in 0..3 {
                params.push(DbValue::from(pattern.as_str()));
            }
            params.extend(card_params);
        }

        let count_rows = self
            .query(
                "count_organization_candidate_bots",
                DbStatement::with_params(
                    format!("SELECT COUNT(*) AS total{from_sql}{filter_sql}"),
                    params.clone(),
                ),
            )
            .await?;
        let total = count_rows
            .first()
            .and_then(|row| row.get_i64("total").ok().flatten())
            .unwrap_or(0)
            .max(0) as u64;

        params.push(DbValue::from(query.limit));
        params.push(DbValue::from(query.offset));
        let rows = self
            .query(
                "list_organization_candidate_bots",
                DbStatement::with_params(
                    format!(
                        "SELECT pb.bot_uuid, pb.provider_id, p.name AS provider_name, \
                         b.name AS bot_name, b.bot_info{from_sql}{filter_sql} \
                         ORDER BY pb.bot_uuid LIMIT ? OFFSET ?"
                    ),
                    params,
                ),
            )
            .await?;

        Ok(Some(OrganizationCandidateReadPage {
            records: rows.iter().filter_map(parse_provider_bot_record).collect(),
            total,
        }))
    }
}
