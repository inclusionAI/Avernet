use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::Session;

use crate::application::session::SessionManagementService;

/// Outbound port that fires the post-completion callback dispatch for a
/// finished service-invocation session. Delivery adapters depend on this port
/// instead of a core service trait directly.
#[async_trait]
pub trait SessionCallbackDispatchPort: Send + Sync {
    async fn maybe_dispatch(
        &self,
        session: Session,
        session_management: Arc<dyn SessionManagementService>,
    );
}
