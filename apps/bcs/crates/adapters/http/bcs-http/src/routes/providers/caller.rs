use super::*;

/// GET `/providers/{provider_id}/bots/{provider_bot_ref}/caller`.
/// Read-back of the authoritative Bot caller configuration behind Provider
/// admin auth. `snapshot: null` means "existing, never configured".
pub async fn get_provider_bot_caller_config(
    State(state): State<HttpAppState>,
    Path((provider_id, provider_bot_ref)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<Json<ProviderBotCallerConfigResponse>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let bot_uuid = state
        .services
        .provider_management
        .resolve_provider_bot_uuid_for_caller_config(&provider_id, &provider_bot_ref, &provider_admin_token)
        .await
        .map_err(provider_error)?;
    let config = state
        .services
        .provider_management
        .get_provider_bot_caller_configuration(
            &provider_id,
            &bot_uuid,
            &bcs_config::resolve_env_str(),
            &provider_admin_token,
        )
        .await
        .map_err(provider_error)?;
    info!(
        provider_id,
        provider_bot_ref,
        configured = config.snapshot.is_some(),
        "Provider Bot caller configuration read completed"
    );
    Ok(Json(ProviderBotCallerConfigResponse {
        snapshot: config.snapshot.map(caller_snapshot_to_dto),
    }))
}

/// PATCH `/providers/{provider_id}/bots/{provider_bot_ref}/caller`.
/// Full-snapshot sync behind Provider admin auth. The response confirms the
/// storage linearization only — never readiness or execution authorization.
pub async fn patch_provider_bot_caller_config(
    State(state): State<HttpAppState>,
    Path((provider_id, provider_bot_ref)): Path<(String, String)>,
    headers: HeaderMap,
    body: Result<Json<PatchProviderBotCallerConfigRequest>, JsonRejection>,
) -> Result<Json<SyncProviderBotCallerConfigResponse>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let Json(body) = body.map_err(|_| {
        warn!(
            request_id = %bcs_observability::CurrentRequestId,
            provider_id,
            failure = "invalid_json_body",
            "Provider Bot caller configuration patch rejected"
        );
        ProviderRouteError::bad_request("request body is invalid")
    })?;
    let bot_uuid = state
        .services
        .provider_management
        .resolve_provider_bot_uuid_for_caller_config(&provider_id, &provider_bot_ref, &provider_admin_token)
        .await
        .map_err(provider_error)?;
    let command = SyncProviderBotCallerConfigurationCommand::new(
        provider_id.clone(),
        bot_uuid,
        bcs_config::resolve_env_str(),
        bcs_domain::caller_identity::CallerIdentitySnapshot {
            source_revision: body.snapshot.source_revision,
            caller_identity: bcs_domain::caller_identity::CallerIdentityConfig {
                enabled: body.snapshot.caller_identity.enabled,
                minimum_isolation: bcs_domain::caller_identity::CallerMinimumIsolation::CallerContainer,
            },
        },
    );
    let (snapshot, sync_status) = match state
        .services
        .provider_management
        .sync_provider_bot_caller_configuration(command, &provider_admin_token)
        .await
    {
        Ok(bcs_service_api::port::repo::CallerConfigurationSync::Applied { snapshot }) => {
            (Some(snapshot), CallerConfigSyncStatusDto::Applied)
        }
        Ok(bcs_service_api::port::repo::CallerConfigurationSync::Unchanged { snapshot }) => {
            (Some(snapshot), CallerConfigSyncStatusDto::Unchanged)
        }
        Ok(bcs_service_api::port::repo::CallerConfigurationSync::Stale { snapshot }) => {
            (Some(snapshot), CallerConfigSyncStatusDto::Stale)
        }
        Ok(bcs_service_api::port::repo::CallerConfigurationSync::Conflict { .. }) => {
            return Err(ProviderRouteError {
                status: StatusCode::CONFLICT,
                message: "caller_configuration_revision_conflict".to_string(),
            });
        }
        Err(error) => return Err(provider_error(error)),
    };
    info!(
        provider_id,
        provider_bot_ref,
        sync_status = ?sync_status,
        "Provider Bot caller configuration sync completed"
    );
    Ok(Json(SyncProviderBotCallerConfigResponse {
        snapshot: snapshot.map(caller_snapshot_to_dto),
        sync_status,
    }))
}

fn caller_snapshot_to_dto(
    snapshot: bcs_domain::caller_identity::CallerIdentitySnapshot,
) -> bcs_protocol::http::provider::CallerIdentitySnapshotDto {
    bcs_protocol::http::provider::CallerIdentitySnapshotDto {
        source_revision: snapshot.source_revision,
        caller_identity: bcs_protocol::http::provider::CallerIdentityConfigDto {
            enabled: snapshot.caller_identity.enabled,
            minimum_isolation: bcs_protocol::http::provider::CallerMinimumIsolationDto::CallerContainer,
        },
    }
}
