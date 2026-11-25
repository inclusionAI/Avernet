//! Explicit Agent-to-Agent invocation. No general application, self-asserted
//! source/lineage or anonymous registration can enter this capability.
use super::*;
use serde::Deserialize;

pub struct AgentInvokeDirectHttpServices {
    pub scope: AgentInvokeScope,
    pub tenant: Option<String>,
    pub service: Arc<dyn AgentInvokeDirectService>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct InvokeBody {
    to_bot_id: String,
    message: String,
    session_id: Option<String>,
    timeout_ms: Option<u64>,
}
pub(super) async fn handle(State(state): State<ApiState>, req: Request) -> Response {
    let id = RequestId::from_headers(req.headers());
    let result = async {
        if req.method() != axum::http::Method::POST {
            return Ok((
                StatusCode::METHOD_NOT_ALLOWED,
                Json(Envelope::success(
                    40500,
                    "Method not allowed",
                    serde_json::json!({"error_code":"method_not_allowed"}),
                    &id.0,
                )),
            )
                .into_response());
        }
        let caller = req
            .extensions()
            .get::<AuthenticatedCaller>()
            .ok_or(AgentInvokeError::Unauthenticated)?;
        let services = state
            .agent_invoke_direct
            .as_ref()
            .ok_or(AgentInvokeError::Unavailable)?;
        if caller.tenant != services.tenant {
            return Err(AgentInvokeError::NotFound);
        }
        // A separate verified Human principal is not owner display metadata.
        // Without trusted root-Agent lineage, Human+Bot is ambiguous delegation
        // and cannot be relabelled as originator==Bot by this direct-only API.
        if caller.user.is_some() {
            return Err(AgentInvokeError::Forbidden);
        }
        let bot = caller.bot.as_ref().ok_or(AgentInvokeError::Forbidden)?;
        let identity = AgentInvokeVerifiedBot {
            bot_id: bot.bot_uuid.clone(),
            agent_code: bot.agent_code.clone(),
        };
        dto::allowed(&dto::params(req.uri().query())?, &[])?;
        let body: InvokeBody = writes::body(req).await?;
        let accepted = services
            .service
            .invoke(AgentInvokeDirectCommand {
                identity,
                authenticated_scope: services.scope.clone(),
                to_bot_id: body.to_bot_id,
                message: body.message,
                session_id: body.session_id,
                timeout_ms: body.timeout_ms,
            })
            .await?;
        let data = serde_json::to_value(accepted).map_err(|_| AgentInvokeError::InvalidState)?;
        Ok(success(&id, StatusCode::ACCEPTED, data))
    }
    .await;
    result.unwrap_or_else(|error| failure(&id, error))
}
