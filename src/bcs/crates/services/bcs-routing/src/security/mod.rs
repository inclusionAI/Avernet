//! AI安全网关集成模块
//!
//! 提供Bot间消息的安全检查和拦截功能。
//!
//! # 主要组件
//!
//! - [`SecurityInterceptor`] — 消息路由拦截器，在消息投递前进行安全检查
//!
//! 真正的网关后端通过 [`bcs_security_gateway_api::SecurityGatewayPort`] 注入，
//! 本模块只负责通用编排（agent_code 校验、dry-run 策略、verdict 映射），
//! 不关心 HTTP / 协议 / 网关地址。
//!
//! # 使用流程
//!
//! 1. 消息路由时调用 `SecurityInterceptor::intercept()`
//! 2. 拦截器检查发送方和接收方的 agent_code
//! 3. 通过注入的 `SecurityGatewayPort` 进行安全检查
//! 4. 根据dry-run配置决定是否拦截消息

pub mod interceptor;

pub use interceptor::{SecurityCheckResult, SecurityInterceptor};
