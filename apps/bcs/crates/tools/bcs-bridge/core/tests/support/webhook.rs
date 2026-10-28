use super::support::spawn_app_with_state;

pub async fn spawn_app(toml_text: &str) -> String {
    spawn_app_with_state(toml_text).await.0
}

/// 从 SSE 文本抽取所有 data 帧里的 seq 序列（按出现顺序）。非 data 行/非
/// JSON/无 seq 字段均跳过；用于断言 seq 单调递增。
pub fn extract_seqs(sse_text: &str) -> Vec<u64> {
    sse_text
        .lines()
        .filter_map(|l| l.strip_prefix("data: "))
        .filter_map(|d| serde_json::from_str::<serde_json::Value>(d).ok())
        .filter_map(|v| v["seq"].as_u64())
        .collect()
}

/// 从 SSE 文本抽取首个 interaction 帧的 `interactionId`。用于端到端
/// interaction 回环测试：BCS 侧拿到 iid 再调 `interaction.resolve`。
pub fn extract_first_interaction_id(sse_text: &str) -> String {
    sse_text
        .lines()
        .filter_map(|l| l.strip_prefix("data: "))
        .filter_map(|d| serde_json::from_str::<serde_json::Value>(d).ok())
        .find(|v| v["interactionId"].is_string())
        .and_then(|v| v["interactionId"].as_str().map(str::to_string))
        .expect("interaction requested frame present")
}
