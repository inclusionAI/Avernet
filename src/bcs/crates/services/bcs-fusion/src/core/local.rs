//! Context Fusion Service Implementation.
//!
//! This crate provides the concrete implementation of `FusionCoreService`
//! for fusing contexts from multiple bots.


use std::path::{Path, PathBuf};

use async_trait::async_trait;
use tracing::{debug, info, warn};

use bcs_service_api::{
    ContextBotSummary, ContextFusionRequest, ContextFusionResponse, ContextParticipantPerspective,
    FusionCoreService, ServiceError, ServiceResult,
};

/// Local fallback fusion service for combining bot contexts without bcsfuse.
pub struct LocalFusionService {
    /// Base directory for bot contexts.
    bots_base_dir: std::path::PathBuf,
    /// LLM client for fusion (optional, uses simple merge if not configured).
    llm_client: Option<Box<dyn LlmClient>>,
}

impl std::fmt::Debug for LocalFusionService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LocalFusionService")
            .field("bots_base_dir", &self.bots_base_dir)
            .field("llm_client", &self.llm_client.is_some())
            .finish()
    }
}

/// Trait for LLM client abstraction.
#[async_trait::async_trait]
pub trait LlmClient: Send + Sync {
    /// Complete a prompt.
    async fn complete(&self, prompt: &str) -> ServiceResult<String>;
}

impl LocalFusionService {
    /// Create a new local fusion service.
    pub fn new(bots_base_dir: impl Into<std::path::PathBuf>) -> Self {
        Self {
            bots_base_dir: bots_base_dir.into(),
            llm_client: None,
        }
    }

    /// Set the LLM client.
    pub fn with_llm_client(mut self, client: Box<dyn LlmClient>) -> Self {
        self.llm_client = Some(client);
        self
    }

    /// Fuse contexts using LLM.
    async fn fuse_with_llm(
        &self,
        client: &dyn LlmClient,
        request: &ContextFusionRequest,
        contexts: &[ContextBotSummary],
    ) -> ServiceResult<ContextFusionResponse> {
        let prompt = build_fusion_prompt(request, contexts);
        debug!(prompt_len = prompt.len(), "Built fusion prompt");

        let response_text = client.complete(&prompt).await?;
        parse_fusion_response(&response_text)
    }

    /// Simple fusion without LLM (just concatenate contexts).
    fn simple_fusion(
        &self,
        request: &ContextFusionRequest,
        contexts: &[ContextBotSummary],
    ) -> ServiceResult<ContextFusionResponse> {
        let perspectives: Vec<_> = contexts
            .iter()
            .map(|ctx| ContextParticipantPerspective {
                bot_uuid: ctx.bot_uuid.clone(),
                name: ctx.display_name().to_string(),
                emoji: ctx.display_emoji().to_string(),
                summary: format!(
                    "Context from {}:\n{}\n{}",
                    ctx.display_name(),
                    ctx.soul.as_deref().unwrap_or("No soul defined"),
                    ctx.memory.as_deref().unwrap_or("No memory defined")
                ),
                key_points: vec![],
                concerns: ctx
                    .rules
                    .as_ref()
                    .map(|r| r.lines().map(|l| l.to_string()).collect())
                    .unwrap_or_default(),
                role: None,
                confidence: None,
                status: None,
                participant_type: Some("bot".to_string()),
                evidence: None,
            })
            .collect();

        Ok(ContextFusionResponse {
            perspectives,
            conflicts: vec![],
            alignment_points: vec![],
            recommendation: Some(format!("Simple fusion for: {}", request.question)),
            key_insights: vec![],
            extra: None,
        })
    }
}

pub fn load_bot_context(bots_base_dir: &Path, bot_id: &str) -> ServiceResult<ContextBotSummary> {
    let bot_dir = resolve_bot_dir(bots_base_dir, bot_id)?;

    debug!(bot_id = %bot_id, bot_dir = %bot_dir.display(), "Loading bot context");

    let identity = read_file_opt(&bot_dir.join("IDENTITY.md"));
    let soul = read_file_opt(&bot_dir.join("SOUL.md"));
    let rules = read_file_opt(&bot_dir.join("RULES.md"));
    let memory = read_file_opt(&bot_dir.join("MEMORY.md"));
    let tools = read_file_opt(&bot_dir.join("TOOLS.md"));
    let agents = read_file_opt(&bot_dir.join("AGENTS.md"));

    info!(
        bot_id = %bot_id,
        bot_dir = %bot_dir.display(),
        identity_len = identity.as_ref().map(|s| s.len()).unwrap_or(0),
        soul_len = soul.as_ref().map(|s| s.len()).unwrap_or(0),
        rules_len = rules.as_ref().map(|s| s.len()).unwrap_or(0),
        memory_len = memory.as_ref().map(|s| s.len()).unwrap_or(0),
        tools_len = tools.as_ref().map(|s| s.len()).unwrap_or(0),
        agents_len = agents.as_ref().map(|s| s.len()).unwrap_or(0),
        "Loaded bot context files"
    );

    // Extract name and emoji from identity frontmatter
    let (name, emoji) = parse_identity_frontmatter(identity.as_deref().unwrap_or(""));

    Ok(ContextBotSummary {
        bot_uuid: bot_id.to_string(),
        name,
        emoji,
        identity,
        soul,
        rules,
        memory,
        tools,
        agents,
    })
}

/// Resolve the concrete directory for a bot in `bots_base_dir`.
///
/// The primary lookup is `bots_base_dir.join(bot_id)` (production layout where
/// directory name equals bot id). If that does not exist, fall back to scanning
/// immediate subdirectories and matching the bot by `name` or `display_name`
/// declared in `IDENTITY.md`. This supports local OpenClaw-based profiles whose
/// filesystem directory names differ from the BCS bot UUID (which may be a
/// human-readable Chinese name).
fn resolve_bot_dir(bots_base_dir: &Path, bot_id: &str) -> ServiceResult<PathBuf> {
    if !bots_base_dir.exists() {
        return Err(ServiceError::BotNotFound(bot_id.to_string()));
    }

    let direct = bots_base_dir.join(bot_id);
    if direct.exists() {
        return Ok(direct);
    }

    let entries = match std::fs::read_dir(bots_base_dir) {
        Ok(entries) => entries,
        Err(e) => {
            debug!(path = %bots_base_dir.display(), error = %e, "Failed to read bots_base_dir");
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        }
    };

    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }

        let identity_path = path.join("IDENTITY.md");
        let identity = match std::fs::read_to_string(&identity_path) {
            Ok(content) => content,
            Err(_) => continue,
        };

        if bot_identity_matches(&identity, bot_id) {
            return Ok(path);
        }
    }

    Err(ServiceError::BotNotFound(bot_id.to_string()))
}

/// Check whether the `name` or `display_name` declared in IDENTITY.md matches
/// the given bot id.
fn bot_identity_matches(identity: &str, bot_id: &str) -> bool {
    for line in identity.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((key, value)) = line.split_once(':') {
            let key = key.trim();
            let value = unquote_yaml_scalar(value.trim());
            if key == "name" || key == "display_name" {
                if value == bot_id {
                    return true;
                }
            }
        }
    }
    false
}

/// Read a file, returning None if it doesn't exist.
fn read_file_opt(path: &Path) -> Option<String> {
    match std::fs::read_to_string(path) {
        Ok(content) => {
            let trimmed = content.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed.to_string())
            }
        }
        Err(e) => {
            if e.kind() != std::io::ErrorKind::NotFound {
                debug!(path = %path.display(), error = %e, "Failed to read file");
            }
            None
        }
    }
}

/// Parse YAML frontmatter from IDENTITY.md to extract name and emoji.
fn parse_identity_frontmatter(content: &str) -> (Option<String>, Option<String>) {
    let frontmatter = extract_yaml_frontmatter(content);
    if frontmatter.is_empty() {
        return (None, None);
    }

    extract_identity_fields(&frontmatter)
}

/// Extract `name` and `emoji` fields from a key-value block.
fn extract_identity_fields(frontmatter: &str) -> (Option<String>, Option<String>) {
    let mut name = None;
    let mut emoji = None;

    for line in frontmatter.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }

        if let Some((key, value)) = line.split_once(':') {
            let key = key.trim();
            let value = unquote_yaml_scalar(value.trim());

            match key {
                "name" => name = Some(value.to_string()),
                "emoji" => emoji = Some(value.to_string()),
                _ => {}
            }
        }
    }

    (name, emoji)
}

/// Extract YAML frontmatter from content.
///
/// Supports the standard `---\n...\n---` delimited form and also a simple
/// top-level key-value header without explicit delimiters (used by many local
/// OpenClaw profiles).
fn extract_yaml_frontmatter(content: &str) -> String {
    let trimmed = content.trim_start();

    // Standard delimited frontmatter: ---\n...\n---
    if let Some(after_open) = trimmed.strip_prefix("---") {
        let mut chars = after_open.chars();
        if chars.next() == Some('\n') {
            let after_newline = chars.as_str();
            if let Some(end) = after_newline.find("\n---") {
                return after_newline[..end].to_string();
            }
        }
    }

    // Fallback: parse the first contiguous block of key-value lines as
    // frontmatter. Leading blank/comment lines are skipped; the block stops at
    // the first subsequent blank line, comment, or non-key-value line.
    let mut lines = Vec::new();
    let mut in_block = false;
    for line in trimmed.lines() {
        let trimmed_line = line.trim();
        if trimmed_line.is_empty() || trimmed_line.starts_with('#') {
            if in_block {
                break;
            }
            continue;
        }
        if trimmed_line.split_once(':').is_none() {
            break;
        }
        in_block = true;
        lines.push(trimmed_line);
    }

    lines.join("\n")
}

/// Unquote a YAML scalar value.
fn unquote_yaml_scalar(value: &str) -> &str {
    if value.len() >= 2
        && ((value.starts_with('"') && value.ends_with('"'))
            || (value.starts_with('\'') && value.ends_with('\'')))
    {
        &value[1..value.len() - 1]
    } else {
        value
    }
}

/// Build the fusion prompt for the LLM.
fn build_fusion_prompt(request: &ContextFusionRequest, contexts: &[ContextBotSummary]) -> String {
    let mut prompt = String::new();

    prompt.push_str("# Context Fusion Task\n\n");
    prompt.push_str(&format!("**Question/Task:** {}\n\n", request.question));

    if let Some(ref focus) = request.focus {
        prompt.push_str(&format!("**Focus Area:** {}\n\n", focus));
    }

    prompt.push_str("## Participant Contexts\n\n");

    for ctx in contexts {
        prompt.push_str(&format!(
            "### {} {} (`{}`)\n\n",
            ctx.display_emoji(),
            ctx.display_name(),
            ctx.bot_uuid
        ));

        if let Some(ref soul) = ctx.soul {
            prompt.push_str("**Soul (Core Being):**\n```\n");
            prompt.push_str(soul);
            prompt.push_str("\n```\n\n");
        }

        if let Some(ref rules) = ctx.rules {
            prompt.push_str("**Rules (Constraints):**\n```\n");
            prompt.push_str(rules);
            prompt.push_str("\n```\n\n");
        }

        if let Some(ref memory) = ctx.memory {
            prompt.push_str("**Memory (Context):**\n```\n");
            push_truncated(&mut prompt, memory, 1000);
            prompt.push_str("\n```\n\n");
        }

        prompt.push_str("---\n\n");
    }

    prompt.push_str("## Instructions\n\n");
    prompt.push_str("Analyze the above participant contexts and produce a JSON response with perspectives, conflicts, alignment points, recommendation, and key insights.\n");

    prompt
}

/// Push text truncated to max lines.
fn push_truncated(prompt: &mut String, text: &str, max_lines: usize) {
    for (i, line) in text.lines().enumerate() {
        if i >= max_lines {
            prompt.push_str("... (truncated)");
            break;
        }
        prompt.push_str(line);
        prompt.push('\n');
    }
}

/// Parse the fusion response from LLM output.
fn parse_fusion_response(text: &str) -> ServiceResult<ContextFusionResponse> {
    // Try to extract JSON from the response
    let json_text = extract_json(text);

    match serde_json::from_str::<ContextFusionResponse>(&json_text) {
        Ok(response) => Ok(response),
        Err(e) => {
            debug!(error = %e, text = %text, "Failed to parse fusion response as JSON");
            // Return a basic response with the raw text
            Ok(ContextFusionResponse {
                perspectives: vec![],
                conflicts: vec![],
                alignment_points: vec![],
                recommendation: Some(text.to_string()),
                key_insights: vec![],
                extra: None,
            })
        }
    }
}

/// Extract JSON from text that might have markdown code fences.
fn extract_json(text: &str) -> String {
    // Try to extract from code fence
    if let Some(start) = text.find("```json") {
        let rest = &text[start + 7..];
        if let Some(end) = rest.find("```") {
            return rest[..end].trim().to_string();
        }
    }

    // Try to find JSON object directly
    if let Some(start) = text.find('{') {
        let mut depth = 0;
        for (i, c) in text[start..].char_indices() {
            match c {
                '{' => depth += 1,
                '}' => {
                    depth -= 1;
                    if depth == 0 {
                        return text[start..start + i + 1].to_string();
                    }
                }
                _ => {}
            }
        }
    }

    text.to_string()
}

#[async_trait]
impl FusionCoreService for LocalFusionService {
    async fn fuse(&self, request: &ContextFusionRequest) -> ServiceResult<ContextFusionResponse> {
        info!(
            question = %request.question,
            participants = ?request.participants,
            "Fusing contexts"
        );

        // Load all bot contexts
        let contexts = self.load_bot_contexts(&request.participants);

        if contexts.is_empty() {
            return Ok(ContextFusionResponse::default());
        }

        // If we have an LLM client, use it for intelligent fusion
        if let Some(ref client) = self.llm_client {
            self.fuse_with_llm(client.as_ref(), request, &contexts)
                .await
        } else {
            // Fall back to simple merge
            self.simple_fusion(request, &contexts)
        }
    }

    fn load_bot_context(&self, bot_id: &str) -> ServiceResult<ContextBotSummary> {
        load_bot_context(&self.bots_base_dir, bot_id)
    }

    fn load_bot_contexts(&self, bot_ids: &[String]) -> Vec<ContextBotSummary> {
        bot_ids
            .iter()
            .filter_map(|id| match load_bot_context(&self.bots_base_dir, id) {
                Ok(ctx) => Some(ctx),
                Err(e) => {
                    warn!(bot_id = %id, error = %e, "Failed to load bot context");
                    None
                }
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::{
        ContextBotSummary as BotContextSummary, ContextConflict as Conflict,
        ContextConflictPosition as ConflictPosition, ContextFusionRequest as FusionRequest,
        ContextFusionResponse as FusionResponse,
        ContextParticipantPerspective as ParticipantPerspective,
    };

    #[test]
    fn test_build_fusion_prompt() {
        let request = FusionRequest {
            question: "Resolve the conflict".to_string(),
            participants: vec!["bot1".to_string()],
            focus: None,
            session_id: None,
            ..Default::default()
        };

        let contexts = vec![BotContextSummary {
            bot_uuid: "bot1".to_string(),
            name: Some("Bot One".to_string()),
            emoji: Some("🤖".to_string()),
            identity: None,
            soul: Some("I am helpful.".to_string()),
            rules: Some("- Be nice".to_string()),
            memory: None,
            tools: None,
            agents: None,
        }];

        let prompt = build_fusion_prompt(&request, &contexts);
        assert!(prompt.contains("Bot One"));
        assert!(prompt.contains("I am helpful"));
        assert!(prompt.contains("Be nice"));
    }

    #[test]
    fn test_extract_json() {
        let text = r#"Some text before
```json
{"key": "value"}
```
Some text after"#;
        let json = extract_json(text);
        assert_eq!(json, r#"{"key": "value"}"#);
    }

    #[test]
    fn test_extract_json_direct() {
        let text = r#"Here is the response: {"a": 1, "b": 2} done."#;
        let json = extract_json(text);
        assert_eq!(json, r#"{"a": 1, "b": 2}"#);
    }

    #[test]
    fn test_extract_yaml_frontmatter() {
        let content = r#"---
name: "Test Bot"
emoji: "🤖"
---
Some content"#;
        let frontmatter = extract_yaml_frontmatter(content);
        assert!(frontmatter.contains("name: \"Test Bot\""));
        assert!(frontmatter.contains("emoji: \"🤖\""));
    }

    #[test]
    fn test_extract_yaml_frontmatter_no_frontmatter() {
        let content = "Just regular content\nNo frontmatter here";
        let frontmatter = extract_yaml_frontmatter(content);
        assert!(frontmatter.is_empty());
    }

    #[test]
    fn test_extract_yaml_frontmatter_incomplete() {
        let content = "---\nname: Test\nNo closing dashes";
        let frontmatter = extract_yaml_frontmatter(content);
        assert!(frontmatter.is_empty());
    }

    #[test]
    fn test_parse_identity_frontmatter_extracts_name_and_emoji() {
        let content = r#"---
name: "张三"
emoji: "🧑‍💻"
---
# Identity content"#;
        let (name, emoji) = parse_identity_frontmatter(content);
        assert_eq!(name, Some("张三".to_string()));
        assert_eq!(emoji, Some("🧑‍💻".to_string()));
    }

    #[test]
    fn test_parse_identity_frontmatter_empty() {
        let content = "No frontmatter here";
        let (name, emoji) = parse_identity_frontmatter(content);
        assert!(name.is_none());
        assert!(emoji.is_none());
    }

    #[test]
    fn test_parse_identity_frontmatter_partial() {
        let content = "---\nname: OnlyName\n---";
        let (name, emoji) = parse_identity_frontmatter(content);
        assert_eq!(name, Some("OnlyName".to_string()));
        assert!(emoji.is_none());
    }

    #[test]
    fn test_parse_identity_frontmatter_without_delimiters() {
        let content = "# IDENTITY.md\n\nname: 店长日常运营\ndisplay_name: 店长日常运营\nemoji: 🏪\nrole: manager\n";
        let (name, emoji) = parse_identity_frontmatter(content);
        assert_eq!(name, Some("店长日常运营".to_string()));
        assert_eq!(emoji, Some("🏪".to_string()));
    }

    #[test]
    fn test_resolve_bot_dir_prefers_direct_match() {
        let tmp = std::env::temp_dir().join(format!("bcs-bot-dir-direct-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).unwrap();
        let bot_dir = tmp.join("bot-uuid-1");
        std::fs::create_dir(&bot_dir).unwrap();

        let resolved = resolve_bot_dir(&tmp, "bot-uuid-1").unwrap();
        assert_eq!(resolved, bot_dir);

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_resolve_bot_dir_falls_back_to_identity_name() {
        let tmp = std::env::temp_dir().join(format!("bcs-bot-dir-fb-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).unwrap();

        let profile_dir = tmp.join("merchant-operations-manager");
        std::fs::create_dir(&profile_dir).unwrap();
        std::fs::write(
            profile_dir.join("IDENTITY.md"),
            "name: 店长日常运营\ndisplay_name: 店长日常运营\nrole: manager\n",
        )
        .unwrap();

        let resolved = resolve_bot_dir(&tmp, "店长日常运营").unwrap();
        assert_eq!(resolved, profile_dir);

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_load_bot_context_reads_tools_and_agents() {
        let tmp = std::env::temp_dir().join(format!("bcs-bot-ctx-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).unwrap();

        let bot_dir = tmp.join("some-bot");
        std::fs::create_dir(&bot_dir).unwrap();
        std::fs::write(bot_dir.join("SOUL.md"), "soul content").unwrap();
        std::fs::write(bot_dir.join("TOOLS.md"), "tools content").unwrap();
        std::fs::write(bot_dir.join("AGENTS.md"), "agents content").unwrap();

        let ctx = load_bot_context(&tmp, "some-bot").unwrap();
        assert_eq!(ctx.soul.as_deref(), Some("soul content"));
        assert_eq!(ctx.tools.as_deref(), Some("tools content"));
        assert_eq!(ctx.agents.as_deref(), Some("agents content"));

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_unquote_yaml_scalar_double_quotes() {
        assert_eq!(unquote_yaml_scalar("\"quoted value\""), "quoted value");
        assert_eq!(unquote_yaml_scalar("\"张三\""), "张三");
    }

    #[test]
    fn test_unquote_yaml_scalar_single_quotes() {
        assert_eq!(unquote_yaml_scalar("'quoted value'"), "quoted value");
    }

    #[test]
    fn test_unquote_yaml_scalar_unquoted() {
        assert_eq!(unquote_yaml_scalar("unquoted"), "unquoted");
        assert_eq!(unquote_yaml_scalar("123"), "123");
    }

    #[test]
    fn test_push_truncated_limits_lines() {
        let mut prompt = String::new();
        let text = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5";
        push_truncated(&mut prompt, text, 3);
        assert!(prompt.contains("Line 1"));
        assert!(prompt.contains("Line 2"));
        assert!(prompt.contains("Line 3"));
        assert!(!prompt.contains("Line 4"));
        assert!(prompt.contains("truncated"));
    }

    #[test]
    fn test_push_truncated_short_text() {
        let mut prompt = String::new();
        let text = "Single line";
        push_truncated(&mut prompt, text, 100);
        assert_eq!(prompt.trim(), "Single line");
        assert!(!prompt.contains("truncated"));
    }

    #[test]
    fn test_parse_fusion_response_valid_json() {
        let json = r#"{
            "perspectives": [{"bot_uuid": "bot1", "name": "Bot1", "emoji": "🤖", "summary": "Test summary"}],
            "conflicts": [],
            "alignment_points": ["Point 1"],
            "recommendation": "Do this",
            "key_insights": []
        }"#;
        let result = parse_fusion_response(json).unwrap();
        assert_eq!(result.perspectives.len(), 1);
        assert_eq!(result.recommendation, Some("Do this".to_string()));
    }

    #[test]
    fn test_parse_fusion_response_invalid_returns_basic() {
        let text = "This is not JSON at all";
        let result = parse_fusion_response(text).unwrap();
        assert!(result.perspectives.is_empty());
        assert_eq!(
            result.recommendation,
            Some("This is not JSON at all".to_string())
        );
    }

    #[test]
    fn test_simple_fusion_generates_perspectives() {
        let engine = LocalFusionService::new("/tmp");

        let request = FusionRequest {
            question: "Test question".to_string(),
            participants: vec!["bot1".to_string()],
            focus: None,
            session_id: None,
            ..Default::default()
        };

        let contexts = vec![BotContextSummary {
            bot_uuid: "bot1".to_string(),
            name: Some("Bot One".to_string()),
            emoji: Some("🤖".to_string()),
            identity: None,
            soul: Some("I am helpful".to_string()),
            rules: Some("- Be nice\n- Be honest".to_string()),
            memory: Some("Remembered something".to_string()),
            tools: None,
            agents: None,
        }];

        let result = engine.simple_fusion(&request, &contexts).unwrap();
        assert_eq!(result.perspectives.len(), 1);
        assert_eq!(result.perspectives[0].bot_uuid, "bot1");
        assert!(result.perspectives[0].summary.contains("Bot One"));
        assert!(
            result.perspectives[0]
                .concerns
                .contains(&"- Be nice".to_string())
        );
    }

    #[test]
    fn test_build_fusion_prompt_with_focus() {
        let request = FusionRequest {
            question: "Test question".to_string(),
            participants: vec![],
            focus: Some("Performance".to_string()),
            session_id: None,
            ..Default::default()
        };

        let prompt = build_fusion_prompt(&request, &[]);
        assert!(prompt.contains("**Focus Area:** Performance"));
    }

    // ========================================================================
    // Additional tests for BCS.md features
    // ========================================================================

    /// Mock LLM client for testing
    struct MockLlmClient {
        response: String,
    }

    #[async_trait::async_trait]
    impl LlmClient for MockLlmClient {
        async fn complete(&self, _prompt: &str) -> ServiceResult<String> {
            Ok(self.response.clone())
        }
    }

    #[tokio::test]
    async fn test_fuse_with_mock_llm() {
        let mock_client = MockLlmClient {
            response: r#"{
                "perspectives": [
                    {
                        "bot_id": "bot1",
                        "name": "Bot One",
                        "emoji": "🤖",
                        "summary": "From development perspective",
                        "key_points": ["Implementation cost"],
                        "concerns": ["Timeline"]
                    }
                ],
                "conflicts": [],
                "alignment_points": ["All agree on security"],
                "recommendation": "Proceed with option A",
                "key_insights": ["Security is a priority"]
            }"#
            .to_string(),
        };

        let engine = LocalFusionService::new("/tmp").with_llm_client(Box::new(mock_client));

        let request = FusionRequest {
            question: "Should we proceed?".to_string(),
            participants: vec!["bot1".to_string()],
            focus: None,
            session_id: None,
            ..Default::default()
        };

        // Note: This will fail to load bot context since /tmp/bot1 doesn't exist,
        // but we can test the prompt building and response parsing
        let contexts = vec![BotContextSummary {
            bot_uuid: "bot1".to_string(),
            name: Some("Bot One".to_string()),
            emoji: Some("🤖".to_string()),
            identity: None,
            soul: Some("I am a developer".to_string()),
            rules: None,
            memory: None,
            tools: None,
            agents: None,
        }];

        let result = engine.fuse_with_llm(&MockLlmClient { response: r#"{"perspectives":[],"conflicts":[],"alignment_points":[],"recommendation":"test","key_insights":[]}"#.to_string() }, &request, &contexts).await;
        assert!(result.is_ok());
    }

    #[test]
    fn test_fusion_response_g2_conflict_scenario() {
        // Test parsing a G2 (conflict alignment) fusion response
        let json = r#"{
            "perspectives": [
                {
                    "bot_uuid": "zhangsan",
                    "name": "张三",
                    "emoji": "🧑‍💻",
                    "summary": "开发者视角：当前代码实现为60分钟超时",
                    "key_points": ["实现成本", "兼容性"],
                    "concerns": ["时间紧迫"]
                },
                {
                    "bot_uuid": "lisi",
                    "name": "李四",
                    "emoji": "📋",
                    "summary": "PM视角：PRD要求30分钟超时",
                    "key_points": ["用户体验", "需求一致性"],
                    "concerns": ["安全风险"]
                },
                {
                    "bot_uuid": "security",
                    "name": "安全",
                    "emoji": "🔒",
                    "summary": "安全视角：建议增加安全校验",
                    "key_points": ["数据安全", "合规性"],
                    "concerns": ["性能影响"]
                }
            ],
            "conflicts": [
                {
                    "parties": ["zhangsan", "lisi"],
                    "issue": "超时时间不一致",
                    "positions": [
                        {"bot_uuid": "zhangsan", "view": "60分钟"},
                        {"bot_uuid": "lisi", "view": "30分钟"}
                    ]
                }
            ],
            "alignment_points": ["都认同需要安全校验"],
            "recommendation": "建议折中为45分钟，并补充安全校验",
            "key_insights": ["安全Bot可提供会话安全建议"]
        }"#;

        let response: FusionResponse = serde_json::from_str(json).unwrap();
        assert_eq!(response.perspectives.len(), 3);
        assert_eq!(response.conflicts.len(), 1);
        assert_eq!(response.conflicts[0].parties, vec!["zhangsan", "lisi"]);
        assert_eq!(response.alignment_points.len(), 1);
        assert!(response.recommendation.is_some());
    }

    #[test]
    fn test_bot_context_summary_display_name_fallback() {
        let ctx = BotContextSummary {
            bot_uuid: "test-bot".to_string(),
            name: None,
            emoji: None,
            identity: None,
            soul: None,
            rules: None,
            memory: None,
            tools: None,
            agents: None,
        };

        assert_eq!(ctx.display_name(), "test-bot");
        assert_eq!(ctx.display_emoji(), "🤖");
    }

    #[test]
    fn test_bot_context_summary_with_values() {
        let ctx = BotContextSummary {
            bot_uuid: "dba".to_string(),
            name: Some("DBA专家".to_string()),
            emoji: Some("🗄️".to_string()),
            identity: Some("# DBA Expert\nHandles database issues.".to_string()),
            soul: Some("I am a database expert.".to_string()),
            rules: Some("- Always verify before executing DDL".to_string()),
            memory: Some("Last incident: deadlock in orders table".to_string()),
            tools: None,
            agents: None,
        };

        assert_eq!(ctx.display_name(), "DBA专家");
        assert_eq!(ctx.display_emoji(), "🗄️");
    }

    #[test]
    fn test_fusion_request_with_session_id() {
        let request = FusionRequest {
            question: "How to coordinate?".to_string(),
            participants: vec!["bot1".to_string(), "bot2".to_string()],
            focus: Some("Security risks".to_string()),
            session_id: Some("grp-001".to_string()),
            ..Default::default()
        };

        assert_eq!(request.session_id, Some("grp-001".to_string()));
        assert_eq!(request.focus, Some("Security risks".to_string()));
    }

    #[test]
    fn test_parse_fusion_response_with_empty_perspectives() {
        let json = r#"{
            "perspectives": [],
            "conflicts": [],
            "alignment_points": [],
            "recommendation": null,
            "key_insights": []
        }"#;

        let response: FusionResponse = serde_json::from_str(json).unwrap();
        assert!(response.perspectives.is_empty());
        assert!(response.recommendation.is_none());
    }

    #[tokio::test]
    async fn test_fuse_returns_default_for_empty_participants() {
        let engine = LocalFusionService::new("/tmp");

        let request = FusionRequest {
            question: "Test".to_string(),
            participants: vec![],
            focus: None,
            session_id: None,
            ..Default::default()
        };

        let result = engine.fuse(&request).await.unwrap();
        assert!(result.perspectives.is_empty());
    }

    #[tokio::test]
    async fn test_fuse_returns_default_for_nonexistent_bots() {
        let engine = LocalFusionService::new("/nonexistent/path");

        let request = FusionRequest {
            question: "Test".to_string(),
            participants: vec!["nonexistent-bot".to_string()],
            focus: None,
            session_id: None,
            ..Default::default()
        };

        // Should return default (empty) response when bot contexts cannot be loaded
        let result = engine.fuse(&request).await.unwrap();
        assert!(result.perspectives.is_empty());
    }

    #[test]
    fn test_load_merchant_operations_manager_context() {
        // Resolve the profile directory from the crate root so this test works on
        // CI/Linux runners as well as local macOS dev machines.
        let profile_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../../../scripts/4bots_merchant_operations_profile");
        assert!(
            profile_dir.exists(),
            "profile directory should exist: {}",
            profile_dir.display()
        );
        let ctx = load_bot_context(&profile_dir, "店长日常运营").expect("load should succeed");

        assert!(ctx.identity.is_some(), "identity should be present");
        assert!(ctx.soul.is_some(), "soul should be present");
        assert!(ctx.rules.is_some(), "rules should be present");
        assert!(ctx.memory.is_some(), "memory should be present");
        assert!(ctx.tools.is_some(), "tools should be present");
        assert!(ctx.agents.is_some(), "agents should be present");
        assert_eq!(ctx.display_name(), "店长日常运营");
    }

    #[test]
    fn test_participant_perspective_serde() {
        let perspective = ParticipantPerspective {
            bot_uuid: "security".to_string(),
            name: "安全Bot".to_string(),
            emoji: "🔒".to_string(),
            summary: "From security perspective".to_string(),
            key_points: vec!["Data encryption".to_string(), "Access control".to_string()],
            concerns: vec!["Performance overhead".to_string()],
            role: None,
            confidence: None,
            status: None,
            participant_type: None,
            evidence: None,
        };

        let json = serde_json::to_string(&perspective).unwrap();
        let parsed: ParticipantPerspective = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.bot_uuid, "security");
        assert_eq!(parsed.key_points.len(), 2);
        assert_eq!(parsed.concerns.len(), 1);
    }

    #[test]
    fn test_conflict_serde() {
        let conflict = Conflict {
            parties: vec!["dev".to_string(), "pm".to_string()],
            issue: "Timeout value disagreement".to_string(),
            positions: vec![
                ConflictPosition {
                    bot_uuid: "dev".to_string(),
                    view: "60 minutes".to_string(),
                },
                ConflictPosition {
                    bot_uuid: "pm".to_string(),
                    view: "30 minutes".to_string(),
                },
            ],
            severity: None,
        };

        let json = serde_json::to_string(&conflict).unwrap();
        let parsed: Conflict = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.parties.len(), 2);
        assert_eq!(parsed.positions.len(), 2);
    }

    #[test]
    fn test_extract_json_nested_object() {
        let text = r#"Response: {"outer": {"inner": "value"}, "count": 1} end"#;
        let json = extract_json(text);
        assert!(json.contains("\"outer\""));
        assert!(json.contains("\"inner\""));
    }
}
