//! Legacy Group route handlers (read/update/membership/settings).

use super::*;

pub async fn list_groups(
    State(state): State<HttpAppState>,
    Query(query): Query<ListGroupsQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    let offset = query.offset.unwrap_or(0);
    let limit = query.limit.unwrap_or(10);
    let kind_filter = group_kind_filter(query.group_kind);
    let result = state
        .services
        .group_query
        .list_groups(GroupListCommand {
            group_kind: kind_filter,
            offset,
            limit,
            visibility: query.visibility.clone(),
            label: query.label.clone(),
        })
        .await
        .map_err(group_use_case_error_to_http)?;
    let items: Vec<Value> = result
        .items
        .into_iter()
        .map(group_list_entry_to_legacy_json)
        .collect();

    Ok(Json(serde_json::json!({
        "items": items,
        "total": result.total,
        "offset": result.offset,
        "limit": result.limit,
        "group_kind": query.group_kind,
    })))
}

pub async fn get_group(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, HttpAdapterError> {
    let mut group = state
        .services
        .group_query
        .get_group(GroupDetailCommand { group_id: id })
        .await
        .map_err(group_use_case_error_to_http)?;

    // Only expose latest_running_session_id for non-service groups
    if group.service_spec.is_none() {
        if let Ok(Some(session)) = state
            .services
            .session_management
            .list_by_group(
                &group.group_id,
                Some(SessionStatus::Running),
                0,
                1,
                None,
                None,
            )
            .await
            .map(|v| v.into_iter().next())
        {
            group.latest_running_session_id = Some(session.id);
        }
    }

    let mut json = group_to_detail_json(group.clone());
    // Resolve driver bot owner info
    let (driver_bot_owner, driver_bot_owner_name) =
        resolve_driver_bot_owner(&state, &group.driver_bot_id).await;
    if let Some(obj) = json.as_object_mut() {
        obj.insert(
            "driver_bot_owner".to_string(),
            serde_json::json!(driver_bot_owner),
        );
        obj.insert(
            "driver_bot_owner_name".to_string(),
            serde_json::json!(driver_bot_owner_name),
        );
    }

    Ok(Json(json))
}

pub async fn patch_group(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    body: Result<Json<LegacyUpdateGroupRequest>, JsonRejection>,
) -> Response {
    let Json(body) = match body {
        Ok(body) => body,
        Err(error) => {
            return legacy_group_error_response(
                StatusCode::BAD_REQUEST,
                "invalid_request",
                error.body_text(),
            );
        }
    };
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(caller) => caller,
        Err(error) => return error.into_response(),
    };
    if let GroupChatCaller::Bot { bot_uuid } = &caller
        && let Err(error) = validate_container_header(&state, &headers, bot_uuid)
    {
        return error.into_response();
    }
    let caller = application_caller(&caller);
    let Some(application) = state.group_application.as_ref() else {
        return legacy_group_error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal_error",
            "group update service is unavailable",
        );
    };

    match application
        .update(UpdateGroup {
            caller,
            group_id,
            patch: body.into(),
        })
        .await
    {
        Ok(group) => Json(group).into_response(),
        Err(error) => group_application_error_response(error),
    }
}


pub async fn get_group_collaboration_definition(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, HttpAdapterError> {
    let view = state
        .services
        .collaboration_runtime
        .get_group_collaboration_definition(&id)
        .await
        .map_err(collaboration_runtime_error_to_http)?;
    serde_json::to_value(view)
        .map(Json)
        .map_err(|error| HttpAdapterError::Service(ServiceError::InternalError(error.to_string())))
}

pub async fn patch_group_collaboration_definition(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    Json(req): Json<PatchGroupCollaborationDefinitionRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    reject_judge_yaml_when_unavailable(&state, &req.definition_yaml, "definition_yaml")?;
    let view = state
        .services
        .collaboration_runtime
        .patch_group_collaboration_definition(PatchGroupCollaborationDefinitionCommand {
            group_id: id,
            base_definition: req.base_definition,
            definition_yaml: req.definition_yaml,
            participant_bindings: req.participant_bindings,
        })
        .await
        .map_err(collaboration_runtime_error_to_http)?;
    serde_json::to_value(view)
        .map(Json)
        .map_err(|error| HttpAdapterError::Service(ServiceError::InternalError(error.to_string())))
}

pub async fn upgrade_group_collaboration_definition(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    Json(req): Json<UpgradeGroupCollaborationDefinitionRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let view = state
        .services
        .collaboration_runtime
        .upgrade_group_collaboration_definition(UpgradeGroupCollaborationDefinitionCommand {
            group_id: id,
            base_definition: req.base_definition,
            target_definition: req.target_definition,
            participant_bindings: req.participant_bindings,
        })
        .await
        .map_err(collaboration_runtime_error_to_http)?;
    serde_json::to_value(view)
        .map(Json)
        .map_err(|error| HttpAdapterError::Service(ServiceError::InternalError(error.to_string())))
}

pub async fn delete_group(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    Query(query): Query<DeleteSessionQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    let result = state
        .services
        .group_management
        .delete_group(GroupDeleteCommand {
            caller_actor_id: query.bot_id,
            group_id: id.clone(),
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "deleted": result.deleted,
        "id": result.group_id
    })))
}

pub async fn list_bot_groups(
    State(state): State<HttpAppState>,
    Path(bot_uuid): Path<String>,
    Query(query): Query<ListBotGroupsQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    let page = list_actor_groups(&state, &bot_uuid, query).await?;

    Ok(Json(serde_json::json!({
        "bot_uuid": bot_uuid,
        "items": page.items,
        "total": page.total,
        "offset": page.offset,
        "limit": page.limit,
    })))
}

pub async fn list_my_groups(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(query): Query<ListBotGroupsQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    let actor_id = resolve_actor_caller(&state, &headers, &uri).await?;
    let page = list_actor_groups(&state, &actor_id, formal_only_group_query(query)).await?;

    Ok(Json(serde_json::json!({
        "actor_id": actor_id,
        "items": page.items,
        "total": page.total,
        "offset": page.offset,
        "limit": page.limit,
    })))
}

pub(crate) struct ActorGroupListPage {
    items: Vec<Value>,
    total: u64,
    offset: u64,
    limit: u64,
}

pub(crate) async fn list_actor_groups(
    state: &HttpAppState,
    actor_id: &str,
    query: ListBotGroupsQuery,
) -> Result<ActorGroupListPage, HttpAdapterError> {
    let offset = query.offset.unwrap_or(0);
    let limit = query.limit.unwrap_or(10);
    let kind_filter = group_kind_filter(query.group_kind);

    let result = state
        .services
        .group_query
        .list_bot_groups(BotGroupListCommand {
            bot_id: actor_id.to_string(),
            group_kind: kind_filter,
            q: query.q.clone(),
            offset,
            limit,
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    if !query.include_session_groups {
        let items: Vec<Value> = result
            .items
            .into_iter()
            .map(bot_group_list_entry_to_legacy_json)
            .collect();

        return Ok(ActorGroupListPage {
            items,
            total: result.total,
            offset: result.offset,
            limit: result.limit,
        });
    }

    // Union: include groups where the actor is only a session participant
    // (not in group.participants), so humans added to a session via PATCH
    // can still see the group in their listing.
    let existing_ids: HashSet<String> = result.items.iter().map(|e| e.group_id.clone()).collect();
    let mut session_extra: Vec<GroupListEntry> = Vec::new();
    if let Ok(session_group_ids) = state
        .services
        .session_management
        .list_group_ids_by_session_participant(actor_id)
        .await
    {
        for gid in session_group_ids {
            if existing_ids.contains(&gid) {
                continue;
            }
            let group = match state
                .services
                .group_query
                .get_group(GroupDetailCommand {
                    group_id: gid.clone(),
                })
                .await
            {
                Ok(group) => group,
                Err(_) => continue,
            };
            // Apply kind filter
            if let Some(kind) = kind_filter {
                if group.group_kind != kind {
                    continue;
                }
            }
            if !group_detail_matches_bot_group_query(&group, query.q.as_deref()) {
                continue;
            }
            let participant_count = group.participants.len();
            let message_count = group.message_count;
            session_extra.push(GroupListEntry {
                group_id: group.group_id,
                label: group.label,
                driver_bot_id: group.driver_bot_id,
                originator: group.originator,
                context: group.context,
                participants: group.participants,
                participant_count,
                message_count,
                created_at: group.created_at,
                updated_at: group.updated_at,
                group_kind: group.group_kind,
                group_strategy: group.group_strategy,
                visibility: group.visibility,
                human_mention_notify_mode: group.human_mention_notify_mode,
            });
        }
    }

    let mut all_items: Vec<GroupListEntry> = result.items;
    if session_extra.is_empty() {
        let items: Vec<Value> = all_items
            .into_iter()
            .map(bot_group_list_entry_to_legacy_json)
            .collect();

        return Ok(ActorGroupListPage {
            items,
            total: result.total,
            offset: result.offset,
            limit: result.limit,
        });
    }

    let session_extra_len = session_extra.len() as u64;
    all_items.extend(session_extra);

    // Re-apply pagination (service layer already paginated formal results;
    // session-only extras are typically few, so we re-paginate the merged set).
    all_items.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
    let total = result.total.saturating_add(session_extra_len);
    let items: Vec<Value> = all_items
        .into_iter()
        .skip(offset as usize)
        .take(limit as usize)
        .map(bot_group_list_entry_to_legacy_json)
        .collect();

    Ok(ActorGroupListPage {
        items,
        total,
        offset,
        limit,
    })
}

pub(crate) fn group_detail_matches_bot_group_query(group: &GroupDetailResult, q: Option<&str>) -> bool {
    let Some(q) = q.map(str::trim).filter(|q| !q.is_empty()) else {
        return true;
    };
    let q = q.to_lowercase();
    group
        .label
        .as_deref()
        .unwrap_or("")
        .to_lowercase()
        .contains(&q)
}

pub async fn add_group_member(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<AddMemberRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let requester_bot_id = resolve_group_member_caller(&state, &headers, &uri, &id).await?;
    let AddMemberRequest { bot_uuid } = req;
    let result = state
        .services
        .group_management
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some(requester_bot_id),
            human_actor_id: extract_human_actor_id(&state, &headers, &uri).await,
            group_id: id.clone(),
            bot_id: bot_uuid,
            message_view_scope: None,
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "added": true,
        "session_id": result.group_id,
        "member": {
            "bot_uuid": result.member.bot_uuid,
            "role": result.member.role,
        }
    })))
}

pub async fn remove_group_member(
    State(state): State<HttpAppState>,
    Path((id, bot_uuid)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, HttpAdapterError> {
    let requester_bot_id = resolve_actor_caller(&state, &headers, &uri).await?;
    let result = state
        .services
        .group_management
        .remove_member(GroupRemoveMemberCommand {
            caller_actor_id: Some(requester_bot_id),
            group_id: id,
            bot_id: bot_uuid,
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "removed": true,
        "group_id": result.group_id,
        "removed_bot_uuid": result.removed_bot_uuid,
    })))
}

pub async fn update_group_status(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(req): Json<UpdateGroupStatusRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let requester_bot_id = authenticated_bot_from_headers(&state, &headers).await?;
    let status = req.status.clone();
    let result = state
        .services
        .group_management
        .update_status(GroupStatusCommand {
            caller_actor_id: Some(requester_bot_id.clone()),
            group_id: id.clone(),
            status,
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "updated": true,
        "group_id": id,
        "status": group_status_to_wire(result.status),
        "reason": req.reason,
        "changed_by": requester_bot_id,
    })))
}

pub async fn terminate_group(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, HttpAdapterError> {
    let caller_bot_id = authenticated_bot_from_headers(&state, &headers).await?;
    let session = state
        .services
        .group_management
        .terminate_group(GroupTerminateCommand {
            caller_actor_id: caller_bot_id.clone(),
            group_id: id.clone(),
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "terminated": true,
        "group_id": id,
        "status": "completed",
        "terminated_by": caller_bot_id,
        "session": {
            "id": session.group_id,
            "label": session.label,
            "driver_bot": session.driver_bot_id,
            "participants": session.participants.iter().map(|p| &p.bot_uuid).collect::<Vec<_>>(),
            "status": group_status_to_wire(session.status),
        }
    })))
}

pub async fn update_group_label(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<UpdateLabelRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let requester_bot_id = resolve_group_member_caller(&state, &headers, &uri, &id).await?;
    let result = state
        .services
        .group_management
        .update_label(GroupUpdateLabelCommand {
            caller_actor_id: requester_bot_id.clone(),
            group_id: id.clone(),
            label: req.label.clone(),
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "updated": true,
        "group_id": id,
        "label": result.label,
        "changed_by": requester_bot_id,
    })))
}

#[derive(Debug, Deserialize)]
pub struct UpdateVisibilityRequest {
    pub visibility: String,
}

pub async fn update_group_visibility(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<UpdateVisibilityRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let requester_bot_id = resolve_group_member_caller(&state, &headers, &uri, &id).await?;
    let result = state
        .services
        .group_management
        .update_visibility(GroupUpdateVisibilityCommand {
            caller_actor_id: requester_bot_id.clone(),
            group_id: id.clone(),
            visibility: req.visibility.clone(),
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "updated": true,
        "group_id": id,
        "visibility": result.visibility,
        "changed_by": requester_bot_id,
    })))
}

pub async fn get_workspace(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, HttpAdapterError> {
    let result = state
        .services
        .group_query
        .get_workspace(GroupWorkspaceQueryCommand { group_id: id })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(
        serde_json::to_value(result.workspace).unwrap_or_default(),
    ))
}

pub async fn update_workspace(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    Json(workspace): Json<Workspace>,
) -> Result<Json<Value>, HttpAdapterError> {
    let result = state
        .services
        .group_management
        .update_workspace(GroupUpdateWorkspaceCommand {
            caller_actor_id: None,
            group_id: id.clone(),
            workspace,
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "updated": true,
        "group_id": result.group_id,
        "workspace": result.workspace,
    })))
}

pub async fn update_routing_policy(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(req): Json<UpdateRoutingPolicyRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let requester_bot_id = authenticated_bot_from_headers(&state, &headers).await?;
    let result = state
        .services
        .group_management
        .update_routing_policy(GroupRoutingPolicyCommand {
            caller_actor_id: Some(requester_bot_id),
            group_id: id,
            mode: req.mode,
            default_bot_final_delivery: req.default_bot_final_delivery,
            sender_routes: req.sender_routes,
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "ok": true,
        "routing_policy": result.routing_policy,
    })))
}

pub async fn put_participant_mode(
    State(state): State<HttpAppState>,
    Path((group_id, actor_id)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<PutParticipantModeRequest>,
) -> Response {
    let mut caller = match resolve_put_caller(&state, &headers, &uri).await {
        Ok(caller) => caller,
        Err(response) => return response,
    };
    if req.message_view_scope.is_some()
        && caller != actor_id
        && let Ok(manage_actor) =
            resolve_group_member_caller(&state, &headers, &uri, &group_id).await
    {
        caller = manage_actor;
    }

    let result = match state
        .services
        .group_management
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: caller,
            group_id,
            actor_id,
            mode: req.mode,
            message_view_scope: req.message_view_scope,
        })
        .await
    {
        Ok(result) => result,
        Err(error) => return group_use_case_error_to_mode_response(error).into_response(),
    };

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "success": true,
            "data": {
                "group_id": result.group_id,
                "actor_id": result.actor_id,
                "mode": result.mode,
                "message_view_scope": result.message_view_scope,
            }
        })),
    )
        .into_response()
}


pub async fn patch_group_settings(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    Json(body): Json<PatchGroupSettingsRequest>,
) -> impl IntoResponse {
    // Security: block callback URLs that resolve to private/local addresses
    // before delegating to the use case. The app service owns the
    // immutability / route-field-lock validation; the outbound URL guard is an
    // adapter-level network policy and lives at the route boundary.
    if let Some(ref spec_patch) = body.service_spec {
        if let Err(e) =
            validate_service_spec_callback_urls(&state.outbound_url_guard, spec_patch.as_ref())
        {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"error": e})),
            )
                .into_response();
        }
    }

    match state
        .services
        .group_management
        .patch_group_settings(GroupPatchSettingsCommand {
            group_id: group_id.clone(),
            service_spec: body.service_spec,
        })
        .await
    {
        Ok(result) => (
            StatusCode::OK,
            Json(serde_json::json!({
                "id": result.group_id,
                "service_spec": result.service_spec,
                "status": "ok",
            })),
        )
            .into_response(),
        Err(GroupUseCaseError::Conflict(message)) => {
            // The use case JSON-encodes a `GroupPatchSettingsConflict` into
            // the message; surface it verbatim so clients recover the detail.
            let conflict: Value = serde_json::from_str(&message)
                .unwrap_or_else(|_| serde_json::json!({"error": message}));
            (StatusCode::CONFLICT, Json(conflict)).into_response()
        }
        Err(GroupUseCaseError::Service(ServiceError::GroupNotFound(_))) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "group not found"})),
        )
            .into_response(),
        Err(error) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": error.to_string()})),
        )
            .into_response(),
    }
}
