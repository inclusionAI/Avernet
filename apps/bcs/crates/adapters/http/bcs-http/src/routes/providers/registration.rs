//! Provider and Bot registration HTTP translation.
use super::*;

pub async fn register_provider(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<RegisterProviderRequest>,
) -> Result<Json<RegisterProviderResponse>, ProviderRouteError> {
    let created_by = require_staff_no(&state, &headers, &uri).await?;
    let outcome = state
        .services
        .provider_management
        .register_provider(RegisterProviderCommand {
            collaboration_scope: parse_collaboration_scope(req.collaboration_scope.as_deref())?,
            name: req.name,
            slug: req.slug,
            webhook_url: req.webhook_url,
            admin_callback_url: req.admin_callback_url,
            auth_mode: auth_mode_from_wire(req.auth.mode),
            created_by,
            protocol_version: req.protocol_version,
            coordination: req.coordination.map(coordination_from_wire),
            caller_capabilities: req.caller_capabilities,
        })
        .await
        .map_err(provider_error)?;

    Ok(Json(RegisterProviderResponse {
        provider_id: outcome.provider_id,
        provider_admin_token: outcome.provider_admin_token,
        bcs_to_provider_token: outcome.bcs_to_provider_token,
    }))
}

pub async fn get_provider_by_slug(
    State(state): State<HttpAppState>,
    Path(slug): Path<String>,
) -> Result<Json<ProviderBasicInfoResponse>, ProviderRouteError> {
    let info = state.services.provider_management.get_provider_by_slug(&slug).await
        .map_err(provider_error)?
        .ok_or_else(|| ProviderRouteError {
            status: StatusCode::NOT_FOUND, message: format!("provider slug '{slug}' not found"),
        })?;
    Ok(Json(ProviderBasicInfoResponse {
        provider_id: info.provider_id,
        slug: info.slug,
        name: info.name,
        auth_mode: match info.auth_mode {
            ProviderAuthMode::StaticBearer => ProviderAuthModeDto::StaticBearer,
            ProviderAuthMode::AgentPass => ProviderAuthModeDto::AgentPass,
            ProviderAuthMode::AgentPassSubbots => ProviderAuthModeDto::AgentPassSubbots,
            ProviderAuthMode::ProviderAdmin => ProviderAuthModeDto::ProviderAdmin,
        },
        enabled: info.enabled,
        protocol_version: info.protocol_version,
        created_at: info.created_at,
        updated_at: info.updated_at,
    }))
}

pub async fn register_provider_bot(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    Json(req): Json<RegisterProviderBotRequest>,
) -> Result<Json<RegisterProviderBotResponse>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let allowed_switch_provider = state.allowed_switch_provider_ids.contains(&provider_id);
    let connection_mode = req
        .connection_mode
        .unwrap_or(ProviderBotConnectionModeDto::Gateway);
    // plugin mode is accepted only for allow-listed providers (§3.0 admission gate).
    if matches!(connection_mode, ProviderBotConnectionModeDto::Plugin) && !allowed_switch_provider {
        return Err(ProviderRouteError::bad_request(
            "connection_mode plugin requires an allow-listed provider",
        ));
    }
    let plugin_mode = matches!(connection_mode, ProviderBotConnectionModeDto::Plugin);
    let bot_uuid = allowed_switch_provider.then(|| req.provider_bot_ref.clone());
    let source=bcs_service_api::types::a2a_bot::A2aCardSource::from_parts(req.agent_card_url,req.agent_card)
        .map_err(provider_error)?;
    let command = RegisterProviderBotCommand {
            webhook_url: req.webhook_url,
            provider_id,
            provider_admin_token,
            name: req.name.unwrap_or_default(),
            summary: req.summary,
            owners: req.owners,
            provider_bot_ref: req.provider_bot_ref,
            domains: req.domains,
            skills: req.skills.into_iter().map(to_core_skill).collect(),
            scopes: req.scopes,
            bot_uuid,
            // Gateway mode over an allow-listed provider rejects a collision where
            // provider_bot_ref is already used as a bot_uuid; plugin mode relaxes
            // this so W-before-P /补注册 over an existing real-token bot proceeds to
            // the token-preserving soft-merge path.
            reject_existing_bot_uuid: allowed_switch_provider && !plugin_mode,
            connection_mode,
        };
    let outcome = if let Some(source) = source {
        let service = state.services.a2a_bot_management.as_ref().ok_or_else(|| ProviderRouteError {
            status: StatusCode::SERVICE_UNAVAILABLE, message: "A2A registration is not configured".into(),
        })?;
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
            .ok().and_then(|time| i64::try_from(time.as_millis()).ok())
            .ok_or_else(|| ProviderRouteError {status:StatusCode::INTERNAL_SERVER_ERROR,message:"registration clock unavailable".into()})?;
        service.register_provider_bot(command, source, now).await
    } else {
        state.services.provider_management.register_provider_bot(command).await
    }.map_err(provider_error)?;

    if allowed_switch_provider && outcome.created && outcome.actor_kind == ActorKind::Bot {
        match outcome.capabilities.clone() {
            Some(capabilities) => {
                state.dispatch_visibility_sync(VisibilitySyncRequest {
                    bot_uuid: outcome.bot_uuid.clone(),
                    visibility: capabilities.visibility.clone(),
                    capabilities,
                    actor_kind: outcome.actor_kind,
                });
            }
            None => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    provider_id = %outcome.provider_id,
                    bot_uuid = %outcome.bot_uuid,
                    "register_provider_bot: allowlisted provider but capabilities missing; \
                     skipping bcs-fuse sync"
                );
            }
        }
    }

    Ok(Json(RegisterProviderBotResponse {
        auth_mode: match outcome.auth_mode {
            ProviderAuthMode::StaticBearer => ProviderAuthModeDto::StaticBearer,
            ProviderAuthMode::ProviderAdmin => ProviderAuthModeDto::ProviderAdmin,
            ProviderAuthMode::AgentPass => ProviderAuthModeDto::AgentPass,
            ProviderAuthMode::AgentPassSubbots => ProviderAuthModeDto::AgentPassSubbots,
        },
        agent_code: outcome.identity.agent_code,
        bot_slug: outcome.identity.bot_slug,
        webhook_url: outcome.webhook_url,
        bot_uuid: outcome.bot_uuid,
        provider_id: outcome.provider_id,
        provider_bot_ref: outcome.provider_bot_ref,
        bot_runtime_token: outcome.bot_runtime_token,
        message: outcome.message,
    }))
}

pub async fn list_provider_bots(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let bindings = state
        .services
        .provider_management
        .list_provider_bots(&provider_id, &provider_admin_token)
        .await
        .map_err(provider_error)?;
    let items: Vec<Value> = bindings.into_iter().map(binding_to_json).collect();
    Ok(Json(json!({ "items": items })))
}

pub async fn get_provider(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<ProviderInfoResponse>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let provider = state
        .services
        .provider_management
        .get_provider(&provider_id, &provider_admin_token)
        .await
        .map_err(provider_error)?;
    Ok(Json(
        provider_to_response(provider).map_err(provider_error)?,
    ))
}

pub async fn patch_provider(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<PatchProviderRequest>,
) -> Result<Json<ProviderInfoResponse>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let authenticated_staff_id = require_staff_no(&state, &headers, &uri).await?;
    let provider = state
        .services
        .provider_management
        .update_provider(UpdateProviderCommand {
            collaboration_scope: parse_collaboration_scope(req.collaboration_scope.as_deref())?,
            provider_id,
            provider_admin_token,
            authenticated_staff_id,
            name: req.name,
            slug: req.slug,
            webhook_url: req.webhook_url,
            admin_callback_url: req.admin_callback_url,
            protocol_version: req.protocol_version,
            coordination: req.coordination.map(coordination_from_wire),
            organization_management: req
                .organization_management
                .map(organization_management_from_wire),
            caller_capabilities: req.caller_capabilities,
        })
        .await
        .map_err(provider_error)?;
    Ok(Json(
        provider_to_response(provider).map_err(provider_error)?,
    ))
}
