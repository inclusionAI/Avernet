use axum::{
    routing::{delete, get, patch, post, put, MethodRouter},
    Router,
};
use bcs_channel_api::ChannelHttpMethod;

use crate::routes;
use crate::state::HttpAppState;

pub fn build_router(state: HttpAppState) -> Router {
    mount_channel_http_ingress(build_api_routes(), &state)
        .route("/health", get(routes::health::health))
        .with_state(state)
}

pub fn build_api_router(state: HttpAppState) -> Router {
    mount_channel_http_ingress(build_api_routes(), &state).with_state(state)
}

fn mount_channel_http_ingress(
    mut router: Router<HttpAppState>,
    state: &HttpAppState,
) -> Router<HttpAppState> {
    let Some(ingress) = state.channel_http_ingress.as_ref() else {
        return router;
    };
    for spec in ingress.route_specs() {
        router = router.route(&spec.path, channel_http_method_router(spec.method));
    }
    router
}

fn channel_http_method_router(method: ChannelHttpMethod) -> MethodRouter<HttpAppState> {
    match method {
        ChannelHttpMethod::Get => get(routes::channel::provider_http_ingress),
        ChannelHttpMethod::Post => post(routes::channel::provider_http_ingress),
        ChannelHttpMethod::Put => put(routes::channel::provider_http_ingress),
        ChannelHttpMethod::Patch => patch(routes::channel::provider_http_ingress),
        ChannelHttpMethod::Delete => delete(routes::channel::provider_http_ingress),
    }
}

fn build_api_routes() -> Router<HttpAppState> {
    Router::new()
        .route("/manifest", get(routes::manifest::get_manifest))
        .route(
            "/assets/{bundle_name}/{file_name}",
            get(routes::assets::manifest_asset),
        )
        .route("/me", get(routes::me::me_identity))
        .route("/me/repair-info", get(routes::me::me_repair_info))
        .route("/me/ensure-human", post(routes::me::ensure_mine))
        .route("/bots", get(routes::bots::list_bots))
        .route("/bots/connect", post(routes::bots::connect_bot))
        .route("/bots/discover", get(routes::discover::discover_bots))
        .route("/bots/my", get(routes::bots::list_my_bots))
        .route("/bots/onboard", post(routes::onboard::onboard_bot))
        .route("/bots/paged", get(routes::bots::list_bots_paged))
        .route("/bots/query", post(routes::bots::query_bots))
        .route("/bots/status", post(routes::bots::update_bot_status))
        .route("/bots/{id}", get(routes::bots::get_bot).delete(routes::bots::leave_bot))
        .route("/providers", post(routes::providers::register_provider))
        .route(
            "/providers/agentpass/resolve",
            post(routes::providers::resolve_agentpass_bot),
        )
        .route(
            "/providers/stream-gray",
            get(routes::providers::get_provider_stream_gray)
                .put(routes::providers::put_provider_stream_gray),
        )
        .route(
            "/providers/{provider_id}",
            get(routes::providers::get_provider).patch(routes::providers::patch_provider),
        )
        .route(
            "/providers/{provider_id}/organizations",
            get(routes::organizations::list_organizations)
                .post(routes::organizations::create_organization),
        )
        .route(
            "/providers/{provider_id}/organizations/{organization_code}",
            get(routes::organizations::get_organization)
                .patch(routes::organizations::patch_organization),
        )
        .route(
            "/providers/{provider_id}/organizations/{organization_code}/members",
            get(routes::organizations::list_members),
        )
        .route(
            "/providers/{provider_id}/organizations/{organization_code}/members/{bot_uuid}",
            get(routes::organizations::get_member)
                .put(routes::organizations::put_member)
                .delete(routes::organizations::delete_member),
        )
        .route(
            "/providers/{provider_id}/organization-candidate-bots",
            get(routes::organizations::candidate_bots),
        )
        .route("/bot/events", post(routes::bot_events::post_bot_event))
        .route(
            "/bot/events/coordination",
            post(routes::bot_events::post_coordination_event),
        )
        .route(
            "/channels/bindings",
            get(routes::channel::list_bindings).post(routes::channel::create_binding),
        )
        .route(
            "/channels/bindings/{id}",
            patch(routes::channel::set_binding_status)
                .delete(routes::channel::delete_binding),
        )
        .route(
            "/providers/{provider_id}/bots",
            get(routes::providers::list_provider_bots)
                .post(routes::providers::register_provider_bot),
        )
        .route(
            "/providers/{provider_id}/bots/{bot_uuid}",
            delete(routes::providers::delete_provider_bot),
        )
        .route(
            "/providers/{provider_id}/disable",
            post(routes::providers::disable_provider),
        )
        .route(
            "/providers/{provider_id}/enable",
            post(routes::providers::enable_provider),
        )
        .route(
            "/providers/{provider_id}/delivery/switch-bot",
            post(routes::providers::switch_bot_delivery),
        )
        .route("/bots/{id}/friends", get(routes::friends::list_friends))
        .route("/bots/{id}/groups", get(routes::groups::list_bot_groups))
        .route(
            "/bots/{id}/visibility",
            get(routes::bots::get_visibility).put(routes::bots::set_visibility),
        )
        .route("/bots/{id}/chat", post(routes::bot_chat::bot_chat))
        .route("/bots/{id}/chat-async", post(routes::bot_chat::bot_chat_async))
        .route("/admin/bots/onboard", post(routes::onboard::admin_onboard_bot))
        .route("/admin/secret/{name}", get(routes::secret::pull_secret))
        .route("/actors/list", get(routes::actors::list_actors))
        .route("/actors/search", get(routes::actors::search_actors))
        .route("/actors/{aid}/status", put(routes::actors::put_actor_status))
        .route("/friends/request", post(routes::friends::create_friend_request))
        .route("/friends/requests", get(routes::friends::list_friend_requests))
        .route(
            "/friends/requests/{id}/accept",
            post(routes::friends::accept_friend_request),
        )
        .route(
            "/friends/requests/{id}/reject",
            post(routes::friends::reject_friend_request),
        )
        .route("/groups", get(routes::groups::list_groups).post(routes::groups::create_group))
        .route(
            "/groups/{id}",
            get(routes::groups::get_group).delete(routes::groups::delete_group),
        )
        .route(
            "/groups/{id}/collaboration-definition",
            get(routes::groups::get_group_collaboration_definition)
                .patch(routes::groups::patch_group_collaboration_definition),
        )
        .route(
            "/groups/{id}/collaboration-definition/upgrade",
            post(routes::groups::upgrade_group_collaboration_definition),
        )
        .route(
            "/collaboration/templates",
            get(routes::templates::list_templates),
        )
        .route(
            "/collaboration/templates/{template_id}",
            get(routes::templates::get_template),
        )
        .route("/groups/request", post(routes::group_requests::group_request))
        .route(
            "/groups/{token}/confirm",
            get(routes::group_requests::confirm_group_page)
                .post(routes::group_requests::confirm_group),
        )
        .route("/groups/{id}/members", post(routes::groups::add_group_member))
        .route("/groups/{id}/members/{bot_uuid}", delete(routes::groups::remove_group_member))
        .route(
            "/groups/{id}/routing-policy",
            put(routes::groups::update_routing_policy),
        )
        .route("/groups/{id}/status", put(routes::groups::update_group_status))
        .route("/groups/{id}/terminate", post(routes::groups::terminate_group))
        .route("/groups/{id}/label", put(routes::groups::update_group_label))
        .route("/groups/{id}/visibility", put(routes::groups::update_group_visibility))
        .route(
            "/groups/{id}/workspace",
            get(routes::groups::get_workspace).put(routes::groups::update_workspace),
        )
        .route(
            "/groups/{id}/settings",
            patch(routes::groups::patch_group_settings),
        )
        .route("/groups/{id}/chat", post(routes::group_messages::group_chat))
        .route(
            "/groups/{id}/state-machine-runs",
            post(routes::collaboration_runs::start_state_machine_run),
        )
        .route("/groups/{id}/callback", post(routes::group_messages::group_callback))
        .route(
            "/groups/{id}/messages",
            get(routes::group_messages::get_messages).post(routes::group_messages::send_message),
        )
        .route("/groups/{id}/fuse", post(routes::messages::fuse_context))
        .route(
            "/groups/{gid}/participants/{aid}/mode",
            put(routes::groups::put_participant_mode),
        )
        .route("/chat/runs/{run_id}", get(routes::messages::get_chat_run))
        .route("/chat/runs/{run_id}/cancel", post(routes::messages::cancel_chat_run))
        .route(
            "/state-machine-runs/{run_id}",
            get(routes::collaboration_runs::get_state_machine_run),
        )
        .route(
            "/state-machine-runs/{run_id}/graph",
            get(routes::collaboration_runs::get_state_machine_run_graph),
        )
        .route(
            "/state-machine-runs/{run_id}/nodes/{node_id}",
            get(routes::collaboration_runs::get_state_machine_node_run),
        )
        .route(
            "/state-machine-runs/{run_id}/cancel",
            post(routes::collaboration_runs::cancel_state_machine_run),
        )
        .route("/onboard/url", get(routes::onboard::get_onboard_url))
        // Register routes (human-initiated bot registration)
        .route("/register/token", get(routes::register::get_register_token))
        .route("/register", post(routes::register::register_bot))
        // Session routes (Phase 2a)
        .route(
            "/groups/{id}/sessions",
            get(routes::sessions::list_sessions_for_group)
                .post(routes::sessions::create_session_for_group),
        )
        .route(
            "/sessions/{sid}",
            get(routes::sessions::get_session_by_id)
                .patch(routes::sessions::patch_session)
                .delete(routes::sessions::delete_session),
        )
        .route(
            "/sessions/{sid}/complete",
            post(routes::sessions::complete_session),
        )
        .route(
            "/sessions/{sid}/members",
            post(routes::sessions::add_session_participant),
        )
        .route(
            "/sessions/{sid}/members/{bot_uuid}",
            delete(routes::sessions::remove_session_participant)
                .patch(routes::sessions::update_session_participant_mode),
        )
        .route(
            "/sessions/{sid}/chat",
            post(routes::sessions::session_chat),
        )
        .route(
            "/sessions/{sid}/messages",
            get(routes::sessions::get_session_messages),
        )
        // Invite-link routes
        .route(
            "/groups/{id}/invite-link",
            post(routes::invite::create_group_invite_link),
        )
        .route(
            "/sessions/{sid}/invite-link",
            post(routes::invite::create_session_invite_link),
        )
        .route(
            "/groups/join/{token}",
            post(routes::invite::join_group_by_invite),
        )
        .route(
            "/sessions/join/{token}",
            post(routes::invite::join_session_by_invite),
        )
        // Service-invocation routes (Phase 2a)
        .route("/services/{group_id}/sessions",
            post(routes::services::post_invocation))
        .route("/services/{group_id}/sessions/{session_id}",
            get(routes::services::get_service_session))
}
