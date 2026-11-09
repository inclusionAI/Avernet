use super::*;
use serde_json::{Value, json};
pub struct GroupCatalog(pub Value);
fn graph(nodes: &Value) -> Value {
    let mut map = nodes.as_object().unwrap().clone();
    let ids: Vec<_> = map.keys().cloned().collect();
    for value in map.values_mut() {
        value["final_output"] = json!(false);
        value["transitions"] = json!({"complete":{"targets":["join"]}});
    }
    map.insert("entry".into(),json!({"kind":"human_input","display_name":"Start","instruction":"start","node_timeout_ms":60000,"transitions":{"complete":{"targets":ids}}}));
    map.insert("join".into(), human());
    Value::Object(map)
}
#[async_trait]
impl AgentCatalog for GroupCatalog {
    async fn resolve(
        &self,
        _: &CallerContext,
        target: &ExecutionTarget,
        id: &SessionId,
    ) -> RuntimeResult<SessionSpec> {
        Ok(SessionSpec {
            session_id: id.clone(),
            target: target.clone(),
            kind: RunnerKind::StructuredGroup,
            definition: Some(serde_json::from_value(json!({"nodes":graph(&self.0)})).unwrap()),
            bindings: BTreeMap::from([("agent".into(), binding())]),
            policy: ExecutionPolicy {
                execution_timeout_ms: 60000,
                human_timeout_ms: 60000,
                judge_available: true,
                max_pending_tasks: 16,
                max_graph_nodes: 32,
                max_agents: 5,
            },
        })
    }
}
pub fn bot() -> Value {
    json!({"kind":"bot_task","display_name":"Work","instruction":"work","final_output":true,"assignee":{"type":"bot_binding","binding":"agent"},"transitions":{}})
}
pub fn human() -> Value {
    json!({"kind":"human_input","display_name":"Question","instruction":"answer","node_timeout_ms":60000,"transitions":{}})
}
pub fn group_request(id: &str) -> SubmitRequest {
    let mut r = request(id);
    r.target = ExecutionTarget::Group {
        group_id: GroupId::new(scope(), "group"),
    };
    r
}
pub fn gateway(f: &Fixture, nodes: Value) -> DefaultGateway {
    DefaultGateway::new(
        f.ports.clone(),
        f.runners(),
        Arc::new(GroupCatalog(nodes)),
        vec![Arc::new(Allow)],
        f.clock.clone(),
        true,
    )
    .unwrap()
}

pub async fn start_parallel(
    _f: &Fixture,
    g: &DefaultGateway,
    w: &DefaultWorker,
    request: SubmitRequest,
) -> TaskView {
    use bcn_runtime_api::application::GatewayService;
    let task = g.submit(caller(), request).await.unwrap();
    w.step(&task.session_id).await.unwrap();
    let mut reply = group_request("start-reply");
    reply.message.task_id = Some(task.task_id.clone());
    reply.message.parts = vec![];
    g.submit(caller(), reply).await.unwrap();
    w.step(&task.session_id).await.unwrap();
    w.tick().await.unwrap();
    w.step(&task.session_id).await.unwrap();
    task
}
