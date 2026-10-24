use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

pub type SecurityGatewayProviderConfig = BTreeMap<String, serde_json::Value>;

/// AI安全网关配置。
///
/// `provider` 选择安全网关实现并在启动阶段注入对应 `SecurityGatewayPort`。
/// 开源版内置 `"noop"`（永远放行）；其他 provider 由链接进二进制的插件注册。
///
/// `dry_run` 是投递策略：true 仅观测（拦截只打日志），false 真正阻断。
/// provider 私有配置放在 `providers.<provider>` map 中，由具体插件解析。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SecurityGatewayConfig {
    #[serde(default = "default_security_provider")]
    pub provider: String,

    #[serde(default = "default_security_dry_run")]
    pub dry_run: bool,

    #[serde(default)]
    pub providers: BTreeMap<String, SecurityGatewayProviderConfig>,
}

impl Default for SecurityGatewayConfig {
    fn default() -> Self {
        Self {
            provider: default_security_provider(),
            dry_run: default_security_dry_run(),
            providers: BTreeMap::new(),
        }
    }
}

fn default_security_provider() -> String {
    "noop".to_string()
}

fn default_security_dry_run() -> bool {
    true
}
