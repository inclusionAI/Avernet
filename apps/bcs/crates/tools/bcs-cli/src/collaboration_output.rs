//! Text projection of server-owned execution metadata. JSON output stays intact.
use serde_json::Value;
use std::fmt::Write;

fn text<'a>(value: &'a Value, key: &str) -> &'a str {
    value.get(key).and_then(Value::as_str).unwrap_or("-")
}

fn render_loop_path(output: &mut String, value: &Value) {
    let Some(path) = value.get("loop_path").and_then(Value::as_array) else { return; };
    let frames = path.iter().map(|frame| format!("{} {}/{}", text(frame, "loop_id"),
        frame["iteration"], frame["max_iterations"])).collect::<Vec<_>>();
    let _ = writeln!(output, "  Loop path: {}", frames.join(" → "));
}

pub fn render(value: &Value) -> String {
    let mut output = String::new();
    if let Some(run) = value.get("run") {
        let _ = writeln!(output, "Run {} · {}", text(run, "run_id"), text(run, "status"));
    }
    let definition = value.get("definition").unwrap_or(value);
    if let Some(mode) = definition.get("execution_graph_mode").and_then(Value::as_str) {
        let _ = writeln!(output, "Graph: {} → {mode}", text(definition, "graph_mode"));
    }
    let nodes = value.get("nodes").and_then(Value::as_array).or_else(|| value.as_array());
    let single = value.get("node");
    for node in nodes.into_iter().flatten().chain(single) {
        let id = text(node, "node_id");
        let execution = node.get("execution").or_else(|| value.get("node_execution_metadata").and_then(|map| map.get(id)))
            .or_else(|| single.and_then(|_| value.get("execution")));
        let _ = writeln!(output, "Node {id} · {} · {} · attempt {}", text(node, "display_name"), text(node, "status"), node.get("attempt").map_or("-".into(), Value::to_string));
        if let Some(execution) = execution {
            let _ = writeln!(output, "  Logical {} · Loop {} · iteration {}/{}", text(execution, "definition_node_id"), text(execution, "loop_id"), execution["iteration"], execution["max_iterations"]);
            render_loop_path(&mut output, execution);
        }
        if let Some(outcome) = node.get("outcome") { let _ = writeln!(output, "  Actual outcome: {outcome}"); }
        if let Some(context) = node.get("loop_context") {
            let _ = writeln!(output, "  Loop {} · iteration {}/{}", text(context, "loop_id"), context["iteration"], context["max_iterations"]);
            render_loop_path(&mut output, context);
            let previous = &context["previous_result"];
            if previous.is_null() { output.push_str("  First iteration: no previous result\n"); }
            else {
                let _ = writeln!(output, "  Previous {} · outcome {}\n{}", text(previous, "execution_node_id"), text(previous, "outcome"), text(previous, "output"));
            }
        }
        if let Some(reference) = node.get("response_ref").and_then(Value::as_str) {
            let _ = writeln!(output, "  Response ref: {reference}\n  Reply using --node {id}");
        }
        if let Some(instruction) = node.get("instruction").and_then(Value::as_str) { let _ = writeln!(output, "  {instruction}"); }
    }
    if let Some(edges) = value.get("edges").and_then(Value::as_array) {
        for edge in edges {
            if let Some(route) = edge.get("loop_route") {
                let _ = writeln!(output, "Route {} → {} · {} / {} (actual: {})", text(edge, "source"), text(edge, "target"), text(route, "kind"), text(route, "logical_outcome"), text(edge, "outcome"));
            }
        }
    }
    output
}
