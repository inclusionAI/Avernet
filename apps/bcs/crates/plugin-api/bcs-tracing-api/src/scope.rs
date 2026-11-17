//! Explicit operation-owned scope for futures, stream polls and blocking work.
use std::{fmt, future::Future, sync::Arc};
use crate::{Carrier, Context, NetworkTracer, AMBIENT, CURRENT, SCOPED_PROVIDER};

#[derive(Clone)]
pub struct CapturedContext {
    pub(crate) context: Option<Context>,
    pub(crate) provider: Option<Arc<dyn NetworkTracer>>,
    pub(crate) ambient: Carrier,
}

impl fmt::Debug for CapturedContext {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CapturedContext").field("active", &self.context.is_some()).finish_non_exhaustive()
    }
}

impl CapturedContext {
    pub fn capture() -> Self {
        let provider = crate::provider();
        Self { context: crate::current_context(), ambient: crate::capture_ambient(provider.as_deref()), provider }
    }

    pub fn context(&self) -> Option<Context> { self.context.clone() }

    pub async fn scope<F: Future>(&self, future: F) -> F::Output {
        SCOPED_PROVIDER.scope(self.provider.clone(),
            CURRENT.scope(self.context.clone(), AMBIENT.scope(self.ambient.clone(), future))).await
    }

    /// Owned counterpart for tasks whose future outlives the caller.
    pub async fn scope_owned<F: Future>(self, future: F) -> F::Output {
        self.scope(future).await
    }

    /// Restores task-local state only for this synchronous call (e.g. poll_next).
    /// No guard escapes into an await or remains active between stream polls.
    pub fn scope_sync<T>(&self, work: impl FnOnce() -> T) -> T {
        SCOPED_PROVIDER.sync_scope(self.provider.clone(), ||
            CURRENT.sync_scope(self.context.clone(), || AMBIENT.sync_scope(self.ambient.clone(), work)))
    }
}
