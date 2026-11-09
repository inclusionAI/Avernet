//! Optional network tracing contract. No vendor SDK, transport or business state.
use std::{any::Any, future::Future, sync::{Arc, OnceLock}};

pub type Carrier = Vec<(String, String)>;
pub type Context = Arc<dyn Any + Send + Sync>;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind { HttpServer, HttpClient, RpcClient }

/// Transport metadata only: never supply bodies, credentials or raw errors.
pub struct RequestInfo<'a> {
    pub kind: Kind,
    pub method: &'a str,
    /// Sanitized URL (no credentials/query/fragment), or RPC service name.
    pub endpoint: &'a str,
    /// Matched HTTP server route template; absent for client/RPC calls.
    pub route: Option<&'a str>,
    pub target_app: &'a str,
    pub request_size: Option<u64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome { Http(u16), Rpc(u32), TransportError, Timeout, Cancelled }

pub trait Record: Send {
    /// Called exactly once, including cancellation. Must not block or panic.
    fn finish(self: Box<Self>, outcome: Outcome, response_size: Option<u64>);
}

pub struct StartedTrace {
    pub context: Context,
    /// Exact propagation fields owned by this provider to remove before injecting
    /// `headers` on clients. Never include another protocol's fields or routing
    /// metadata. Servers retain the original incoming carrier.
    pub remove_headers: Vec<String>,
    pub headers: Carrier,
    pub record: Box<dyn Record>,
}

/// Separate parent sources: adapters never merge ambient context into headers.
#[derive(Clone, Copy)]
pub struct ParentContext<'a> {
    pub local: Option<&'a Context>,
    /// Current SDK/business context, or its explicitly captured spawn snapshot.
    /// Client providers prefer valid fields of their protocol here over a local
    /// ancestor, then fall back to local context, existing headers, or a root.
    /// Server providers extract the remote parent from incoming headers instead.
    pub ambient: &'a Carrier,
}

/// Trusted in-process plugin. Install once at bootstrap before serving requests.
/// No provider means no SPI records or propagation changes; independent tracing
/// continues normally. Implementations must preserve business results and keep
/// contexts safe across concurrent tasks.
pub trait NetworkTracer: Send + Sync {
    fn start(&self, info: &RequestInfo<'_>, incoming: &Carrier, parent: ParentContext<'_>) -> StartedTrace;

    /// Optional SDK bridge. Return only valid propagation fields, never bind a
    /// thread-local guard across await. The default preserves SPI-only behavior.
    fn capture_ambient(&self) -> Carrier { Vec::new() }
}

static PROVIDER: OnceLock<Arc<dyn NetworkTracer>> = OnceLock::new();
tokio::task_local! {
    static CURRENT: Option<Context>;
    static SCOPED_PROVIDER: Option<Arc<dyn NetworkTracer>>;
    static AMBIENT: Carrier;
}

pub fn install(provider: Arc<dyn NetworkTracer>) -> Result<(), &'static str> {
    PROVIDER.set(provider).map_err(|_| "network tracer already installed")
}

/// Adapters can bypass carrier allocation entirely in the default no-op mode.
pub fn is_enabled() -> bool { provider().is_some() }

/// Scoped composition override, also isolates conformance tests from globals.
pub async fn with_provider<F: Future>(provider: Option<Arc<dyn NetworkTracer>>, future: F) -> F::Output {
    SCOPED_PROVIDER.scope(provider, future).await
}

pub fn current_context() -> Option<Context> {
    CURRENT.try_with(Clone::clone).ok().flatten()
}

pub async fn with_context<F: Future>(context: Option<Context>, future: F) -> F::Output {
    CURRENT.scope(context, future).await
}

/// Capture before spawn, then poll the returned future inside the new task.
/// Explicit only: durable queues and WebSocket messages need separate contracts.
pub fn in_current_context<F: Future>(future: F) -> impl Future<Output = F::Output> {
    let context = current_context();
    let provider = provider();
    let ambient = capture_ambient(provider.as_deref());
    async move { with_provider(provider, with_context(context, AMBIENT.scope(ambient, future))).await }
}

/// Capture at the async caller before handing work to spawn_blocking.
pub fn in_current_context_blocking<F: FnOnce() -> R, R>(work: F) -> impl FnOnce() -> R {
    let context = current_context();
    let provider = provider();
    let ambient = capture_ambient(provider.as_deref());
    move || SCOPED_PROVIDER.sync_scope(provider, || CURRENT.sync_scope(context, || AMBIENT.sync_scope(ambient, work)))
}

fn capture_ambient(provider: Option<&dyn NetworkTracer>) -> Carrier {
    let Some(provider) = provider else { return Vec::new(); };
    let current = provider.capture_ambient();
    if current.is_empty() { AMBIENT.try_with(Clone::clone).unwrap_or_default() } else { current }
}

fn provider() -> Option<Arc<dyn NetworkTracer>> {
    SCOPED_PROVIDER.try_with(Clone::clone).unwrap_or_else(|_| PROVIDER.get().cloned())
}

pub struct Call(Option<StartedTrace>);

impl Call {
    pub fn start(info: &RequestInfo<'_>, incoming: &Carrier) -> Self {
        Self(provider().map(|provider| {
            let ambient = capture_ambient(Some(provider.as_ref()));
            provider.start(info, incoming, ParentContext { local: current_context().as_ref(), ambient: &ambient })
        }))
    }

    pub fn context(&self) -> Option<Context> { self.0.as_ref().map(|trace| trace.context.clone()) }
    pub fn headers(&self) -> &[(String, String)] {
        self.0.as_ref().map(|trace| trace.headers.as_slice()).unwrap_or_default()
    }
    pub fn remove_headers(&self) -> &[String] {
        self.0.as_ref().map(|trace| trace.remove_headers.as_slice()).unwrap_or_default()
    }
    pub fn finish(mut self, outcome: Outcome, response_size: Option<u64>) {
        if let Some(trace) = self.0.take() { trace.record.finish(outcome, response_size); }
    }
}

impl Drop for Call {
    fn drop(&mut self) {
        if let Some(trace) = self.0.take() { trace.record.finish(Outcome::Cancelled, None); }
    }
}
