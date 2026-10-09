mod routes;

use axum::Router;

use super::common::ApiState;

pub fn protected_router() -> Router<ApiState> {
    Router::new().nest(
        "/api/v1/collaboration",
        routes::bot::router()
            .merge(routes::collaboration_template::router())
            .merge(routes::collaboration_definition::router())
            .merge(routes::invite_code::router())
            .merge(routes::session_file::protected_router())
            .merge(routes::collaboration_run::protected_router()),
    )
}

pub fn public_router() -> Router<ApiState> {
    Router::new().nest(
        "/api/v1/collaboration",
        routes::session_file::public_router().merge(routes::manifest::public_router())
            .merge(routes::bot_self::router()),
    )
}

pub fn router() -> Router<ApiState> {
    protected_router().merge(public_router())
}

/// The trusted team-manager sources slice (plan Task 13, spec §6.1),
/// EXPLICITLY merged by `v1::router` OUTSIDE the generic
/// Principal/invite-code middleware boundary when (and only when) the
/// composition root armed the credential lane. The slice keeps the spec's
/// reserved `/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}`
/// path — it never becomes part of `/api/v1/collaboration` and never
/// becomes anonymous: every write under it must carry the verified
/// team-manager service credential.
pub fn team_manager_sources_router() -> Router<ApiState> {
    Router::new().nest("/api/v1/bots", routes::team_manager_sources::router())
}
