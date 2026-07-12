use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
};
use bcs_domain::{Organization, OrganizationMember};
use bcs_protocol::{
    CreateOrganizationRequest, OrganizationCandidateBotListResponse,
    OrganizationCandidateBotResponse, OrganizationListResponse, OrganizationMemberListResponse,
    OrganizationMemberResponse, OrganizationResponse, PatchOrganizationRequest,
    PutOrganizationMemberRequest,
};
use bcs_service_api::{
    CreateOrganizationCommand, OrganizationAuth, OrganizationCandidateBot,
    OrganizationCandidateQuery, PutOrganizationMemberCommand, ServiceError,
    UpdateOrganizationCommand,
};
use serde::Deserialize;

use crate::error::HttpAdapterError;
use crate::mapping::capabilities::to_wire_capabilities;
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
}

#[derive(Debug, Deserialize)]
pub struct CandidateBotsQuery {
    q: Option<String>,
    provider_id: Option<String>,
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
    Path((provider_id, organization_code, bot_uuid)): Path<(String, String, String)>,
    headers: HeaderMap,
    Json(req): Json<PutOrganizationMemberRequest>,
) -> Result<Json<OrganizationMemberResponse>, HttpAdapterError> {
    let auth = organization_auth(provider_id, &headers)?;
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
    Path((provider_id, organization_code, bot_uuid)): Path<(String, String, String)>,
    headers: HeaderMap,
) -> Result<StatusCode, HttpAdapterError> {
    let auth = organization_auth(provider_id, &headers)?;
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
    Path((provider_id, organization_code, bot_uuid)): Path<(String, String, String)>,
    headers: HeaderMap,
) -> Result<Json<OrganizationMemberResponse>, HttpAdapterError> {
    let auth = organization_auth(provider_id, &headers)?;
    let member = state
        .services
        .organization_management
        .get_member(auth, &organization_code, &bot_uuid)
        .await
        .map_err(organization_error)?
        .ok_or_else(|| HttpAdapterError::NotFound("organization member not found".to_string()))?;
    Ok(Json(member_to_response(member)))
}

pub async fn list_members(
    State(state): State<HttpAppState>,
    Path((provider_id, organization_code)): Path<(String, String)>,
    headers: HeaderMap,
    Query(query): Query<ListMembersQuery>,
) -> Result<Json<OrganizationMemberListResponse>, HttpAdapterError> {
    let auth = organization_auth(provider_id, &headers)?;
    let members = state
        .services
        .organization_management
        .list_members(
            auth,
            &organization_code,
            query.include_disabled,
            query.role.as_deref(),
        )
        .await
        .map_err(organization_error)?;
    Ok(Json(OrganizationMemberListResponse {
        members: members.into_iter().map(member_to_response).collect(),
    }))
}

pub async fn candidate_bots(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    Query(query): Query<CandidateBotsQuery>,
) -> Result<Json<OrganizationCandidateBotListResponse>, HttpAdapterError> {
    let auth = organization_auth(provider_id, &headers)?;
    let bots = state
        .services
        .organization_management
        .candidate_bots(
            auth,
            OrganizationCandidateQuery {
                q: query.q,
                provider_id: query.provider_id,
            },
        )
        .await
        .map_err(organization_error)?;
    Ok(Json(OrganizationCandidateBotListResponse {
        bots: bots.into_iter().map(candidate_to_response).collect(),
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

fn candidate_to_response(bot: OrganizationCandidateBot) -> OrganizationCandidateBotResponse {
    OrganizationCandidateBotResponse {
        bot_uuid: bot.bot_uuid,
        provider_id: bot.provider_id,
        capabilities: to_wire_capabilities(bot.capabilities),
    }
}
