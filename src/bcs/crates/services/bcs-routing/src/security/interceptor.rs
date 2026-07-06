use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::interceptor::{InterceptorDecision, MessageInterceptor, OutboundMessage};
use bcs_security_gateway_api::{SecurityCheckRequest, SecurityGatewayPort, SecurityVerdict};
use tracing::{info, warn};

/// 安全检查结果（拦截器把网关 verdict 翻译成的内部结果）
#[derive(Debug, Clone)]
pub enum SecurityCheckResult {
    /// 检查通过，返回task_id
    Pass { task_id: String },
    /// 检查未通过（需要拦截）
    Block { error_code: String, message: String },
    /// 检查异常/降级（继续路由）
    Degraded { reason: String },
}

impl SecurityCheckResult {
    /// 检查是否通过
    pub fn is_pass(&self) -> bool {
        matches!(self, SecurityCheckResult::Pass { .. })
    }

    /// 检查是否需要拦截（非dry-run模式下）
    pub fn should_block(&self) -> bool {
        matches!(self, SecurityCheckResult::Block { .. })
    }

    /// 获取task_id（如果通过）
    pub fn task_id(&self) -> Option<&str> {
        match self {
            SecurityCheckResult::Pass { task_id } => Some(task_id),
            _ => None,
        }
    }

    /// 获取错误码（如果拦截）
    pub fn error_code(&self) -> Option<&str> {
        match self {
            SecurityCheckResult::Block { error_code, .. } => Some(error_code),
            _ => None,
        }
    }
}

/// 安全拦截器
///
/// 后端无关的纯编排：组装中立 [`SecurityCheckRequest`] → 调注入的
/// [`SecurityGatewayPort`] → 按 `dry_run` 把 verdict 翻译成投递决策。
///
/// 它不认识 agent_code、JWT、网关错误码——这些后端专属的前置校验全部由具体
/// `SecurityGatewayPort` 实现负责（例如 AgentPass client）。
#[derive(Clone)]
pub struct SecurityInterceptor {
    gateway: Arc<dyn SecurityGatewayPort>,
    dry_run: bool,
}

impl SecurityInterceptor {
    /// 创建新的安全拦截器
    ///
    /// # 参数
    /// - `gateway`: 注入的安全网关实现（noop / fake / 真实网关 client）
    /// - `dry_run`: true 时拦截仅日志、不阻断；false 时真正阻断
    pub fn new(gateway: Arc<dyn SecurityGatewayPort>, dry_run: bool) -> Self {
        Self { gateway, dry_run }
    }

    /// 执行消息拦截检查
    ///
    /// 把请求交给注入的网关，并将其中立 verdict 翻译成内部结果（应用 dry-run）。
    pub async fn intercept(&self, request: SecurityCheckRequest) -> SecurityCheckResult {
        let original_id = request.message_id.clone();
        let verdict = self.gateway.check(request).await;
        self.map_verdict(verdict, &original_id)
    }

    /// 把 port 返回的中立 verdict 映射成内部结果（并应用 dry-run 策略）
    fn map_verdict(&self, verdict: SecurityVerdict, original_id: &str) -> SecurityCheckResult {
        match verdict {
            SecurityVerdict::Allow { task_id } => {
                let task_id = task_id.unwrap_or_else(|| original_id.to_string());
                info!(task_id = %task_id, "Security check passed");
                SecurityCheckResult::Pass { task_id }
            }
            SecurityVerdict::Deny { code, message } => self.handle_block(code, message),
            SecurityVerdict::Unavailable { reason } => {
                warn!(reason = %reason, "Security check unavailable, degrading");
                SecurityCheckResult::Degraded { reason }
            }
        }
    }

    /// 处理拦截情况（根据dry-run模式决定实际行为）
    fn handle_block(&self, error_code: String, message: String) -> SecurityCheckResult {
        if self.dry_run {
            warn!(
                error_code = %error_code,
                message = %message,
                dry_run = true,
                "Security check blocked (dry-run, continuing)"
            );
            SecurityCheckResult::Degraded {
                reason: format!(
                    "Security check blocked with code {} (dry-run), message: {}",
                    error_code, message
                ),
            }
        } else {
            warn!(
                error_code = %error_code,
                message = %message,
                dry_run = false,
                "Security check blocked (blocking message)"
            );
            SecurityCheckResult::Block { error_code, message }
        }
    }

    /// 获取dry_run配置
    pub fn is_dry_run(&self) -> bool {
        self.dry_run
    }
}

#[async_trait]
impl MessageInterceptor for SecurityInterceptor {
    async fn on_outbound(&self, msg: &mut OutboundMessage) -> InterceptorDecision {
        let request = SecurityCheckRequest {
            sender_bot_id: msg.message.sender.clone(),
            receiver_bot_id: msg.receiver_bot_id.clone(),
            sender_agent_code: msg.caller.agent_code.clone(),
            receiver_agent_code: msg.receiver.agent_code.clone(),
            agent_token: msg.caller.agent_token.clone(),
            message_content: msg.message.content.clone(),
            message_id: msg.message.id.clone(),
        };

        match self.intercept(request).await {
            SecurityCheckResult::Pass { task_id } => {
                if task_id != msg.message.id {
                    msg.message.id = task_id;
                    InterceptorDecision::Modify
                } else {
                    InterceptorDecision::Pass
                }
            }
            SecurityCheckResult::Degraded { reason } => {
                warn!(reason = %reason, "Security interceptor degraded; continuing outbound delivery");
                InterceptorDecision::Pass
            }
            SecurityCheckResult::Block { error_code, message } => {
                InterceptorDecision::Block(bcs_service_api::interceptor::BlockReason {
                    interceptor_id: "security_gateway".to_string(),
                    code: error_code,
                    message,
                    user_visible: true,
                })
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_security_gateway_local::FakeSecurityGateway;

    fn request() -> SecurityCheckRequest {
        SecurityCheckRequest {
            sender_bot_id: "bot_A".to_string(),
            receiver_bot_id: "bot_B".to_string(),
            sender_agent_code: Some("agent_A".to_string()),
            receiver_agent_code: Some("agent_B".to_string()),
            agent_token: None,
            message_content: "Hello".to_string(),
            message_id: "msg_123".to_string(),
        }
    }

    fn interceptor(gateway: FakeSecurityGateway, dry_run: bool) -> SecurityInterceptor {
        SecurityInterceptor::new(Arc::new(gateway), dry_run)
    }

    #[test]
    fn test_security_check_result_variants() {
        let pass = SecurityCheckResult::Pass { task_id: "task_123".to_string() };
        assert!(pass.is_pass());
        assert!(!pass.should_block());
        assert_eq!(pass.task_id(), Some("task_123"));

        let block = SecurityCheckResult::Block {
            error_code: "ERR_001".to_string(),
            message: "Blocked".to_string(),
        };
        assert!(!block.is_pass());
        assert!(block.should_block());
        assert_eq!(block.error_code(), Some("ERR_001"));

        let degraded = SecurityCheckResult::Degraded { reason: "Timeout".to_string() };
        assert!(!degraded.is_pass());
        assert!(!degraded.should_block());
        assert_eq!(degraded.task_id(), None);
    }

    #[tokio::test]
    async fn test_allow_maps_to_pass() {
        let i = interceptor(
            FakeSecurityGateway::new(SecurityVerdict::Allow {
                task_id: Some("task_from_gateway".to_string()),
            }),
            true,
        );
        match i.intercept(request()).await {
            SecurityCheckResult::Pass { task_id } => assert_eq!(task_id, "task_from_gateway"),
            other => panic!("expected Pass, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_allow_without_task_id_keeps_message_id() {
        let i = interceptor(FakeSecurityGateway::allow(), true);
        match i.intercept(request()).await {
            SecurityCheckResult::Pass { task_id } => assert_eq!(task_id, "msg_123"),
            other => panic!("expected Pass, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_deny_non_dry_run_blocks() {
        let i = interceptor(FakeSecurityGateway::deny("000_200_101", "黑名单拦截"), false);
        match i.intercept(request()).await {
            SecurityCheckResult::Block { error_code, .. } => assert_eq!(error_code, "000_200_101"),
            other => panic!("expected Block, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_deny_dry_run_degrades() {
        let i = interceptor(FakeSecurityGateway::deny("000_200_101", "黑名单拦截"), true);
        match i.intercept(request()).await {
            SecurityCheckResult::Degraded { reason } => assert!(reason.contains("000_200_101")),
            other => panic!("expected Degraded, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_unavailable_degrades() {
        let i = interceptor(
            FakeSecurityGateway::new(SecurityVerdict::Unavailable {
                reason: "timeout".to_string(),
            }),
            false,
        );
        match i.intercept(request()).await {
            SecurityCheckResult::Degraded { reason } => assert!(reason.contains("timeout")),
            other => panic!("expected Degraded, got {other:?}"),
        }
    }

    #[test]
    fn test_is_dry_run() {
        assert!(interceptor(FakeSecurityGateway::allow(), true).is_dry_run());
        assert!(!interceptor(FakeSecurityGateway::allow(), false).is_dry_run());
    }
}
