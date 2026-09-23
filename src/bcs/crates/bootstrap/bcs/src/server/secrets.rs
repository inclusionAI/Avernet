//! Secret resolution and key-port construction.
//!
//! Split out from server.rs as part of the V1 API auth plugin chain (Task 1) refactor. Behavior preserved exactly.

use super::*;

/// Build a default `SecretService` for the `ServicesBuilder` step.
///
/// At builder time we do not perform async provider initialization. We seed
/// every `Services` instance with a Noop so the builder's required-field
/// invariant is satisfied; the configured backend is swapped in alongside
/// `HttpAppState` construction.
pub(super) fn default_bootstrap_secret_service() -> Arc<dyn bcs_service_api::SecretService> {
    use bcs_secret::DefaultSecretService;
    use bcs_secret_local::NoopSecretAccess;
    Arc::new(DefaultSecretService::new(Arc::new(NoopSecretAccess)))
}

pub(super) fn group_session_test_secret_access(config: &BcsConfig) -> Arc<dyn SecretAccessPort> {
    Arc::new(InMemorySecretAccess::with_entries([(
        config
            .group_session_ws
            .signing_key_secret
            .trim()
            .to_string(),
        String::new(),
        GROUP_SESSION_WS_TEST_SIGNING_KEY.to_string(),
    )]))
}

pub(super) fn build_secret_access_blocking(config: &BcsConfig) -> crate::Result<Arc<dyn SecretAccessPort>> {
    std::thread::scope(|scope| {
        scope
            .spawn(|| {
                tokio::runtime::Runtime::new()
                    .expect("temp runtime for secret provider build")
                    .block_on(crate::http_adapter::build_secret_access(config))
            })
            .join()
            .expect("secret provider build thread panicked")
    })
}

pub(super) async fn build_group_session_token_port(
    config: &GroupSessionWsConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<Arc<dyn GroupSessionTokenPort>> {
    let secret_name = config.signing_key_secret.trim();
    let secret = secret_access.get_secret(secret_name).await.map_err(|_| {
        crate::BcsError::InvalidConfig(format!(
            "group_session_ws.signing_key_secret '{secret_name}' is required"
        ))
    })?;
    let tokens = GroupSessionJwtService::new(&secret.value).map_err(|_| {
        crate::BcsError::InvalidConfig(format!(
            "group_session_ws.signing_key_secret '{secret_name}' must resolve to non-empty material"
        ))
    })?;
    Ok(Arc::new(tokens))
}

pub(super) async fn build_group_session_connection_service(
    sessions: Arc<dyn bcs_service_api::application::v1::SessionService>,
    config: &GroupSessionWsConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<Arc<dyn GroupSessionConnectionService>> {
    let tokens = build_group_session_token_port(config, secret_access).await?;
    Ok(Arc::new(GroupSessionConnectionServiceImpl::new(
        sessions, tokens,
    )))
}

pub(super) fn build_invite_code_service(
    config: &BcsConfig,
    db_plugin: Option<Arc<dyn bcs_db_api::DbPlugin>>,
    db_kind: Option<&DbPluginKind>,
    invite_token_secret: Vec<u8>,
) -> Arc<dyn bcs_service_api::application::v1::InviteCodeService> {
    let repo: Arc<dyn bcs_service_api::port::repo::InviteCodeRepoPort> = match (db_plugin, db_kind) {
        (Some(db_plugin), Some(db_kind)) => match db_kind {
            DbPluginKind::LocalSqlite => Arc::new(DbInviteCodeStore::sqlite(db_plugin)),
            DbPluginKind::Mysql => Arc::new(DbInviteCodeStore::mysql(db_plugin)),
            DbPluginKind::External(provider) => {
                panic!(
                    "external database plugin '{}' has no invite-code store wiring",
                    provider
                )
            }
        },
        _ => Arc::new(MemoryInviteCodeRepo::with_data_dir(config.bots_base_dir.clone())),
    };
    Arc::new(InviteCodeServiceImpl::new(
        repo,
        invite_token_secret,
        config.invite.public_claim_max_count,
    ))
}

pub(super) async fn resolve_secret_value(
    name: Option<&str>,
    access: &dyn SecretAccessPort,
    field: &str,
) -> crate::Result<Option<String>> {
    let Some(name) = name.map(str::trim).filter(|v| !v.is_empty()) else { return Ok(None); };
    let record = access.get_secret(name).await.map_err(|e| crate::BcsError::InvalidConfig(format!("{field} '{name}' unavailable: {e}")))?;
    if record.value.trim().is_empty() { return Err(crate::BcsError::InvalidConfig(format!("{field} '{name}' is empty"))); }
    Ok(Some(record.value))
}

pub(super) async fn resolve_token_secret_secret(
    secret_key: Option<&str>,
    secret_access: &dyn SecretAccessPort,
    field: &str,
) -> crate::Result<Option<String>> {
    resolve_secret_value(secret_key, secret_access, field).await
}

pub(super) fn resolve_invite_token_secret(config: &BcsConfig) -> Vec<u8> {
    config
        .invite
        .token_secret
        .as_deref()
        .map(str::as_bytes)
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| {
            tracing::warn!(
                "invite.token_secret not configured — generating random secret (tokens will not survive restart)"
            );
            (0..32).map(|_| fastrand::u8(..)).collect()
        })
}

pub async fn resolve_config_secrets(config: &mut BcsConfig, access: &dyn SecretAccessPort) -> crate::Result<()> {
    if let Some(value) = resolve_secret_value(config.auth_sdk.secret_key_secret.as_deref(), access, "auth_sdk.secret_key_secret").await? {
        config.auth_sdk.secret_key = Some(value);
    }
    if let Some(value) = resolve_secret_value(config.llm.api_key_secret.as_deref(), access, "llm.api_key_secret").await? {
        config.llm.api_key = Some(Secret::new(value));
        config.llm.api_key_env = None;
    }
    if let Some(value) = resolve_secret_value(
        config.bcsfuse.authorization_ref.as_deref(),
        access,
        "bcsfuse.authorization_ref",
    )
    .await?
    {
        config.bcsfuse.set_resolved_authorization(value);
    }
    if config.invite.token_secret_secret.as_deref().is_some_and(|v| !v.trim().is_empty()) {
        config.invite.token_secret = resolve_token_secret_secret(config.invite.token_secret_secret.as_deref(), access, "invite.token_secret_secret").await?;
    }
    if config.session_files.share.token_secret_secret.as_deref().is_some_and(|v| !v.trim().is_empty()) {
        config.session_files.share.token_secret = resolve_token_secret_secret(config.session_files.share.token_secret_secret.as_deref(), access, "session_files.share.token_secret_secret").await?;
    }
    for account in &mut config.dingtalk_accounts {
        if let Some(value) = resolve_secret_value(account.client_secret_secret.as_deref(), access, "dingtalk_accounts.client_secret_secret").await? {
        account.client_secret = Some(Secret::new(value));
        }
    }
    if let Some(logger) = config.group_logger.as_mut() {
        if let Some(value) = resolve_secret_value(logger.client_secret_secret.as_deref(), access, "group_logger.client_secret_secret").await? {
        logger.client_secret = value;
        }
    }
    // Any `<key>_secret` option inside a human_notify provider entry is a
    // secret-provider reference (e.g. Mist): resolve it here and inject the
    // plain value as `<key>`, then drop the reference. A non-blank literal
    // `<key>` wins over the reference so explicit values are never clobbered.
    // `client_secret_secret` (DingTalk notifier) and `signing_key_secret`
    // (work-order notifier) are instances of this rule. The DingTalk
    // notifier's literal secret field is `client_secret`, which itself ends
    // in `_secret`; it is excluded below so it is never mistaken for a
    // reference to a (nonexistent) `client` literal.
    for provider in &mut config.human_notify.providers {
        let provider_name = provider.name.clone();
        let reference_keys: Vec<String> = provider
            .options
            .keys()
            .filter(|key| key.ends_with("_secret") && key.as_str() != "client_secret")
            .cloned()
            .collect();
        for reference_key in reference_keys {
            let Some(reference) = provider
                .options
                .get(&reference_key)
                .and_then(|value| value.as_str())
                .map(str::trim)
                .filter(|value| !value.is_empty())
            else {
                continue;
            };
            let target_key = reference_key
                .strip_suffix("_secret")
                .expect("reference keys end with _secret")
                .to_string();
            let literal_wins = provider
                .options
                .get(&target_key)
                .and_then(|value| value.as_str())
                .is_some_and(|value| !value.trim().is_empty());
            if literal_wins {
                continue;
            }
            let field = format!("human_notify.providers.{provider_name}.{reference_key}");
            if let Some(value) = resolve_secret_value(Some(reference), access, &field).await? {
                provider
                    .options
                    .insert(target_key, serde_json::Value::String(value));
                provider.options.remove(&reference_key);
            }
        }
    }
    if let Some(oauth) = config.auth.oauth.as_mut() {
        for (provider_name, provider) in &mut oauth.providers {
        let field = format!("auth.oauth.providers.{provider_name}.client_secret_secret");
        if let Some(value) = resolve_secret_value(provider.client_secret_secret.as_deref(), access, &field).await? {
            provider.client_secret = Some(Secret::new(value));
        }
        }
    }
    Ok(())
}
