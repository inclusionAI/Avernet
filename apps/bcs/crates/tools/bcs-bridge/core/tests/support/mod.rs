use std::{net::SocketAddr, sync::Arc};
use bcs_bridge_core::{config::ProviderConfig, engine::EngineRegistry, webhook, AppState};

/// 起服务并返回共享的 [`AppState`] 句柄，供测试
/// 直接编排会话状态（例如检查会话持久化状态）。
pub async fn spawn_app_with_state(toml_text: &str) -> (String, Arc<AppState>) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bridge.toml");
    // Each test owns its database; never use the developer's home default.
    let mut document: toml::Value = toml::from_str(toml_text).unwrap();
    document.as_table_mut().unwrap().entry("state_path")
        .or_insert_with(|| toml::Value::String(dir.path().join("bridge-state.sqlite3").to_string_lossy().into_owned()));
    std::fs::write(&path, toml::to_string(&document).unwrap()).unwrap();
    let config = ProviderConfig::load(&path).unwrap();
    // tempdir 不能 drop：泄漏到测试生命周期结束即可（测试进程退出清理）
    std::mem::forget(dir);
    let mut cfg = config;
    cfg.listen = Some("127.0.0.1:0".parse::<SocketAddr>().unwrap());
    let state = Arc::new(AppState::new(cfg, &EngineRegistry::builtin()).unwrap());
    let app = webhook::router(state.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (format!("http://{addr}"), state)
}

/// 用指定 mock 脚本作为引擎可执行文件起服务（engine 由参数选择 claude-code/codex 语义）。
///
/// 单 bot `worker-1`，engine_bin 指向 `tests/fixtures/{script}`；两个端到端
/// 测试都经此构造，避免真实 LLM 调用。
pub async fn spawn_app_with_mock(script: &str, engine: &str) -> String {
    spawn_app_with_mock_and_state(script, engine).await.0
}

/// [`spawn_app_with_mock`] 的 state-暴露版本：额外返回共享 [`AppState`]。
pub async fn spawn_app_with_mock_and_state(script: &str, engine: &str) -> (String, Arc<AppState>) {
    let bin = format!("{}/tests/fixtures/{script}", env!("CARGO_MANIFEST_DIR"));
    // These replay fixtures predate native Stop/idle and intentionally test legacy.
    let options = if engine == "claude-code" { "engine_options = { completion_mode = \"legacy\" }" } else { "" };
    spawn_app_with_state(&format!(
        r#"
provider_id = "bridge-1"
listen = "127.0.0.1:0"
bcs_to_provider_token = "tok-b2p"
[[bot]]
provider_bot_ref = "worker-1"
engine = "{engine}"
cwd = "/tmp"
engine_bin = "{bin}"
{options}
"#
    ))
    .await
}
