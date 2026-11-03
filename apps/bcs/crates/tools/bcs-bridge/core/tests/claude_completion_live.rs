//! Manual integration: requires the pinned launcher and configured model access.
use std::sync::Arc;
use bcs_bridge_core::engine::{claude_code::ClaudeCodeFactory, EngineFactory, SessionObserver, TurnRequest};
use tokio_util::sync::CancellationToken;
struct Observer;
#[async_trait::async_trait]
impl SessionObserver for Observer { async fn established(&self, _: &str) -> Result<(), String> { Ok(()) } }

#[tokio::test]
#[ignore = "requires CFUSE_TEST_BIN pinned to cfuse 2.6.44 and model access"]
async fn native_live_completion() {
    let bin = std::env::var("CFUSE_TEST_BIN").expect("explicit pinned launcher required");
    let prompts = [
        ("Reply BASIC_DONE exactly. Do not use tools.", "BASIC_DONE"),
        ("Isolated lifecycle test. Use Bash exactly once: sleep 12; printf CHILD_DONE, with run_in_background=true. Immediately reply SPAWNED without waiting or polling. When completion notification arrives reply BASH_DONE exactly. No other tools or files.", "BASH_DONE"),
        ("Isolated lifecycle test. Use Bash sleep 2; printf CHILD_DONE with run_in_background=true. Immediately then Bash sleep 9; printf MAIN_DONE with timeout=20000. After the second command completes reply MIDTURN_DONE exactly. No polling or files.", "MIDTURN_DONE"),
        ("Isolated lifecycle test. Use Bash exactly once: sleep 2; printf QUEUED_CHILD_DONE with run_in_background=true. Immediately output integers 1 through 200 one per line then INITIAL_END. Do not wait, poll, or stop it. Only after the actual completion notification arrives reply QUEUED_DONE exactly. No other tools or files.", "QUEUED_DONE"),
    ];
    for (prompt, expected) in prompts {
        let cwd = tempfile::tempdir().unwrap();
        let args = ["--cc", "--skip-update", "--strict-mcp-config", "--mcp-config", "{\"mcpServers\":{}}", "--disable-slash-commands", "--setting-sources", "", "--tools", "Bash"];
        let factory = ClaudeCodeFactory::custom("live", "cfuse", args.into_iter().map(str::to_owned).collect());
        let engine = factory.build(bin.clone().into(), &toml::Table::new()).unwrap();
        let mut req = TurnRequest::new("live", prompt, cwd.path(), Arc::new(Observer));
        req.model = Some("antchat/GLM-5.3-Flash".into());
        req.permission_mode = Some("bypassPermissions".into());
        let (tx, mut rx) = tokio::sync::mpsc::channel(64);
        let drain = tokio::spawn(async move { while rx.recv().await.is_some() {} });
        let outcome = tokio::time::timeout(std::time::Duration::from_secs(90), engine.run_turn(req, tx, CancellationToken::new()))
            .await.expect("bounded live run").expect(expected);
        drain.await.unwrap();
        assert_eq!(outcome.final_text.as_deref().map(str::trim), Some(expected));
        eprintln!("verified {expected}");
        if expected == "BASIC_DONE" {
            let mut resume = TurnRequest::new("live-resume", "Reply RESUMED_DONE exactly. Do not use tools.", cwd.path(), Arc::new(Observer));
            resume.engine_session_id = outcome.engine_session_id;
            resume.model = Some("antchat/GLM-5.3-Flash".into());
            resume.permission_mode = Some("bypassPermissions".into());
            let (tx, mut rx) = tokio::sync::mpsc::channel(64);
            let drain = tokio::spawn(async move { while rx.recv().await.is_some() {} });
            let resumed = tokio::time::timeout(std::time::Duration::from_secs(90), engine.run_turn(resume, tx, CancellationToken::new()))
                .await.unwrap().unwrap();
            drain.await.unwrap();
            assert_eq!(resumed.final_text.as_deref().map(str::trim), Some("RESUMED_DONE"));
            eprintln!("verified RESUMED_DONE");
        }
    }
}
