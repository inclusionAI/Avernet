//! AgentPass 注册模块
//!
//! 在 Office 网络环境下调用 AgentPass 注册端点，
//! 在获取 OAuth headers 之前执行，实现自动注册和鉴权流程。
//!
//! 流程：
//! 1. 检查 BOT_DATA_DIR 环境变量
//! 2. 加载 SessionInfo 获取 token 和 bot_uuid
//! 3. 从 bot.json 读取 summary
//! 4. 发送 POST 请求到公开部署配置的 AgentPass 注册端点
//! 5. 如果首次注册：提取 iframe_url, 构建回调 URL, 打开浏览器
//! 6. 如果已注册：输出信息并继续
//! 7. 其他情况：输出错误并退出

use anyhow::{Context, Result, anyhow};
use serde::{Deserialize, Serialize};
// use std::collections::HashMap;
use tracing::{info, warn};

/// AgentPass 注册响应
#[derive(Debug, Clone, Deserialize)]
pub struct RegisterResponse {
    pub success: bool,
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub confirm_urls: Option<ConfirmUrls>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// 确认 URL 集合
#[derive(Debug, Clone, Deserialize)]
pub struct ConfirmUrls {
    #[allow(dead_code)]
    pub redirect_url: String,
    pub iframe_url: String,
}

/// 注册结果
#[derive(Debug, Clone)]
pub enum RegistrationStatus {
    /// 首次注册成功
    FirstTime {
        agent_code: String,
        iframe_url: String,
    },
    /// 已经注册
    AlreadyRegistered {
        agent_code: String,
    },
    /// 注册失败
    Failed {
        error_code: String,
        message: String,
    },
}

/// 注册请求体
#[derive(Debug, Clone, Serialize)]
struct RegisterRequest {
    agent_name: String,
}

/// AgentPass 注册端点 URL
const AGENTPASS_REGISTER_URL: &str = "https://bcs.example.com/dummy_agentpass";

/// 回调 URL 基础地址
const REGISTRATION_CALLBACK_BASE: &str = "https://botchat.example.com/bcn/register";

/// 尝试注册 Agent
///
/// # 参数
/// - `agent_name`: 代理名称，使用 bot_uuid
///
/// # 返回
/// - `Ok(RegistrationStatus)`: 注册状态
/// - `Err`: 请求或解析错误
pub async fn register_agent(agent_name: &str) -> Result<RegistrationStatus> {
    info!("正在发送 AgentPass 注册请求，agent_name: {}", agent_name);

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .context("创建 HTTP 客户端失败")?;

    let request_body = RegisterRequest {
        agent_name: agent_name.to_string(),
    };

    let response = client
        .post(AGENTPASS_REGISTER_URL)
        .header("Content-Type", "application/json")
        .json(&request_body)
        .send()
        .await
        .context("发送注册请求失败")?;

    let status = response.status();
    let body_text = response
        .text()
        .await
        .context("读取响应体失败")?;

    info!("注册响应状态码: {}, 响应体: {}", status, body_text);

    // 尝试解析响应
    let register_response: RegisterResponse = match serde_json::from_str(&body_text) {
        Ok(resp) => resp,
        Err(e) => {
            // 解析失败，可能是错误响应
            return if status.is_success() {
                Err(anyhow!("解析注册响应失败: {}, 原始响应: {}", e, body_text))
            } else {
                Err(anyhow!("注册请求失败 (HTTP {}): {}", status, body_text))
            };
        }
    };

    // 根据状态码和响应内容判断结果
    match register_response.status.as_str() {
        "registered" if register_response.success => {
            let confirm_urls = register_response
                .confirm_urls
                .context("首次注册响应缺少 confirm_urls")?;

            let agent_code = register_response
                .agent_code
                .context("首次注册响应缺少 agent_code")?;

            info!("代理首次注册成功，agent_code: {}", agent_code);

            Ok(RegistrationStatus::FirstTime {
                agent_code,
                iframe_url: confirm_urls.iframe_url,
            })
        }
        "already_registered" if register_response.success => {
            let agent_code = register_response
                .agent_code
                .context("已注册响应缺少 agent_code")?;

            info!("代理已注册，agent_code: {}", agent_code);

            Ok(RegistrationStatus::AlreadyRegistered { agent_code })
        }
        _ => {
            // 错误情况
            let error_code = register_response
                .error
                .unwrap_or_else(|| "unknown_error".to_string());
            let message = register_response
                .message
                .unwrap_or_else(|| "未知的注册错误".to_string());

            warn!("注册失败: {} - {}", error_code, message);

            Ok(RegistrationStatus::Failed {
                error_code,
                message,
            })
        }
    }
}

/// 构建注册回调 URL
///
/// URL 格式: https://botchat.example.com/bcn/register?token={token}&name={name}&summary={summary}&auth_iframe={iframe_url}
pub fn build_registration_url(
    token: &str,
    name: &str,
    summary: &str,
    iframe_url: &str,
) -> String {
    let mut url = format!(
        "{}?token={}&name={}",
        REGISTRATION_CALLBACK_BASE,
        urlencoding::encode(token),
        urlencoding::encode(name)
    );

    if !summary.is_empty() {
        url.push_str(&format!("&summary={}", urlencoding::encode(summary)));
    }

    url.push_str(&format!("&auth_iframe={}", urlencoding::encode(iframe_url)));

    url
}

/// 尝试在浏览器中打开 URL
///
/// # 返回
/// - `Ok(true)`: 浏览器成功打开
/// - `Ok(false)`: 浏览器打开失败
/// - `Err`: 命令执行错误
pub fn try_open_browser(url: &str) -> Result<bool> {
    let (cmd, args): (&str, Vec<&str>) = if cfg!(target_os = "macos") {
        ("open", vec![url])
    } else if cfg!(target_os = "windows") {
        ("cmd", vec!["/C", "start", url])
    } else {
        // Linux 和其他系统
        ("xdg-open", vec![url])
    };

    info!("尝试使用 {} 打开浏览器: {}", cmd, url);

    let result = std::process::Command::new(cmd)
        .args(&args)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();

    match result {
        Ok(_child) => {
            info!("浏览器命令已启动");
            Ok(true)
        }
        Err(e) => {
            warn!("打开浏览器失败: {}", e);
            Ok(false)
        }
    }
}

/// 尝试注册并鉴权的完整流程
///
/// # 参数
/// - `token`: Session token
/// - `bot_uuid`: Bot UUID
/// - `summary`: Bot 描述
/// - `structured_mode`: 是否结构化输出模式
///
/// # 返回
/// - `Ok(())`: 成功（已注册或首次注册完成）
/// - `Err`: 错误（注册失败）
pub async fn try_register_and_auth(
    token: &str,
    bot_uuid: &str,
    summary: &str,
    structured_mode: bool,
) -> Result<()> {
    // 1. 发送注册请求
    let status = register_agent(bot_uuid).await?;

    match status {
        RegistrationStatus::FirstTime {
            agent_code,
            iframe_url,
        } => {
            // 首次注册，需要构建 URL 并打开浏览器
            let registration_url = build_registration_url(token, bot_uuid, summary, &iframe_url);

            info!("首次注册成功，agent_code: {}，准备打开注册页面", agent_code);

            // 尝试打开浏览器
            let opened = try_open_browser(&registration_url)?;

            if structured_mode {
                // 结构化输出
                let result = serde_json::json!({
                    "status": "first_time_registration",
                    "message": "代理首次注册成功，需要完成授权确认",
                    "agent_code": agent_code,
                    "registration_url": registration_url,
                    "browser_opened": opened
                });
                println!("{}", result);
            } else {
                if opened {
                    eprintln!("[AgentPass] 代理首次注册成功，已打开浏览器进行授权确认...");
                } else {
                    eprintln!("[AgentPass] 代理首次注册成功，请手动打开以下 URL 完成授权确认:");
                }
                eprintln!("  {}", registration_url);
                eprintln!();
            }

            // 首次注册后，用户需要在浏览器中完成授权
            // 然后才能继续 OAuth 流程
            // 这里可以选择等待或让 OAuth 流程继续（OAuth 会处理未授权的情况）

            Ok(())
        }
        RegistrationStatus::AlreadyRegistered { agent_code } => {
            // 已经注册，继续执行
            info!("代理已注册，agent_code: {}，继续执行", agent_code);

            if !structured_mode {
                eprintln!("[AgentPass] 代理已注册，继续执行...");
            }

            Ok(())
        }
        RegistrationStatus::Failed {
            error_code,
            message,
        } => {
            // 注册失败，返回错误
            Err(anyhow!(
                "AgentPass 注册失败 [{}]: {}",
                error_code,
                message
            ))
        }
    }
}

/// 从 bot.json 读取 summary
///
/// 首先尝试读取 $BOT_DATA_DIR/bot.json，如果失败则返回空字符串
pub fn load_bot_summary(bot_data_dir: &std::path::Path) -> String {
    let bot_json_path = bot_data_dir.join("bot.json");

    if !bot_json_path.exists() {
        info!("bot.json 不存在，使用空 summary: {:?}", bot_json_path);
        return String::new();
    }

    match std::fs::read_to_string(&bot_json_path) {
        Ok(content) => {
            match serde_json::from_str::<serde_json::Value>(&content) {
                Ok(json) => {
                    let summary = json
                        .get("summary")
                        .and_then(|s| s.as_str())
                        .unwrap_or("")
                        .to_string();
                    info!("从 bot.json 读取 summary: {}", summary);
                    summary
                }
                Err(e) => {
                    warn!("解析 bot.json 失败: {}, 使用空 summary", e);
                    String::new()
                }
            }
        }
        Err(e) => {
            warn!("读取 bot.json 失败: {}, 使用空 summary", e);
            String::new()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_registration_url() {
        let token = "test_token_123";
        let name = "test_bot";
        let summary = "A test bot";
        let iframe_url = "https://identity.example.com/confirm/xxx?mode=iframe";

        let url = build_registration_url(token, name, summary, iframe_url);

        assert!(url.starts_with(REGISTRATION_CALLBACK_BASE));
        assert!(url.contains("token=test_token_123"));
        assert!(url.contains("name=test_bot"));
        // urlencoding crate uses percent-encoding (%20) not plus (+)
        assert!(url.contains("summary=A%20test%20bot"));
        assert!(url.contains("auth_iframe="));
    }

    #[test]
    fn test_build_registration_url_empty_summary() {
        let token = "test_token";
        let name = "test_bot";
        let summary = "";
        let iframe_url = "https://example.com/iframe";

        let url = build_registration_url(token, name, summary, iframe_url);

        assert!(url.contains("token=test_token"));
        assert!(url.contains("name=test_bot"));
        assert!(!url.contains("&summary="));
    }

    #[test]
    fn test_build_registration_url_with_special_chars() {
        let token = "token with spaces & symbols=+";
        let name = "bot-name_123";
        let summary = "这是一个中文描述";
        let iframe_url = "https://example.com/path?param=value&other=test";

        let url = build_registration_url(token, name, summary, iframe_url);

        // 验证 URL 是有效的
        assert!(url.starts_with(REGISTRATION_CALLBACK_BASE));
        // 特殊字符应该被正确编码
        assert!(!url.contains(" "));
    }

    #[test]
    fn test_load_bot_summary() {
        use std::io::Write;
        use tempfile::TempDir;

        let temp_dir = TempDir::new().unwrap();
        let bot_json = temp_dir.path().join("bot.json");

        // 创建测试 bot.json
        let content = r#"{
            "bot_id": "test_bot",
            "name": "Test Bot",
            "summary": "A helpful test bot"
        }"#;

        let mut file = std::fs::File::create(&bot_json).unwrap();
        file.write_all(content.as_bytes()).unwrap();

        let summary = load_bot_summary(temp_dir.path());
        assert_eq!(summary, "A helpful test bot");
    }

    #[test]
    fn test_load_bot_summary_file_not_exists() {
        use tempfile::TempDir;

        let temp_dir = TempDir::new().unwrap();

        let summary = load_bot_summary(temp_dir.path());
        assert!(summary.is_empty());
    }

    #[test]
    fn test_register_response_parsing() {
        let json = r#"{
            "success": true,
            "status": "registered",
            "agent_code": "abc-123",
            "message": "Agent registered successfully",
            "confirm_urls": {
                "redirect_url": "https://example.com/redirect",
                "iframe_url": "https://example.com/iframe"
            }
        }"#;

        let response: RegisterResponse = serde_json::from_str(json).unwrap();
        assert!(response.success);
        assert_eq!(response.status, "registered");
        assert_eq!(response.agent_code, Some("abc-123".to_string()));
        assert!(response.confirm_urls.is_some());

        let urls = response.confirm_urls.unwrap();
        assert_eq!(urls.iframe_url, "https://example.com/iframe");
    }

    #[test]
    fn test_register_response_already_registered() {
        let json = r#"{
            "success": true,
            "status": "already_registered",
            "agent_code": "abc-123",
            "message": "Agent already registered"
        }"#;

        let response: RegisterResponse = serde_json::from_str(json).unwrap();
        assert!(response.success);
        assert_eq!(response.status, "already_registered");
        assert_eq!(response.agent_code, Some("abc-123".to_string()));
        assert!(response.confirm_urls.is_none());
    }
}
