use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
};
use bcs_domain::{ActorKind, Organization, OrganizationMember};
use bcs_protocol::{
    CreateOrganizationRequest, OrganizationCandidateBotListResponse,
    OrganizationCandidateBotResponse, OrganizationListResponse, OrganizationMemberListResponse,
    OrganizationMemberBotResponse, OrganizationMemberDetailResponse, OrganizationMemberResponse,
    OrganizationMemberProfileResponse, OrganizationResponse,
    PatchOrganizationMemberProfileRequest, PatchOrganizationRequest, PutOrganizationMemberRequest,
};
use bcs_service_api::{
    CreateOrganizationCommand, OrganizationAuth, OrganizationCandidateBot,
    OrganizationCandidatePageQuery, OrganizationCandidateQuery, OrganizationMemberAuth,
    OrganizationMemberBotDetail, OrganizationMemberDetail, OrganizationMemberPageQuery,
    OrganizationMemberProfilePatch, PutOrganizationMemberCommand,
    ServiceError, UpdateOrganizationCommand, UpdateOrganizationMemberProfileCommand,
};
use serde::Deserialize;

use crate::error::HttpAdapterError;
use crate::mapping::capabilities::{to_core_skill, to_wire_capabilities};
use crate::state::HttpAppState;

#[derive(Debug, Deserialize)]
pub struct ListOrganizationsQuery {
    #[serde(default)]
    include_disabled: bool,
}

#[derive(Debug, Deserialize)]
pub struct ListMembersQuery {
    #[serde(default)]
    include_disabled: bool,
    role: Option<String>,
    offset: Option<u64>,
    limit: Option<u64>,
}

#[derive(Debug, Deserialize)]
pub struct CandidateBotsQuery {
    q: Option<String>,
    provider_id: Option<String>,
    #[serde(default)]
    offset: Option<u64>,
    #[serde(default)]
    limit: Option<u64>,
}

pub async fn create_organization(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    Json(req): Json<CreateOrganizationRequest>,
) -> Result<Json<OrganizationResponse>, HttpAdapterError> {
    let auth = organization_auth(provider_id, &headers)?;
    let organization = state
        .services
        .organization_management
        .create(CreateOrganizationCommand {
            auth,
            organization_code: req.organization_code,
            name: req.name,
            description: req.description,
        })
        .await
        .map_err(organization_error)?;
    Ok(Json(organization_to_response(organization)))
}

pub async fn get_organization(
    State(state): State<HttpAppState>,
    Path((provider_id, organization_code)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<Json<OrganizationResponse>, HttpAdapterError> {
    let auth = organization_auth(provider_id, &headers)?;
    let organization = state
        .services
        .organization_management
        .get(auth, &organization_code)
        .await
        .map_err(organization_error)?;
    Ok(Json(organization_to_response(organization)))
}

pub async fn list_organizations(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    Query(query): Query<ListOrganizationsQuery>,
) -> Result<Json<OrganizationListResponse>, HttpAdapterError> {
    let auth = organization_auth(provider_id, &headers)?;
    let organizations = state
        .services
        .organization_management
        .list(auth, query.include_disabled)
        .await
        .map_err(organization_error)?;
    Ok(Json(OrganizationListResponse {
        organizations: organizations.into_iter().map(organization_to_response).collect(),
    }))
}

pub async fn patch_organization(
    State(state): State<HttpAppState>,
    Path((provider_id, organization_code)): Path<(String, String)>,
    headers: HeaderMap,
    Json(req): Json<PatchOrganizationRequest>,
) -> Result<Json<OrganizationResponse>, HttpAdapterError> {
    let auth = organization_auth(provider_id, &headers)?;
    let organization = state
        .services
        .organization_management
        .update(UpdateOrganizationCommand {
            auth,
            organization_code,
            name: req.name,
            description: req.description,
            disabled: req.disabled,
        })
        .await
        .map_err(organization_error)?;
    Ok(Json(organization_to_response(organization)))
}

pub async fn put_member(
    State(state): State<HttpAppState>,
    Path((organization_code, bot_uuid)): Path<(String, String)>,
    headers: HeaderMap,
    Json(req): Json<PutOrganizationMemberRequest>,
) -> Result<Json<OrganizationMemberResponse>, HttpAdapterError> {
    let auth = organization_member_auth(&headers)?;
    let member = state
        .services
        .organization_management
        .put_member(PutOrganizationMemberCommand {
            auth,
            organization_code,
            bot_uuid,
            role: req.role,
        })
        .await
        .map_err(organization_error)?;
    Ok(Json(member_to_response(member)))
}

pub async fn delete_member(
    State(state): State<HttpAppState>,
    Path((organization_code, bot_uuid)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<StatusCode, HttpAdapterError> {
    let auth = organization_member_auth(&headers)?;
    state
        .services
        .organization_management
        .delete_member(auth, &organization_code, &bot_uuid)
        .await
        .map_err(organization_error)?;
    Ok(StatusCode::NO_CONTENT)
}

pub async fn get_member(
    State(state): State<HttpAppState>,
    Path((organization_code, bot_uuid)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<Json<OrganizationMemberDetailResponse>, HttpAdapterError> {
    let auth = organization_member_auth(&headers)?;
    let member = state
        .services
        .organization_management
        .get_member_detail(auth, &organization_code, &bot_uuid)
        .await
        .map_err(organization_error)?
        .ok_or_else(|| HttpAdapterError::NotFound("organization member not found".to_string()))?;
    Ok(Json(member_detail_to_response(member)))
}

pub async fn list_members(
    State(state): State<HttpAppState>,
    Path(organization_code): Path<String>,
    headers: HeaderMap,
    Query(query): Query<ListMembersQuery>,
) -> Result<Json<OrganizationMemberListResponse>, HttpAdapterError> {
    let auth = organization_member_auth(&headers)?;
    let offset = query.offset.unwrap_or(0);
    let limit = query.limit.unwrap_or(50);
    if !(1..=200).contains(&limit) {
        return Err(HttpAdapterError::BadRequest(
            "limit must be between 1 and 200".to_string(),
        ));
    }
    let page = state
        .services
        .organization_management
        .list_members_page(
            auth,
            &organization_code,
            OrganizationMemberPageQuery {
                include_disabled: query.include_disabled,
                role: query.role,
                offset,
                limit,
            },
        )
        .await
        .map_err(organization_error)?;
    Ok(Json(OrganizationMemberListResponse {
        members: page.members.into_iter().map(member_to_response).collect(),
        offset: page.offset,
        limit: page.limit,
        total: page.total,
    }))
}

pub async fn patch_member_profile(
    State(state): State<HttpAppState>,
    Path((organization_code, bot_uuid)): Path<(String, String)>,
    headers: HeaderMap,
    payload: Result<Json<PatchOrganizationMemberProfileRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<OrganizationMemberProfileResponse>, HttpAdapterError> {
    let Json(req) = payload.map_err(|error| HttpAdapterError::BadRequest(error.body_text()))?;
    if req.name.is_none()
        && req.summary.is_none()
        && req.domains.is_none()
        && req.skills.is_none()
        && req.scopes.is_none()
    {
        return Err(HttpAdapterError::BadRequest(
            "at least one member profile field is required".to_string(),
        ));
    }
    let auth = organization_member_auth(&headers)?;
    let profile = state
        .services
        .organization_management
        .update_member_profile(UpdateOrganizationMemberProfileCommand {
            auth,
            organization_code,
            bot_uuid,
            patch: OrganizationMemberProfilePatch {
                name: req.name,
                summary: req.summary,
                domains: req.domains,
                skills: req
                    .skills
                    .map(|skills| skills.into_iter().map(to_core_skill).collect()),
                scopes: req.scopes,
            },
        })
        .await
        .map_err(organization_error)?;
    Ok(Json(OrganizationMemberProfileResponse {
        organization_code: profile.organization_code,
        bot_uuid: profile.bot_uuid,
        provider_id: profile.provider_id,
        capabilities: to_wire_capabilities(profile.capabilities),
    }))
}

pub async fn candidate_bots(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    Query(query): Query<CandidateBotsQuery>,
) -> Result<Json<OrganizationCandidateBotListResponse>, HttpAdapterError> {
    let auth = organization_auth(provider_id, &headers)?;
    let offset = query.offset.unwrap_or(0);
    let limit = query.limit.unwrap_or(50);
    if !(1..=200).contains(&limit) {
        return Err(HttpAdapterError::BadRequest("limit must be between 1 and 200".to_string()));
    }
    let page = state
        .services
        .organization_management
        .candidate_bots_page(
            auth,
            OrganizationCandidatePageQuery {
                candidate: OrganizationCandidateQuery { q: query.q, provider_id: query.provider_id },
                offset,
                limit,
            },
        )
        .await
        .map_err(organization_error)?;
    Ok(Json(OrganizationCandidateBotListResponse {
        bots: page.bots.into_iter().map(candidate_to_response).collect(),
        offset: page.offset,
        limit: page.limit,
        total: page.total,
    }))
}

fn organization_auth(
    provider_id: String,
    headers: &HeaderMap,
) -> Result<OrganizationAuth, HttpAdapterError> {
    Ok(OrganizationAuth {
        provider_id,
        provider_admin_token: bearer_token(headers)?,
    })
}

fn organization_member_auth(
    headers: &HeaderMap,
) -> Result<OrganizationMemberAuth, HttpAdapterError> {
    Ok(OrganizationMemberAuth {
        provider_admin_token: bearer_token(headers)?,
    })
}

fn bearer_token(headers: &HeaderMap) -> Result<String, HttpAdapterError> {
    crate::headers::extract_bearer_token(headers).ok_or_else(|| {
        HttpAdapterError::Unauthorized("valid provider admin token is required".to_string())
    })
}

fn organization_error(error: ServiceError) -> HttpAdapterError {
    HttpAdapterError::Service(error)
}

fn organization_to_response(organization: Organization) -> OrganizationResponse {
    OrganizationResponse {
        organization_code: organization.code,
        name: organization.name,
        description: organization.description,
        managing_provider_id: organization.managing_provider_id,
        disabled: organization.disabled,
    }
}

fn member_to_response(member: OrganizationMember) -> OrganizationMemberResponse {
    OrganizationMemberResponse {
        organization_code: member.organization_code,
        bot_uuid: member.bot_uuid,
        role: member.role,
        disabled: member.disabled,
    }
}

fn member_detail_to_response(detail: OrganizationMemberDetail) -> OrganizationMemberDetailResponse {
    OrganizationMemberDetailResponse {
        organization_code: detail.member.organization_code,
        bot_uuid: detail.member.bot_uuid,
        role: detail.member.role,
        disabled: detail.member.disabled,
        bot: detail.bot.map(member_bot_to_response),
    }
}

fn member_bot_to_response(bot: OrganizationMemberBotDetail) -> OrganizationMemberBotResponse {
    let capabilities = to_wire_capabilities(bot.capabilities);
    OrganizationMemberBotResponse {
        provider_id: bot.provider_id,
        provider_bot_ref: bot.provider_bot_ref,
        agent_code: bot.agent_code,
        name: capabilities.name,
        summary: capabilities.summary,
        domains: capabilities.domains,
        skills: capabilities.skills,
        scopes: capabilities.scopes,
        visibility: capabilities.visibility,
        created_by: bot.created_by,
        actor_kind: match bot.actor_kind {
            ActorKind::Bot => "bot",
            ActorKind::Human => "human",
        }
        .to_string(),
        env: bot.env,
    }
}

fn candidate_to_response(bot: OrganizationCandidateBot) -> OrganizationCandidateBotResponse {
    OrganizationCandidateBotResponse {
        bot_uuid: bot.bot_uuid,
        provider_id: bot.provider_id,
        capabilities: to_wire_capabilities(bot.capabilities),
    }
}
