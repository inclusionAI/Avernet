//! Recording plugin installed only by the test-utils process composition.
//! Completion stays in memory; file I/O happens after graceful server shutdown.
use std::{path::Path, sync::{Arc, Mutex, atomic::{AtomicU64, Ordering}}};
use bcs_tracing_api::{Carrier, Kind, NetworkTracer, Outcome, Record, RequestInfo, StartedTrace};
use serde_json::{Value, json};

#[derive(Default)]
pub struct Recorder {
    records: Arc<Mutex<Vec<Value>>>,
    sequence: AtomicU64,
}

struct TraceContext { trace_id: String, span_id: u64 }
struct Completion { records: Arc<Mutex<Vec<Value>>>, metadata: Value }

impl Record for Completion {
    fn finish(mut self: Box<Self>, outcome: Outcome, response_size: Option<u64>) {
        self.metadata["outcome"] = json!(format!("{outcome:?}"));
        self.metadata["response_size"] = json!(response_size);
        if let Ok(mut records) = self.records.lock() { records.push(self.metadata); }
    }
}

impl NetworkTracer for Recorder {
    fn start(&self, info: &RequestInfo<'_>, incoming: &Carrier, parent: bcs_tracing_api::ParentContext<'_>) -> StartedTrace {
        let span_id = self.sequence.fetch_add(1, Ordering::Relaxed) + 1;
        let parent = parent.local.and_then(|context| context.downcast_ref::<TraceContext>());
        let trace_id = parent.map(|context| context.trace_id.clone())
            .or_else(|| incoming.iter().find(|(name, _)| name == "x-e2e-trace-id").map(|(_, value)| value.clone()))
            .unwrap_or_else(|| format!("e2e-{span_id}"));
        let kind = match info.kind { Kind::HttpServer => "http_server", Kind::HttpClient => "http_client", Kind::RpcClient => "rpc_client" };
        let metadata = json!({
            "kind": kind, "method": info.method, "endpoint": info.endpoint,
            "request_size": info.request_size, "trace_id": trace_id,
            "span_id": span_id, "parent_span_id": parent.map(|context| context.span_id),
        });
        StartedTrace {
            remove_headers: vec![],
            context: Arc::new(TraceContext { trace_id: trace_id.clone(), span_id }),
            headers: vec![("x-e2e-trace-id".into(), trace_id), ("x-e2e-span-id".into(), span_id.to_string())],
            record: Box::new(Completion { records: self.records.clone(), metadata }),
        }
    }
}

impl Recorder {
    pub fn write(&self, path: &Path) -> anyhow::Result<()> {
        let records = self.records.lock().map_err(|_| anyhow::anyhow!("trace recorder lock poisoned"))?;
        serde_json::to_writer(std::fs::File::create(path)?, &*records)?;
        Ok(())
    }
}

pub fn install() -> anyhow::Result<Arc<Recorder>> {
    let recorder = Arc::new(Recorder::default());
    bcs_tracing_api::install(recorder.clone()).map_err(anyhow::Error::msg)?;
    Ok(recorder)
}
