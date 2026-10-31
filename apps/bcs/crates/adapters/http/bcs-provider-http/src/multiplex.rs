use super::*;

pub struct BotTransportMux {
    a2a: Option<Arc<dyn BotDeliveryPort>>,
    websocket: Arc<dyn BotDeliveryPort>,
    provider: Arc<HttpProviderTransport>,
}

impl BotTransportMux {
    pub fn new(websocket: Arc<dyn BotDeliveryPort>, provider: Arc<HttpProviderTransport>) -> Self {
        Self {
            websocket,
            provider,
            a2a: None,
        }
    }

    pub fn with_a2a(mut self, a2a: Arc<dyn BotDeliveryPort>) -> Self {
        self.a2a = Some(a2a);
        self
    }

    fn a2a(&self) -> ServiceResult<&Arc<dyn BotDeliveryPort>> {
        self.a2a.as_ref().ok_or_else(|| ServiceError::InvalidOperation {
            message: "A2A transport is not configured".into(), request_id: None })
    }
}

#[async_trait]
impl BotDeliveryPort for BotTransportMux {
    async fn connection_identity(&self, target: &BotDeliveryTarget) -> Option<String> {
        match target {
            BotDeliveryTarget::WebSocket { .. } => self.websocket.connection_identity(target).await,
            BotDeliveryTarget::HttpProvider { .. } => self.provider.connection_identity(target).await,
            BotDeliveryTarget::A2a { .. } => None,
        }
    }

    async fn deliver_on_connection(
        &self,
        cmd: BotDeliveryCommand,
        connection_id: &str,
    ) -> ServiceResult<BotDeliveryResult> {
        match &cmd.target {
            BotDeliveryTarget::WebSocket { .. } => self.websocket.deliver_on_connection(cmd, connection_id).await,
            BotDeliveryTarget::HttpProvider { .. } => self.provider.deliver_on_connection(cmd, connection_id).await,
            BotDeliveryTarget::A2a { .. } => self.a2a()?.deliver_on_connection(cmd, connection_id).await,
        }
    }

    async fn abort_on_connection(
        &self,
        cmd: BotAbortDeliveryCommand,
        connection_id: &str,
    ) -> ServiceResult<BotAbortDeliveryResult> {
        match &cmd.target {
            BotDeliveryTarget::WebSocket { .. } => self.websocket.abort_on_connection(cmd, connection_id).await,
            BotDeliveryTarget::HttpProvider { .. } => self.provider.abort_on_connection(cmd, connection_id).await,
            BotDeliveryTarget::A2a { .. } => self.a2a()?.abort_on_connection(cmd, connection_id).await,
        }
    }

    async fn is_available(&self, target: &BotDeliveryTarget) -> bool {
        match target {
            BotDeliveryTarget::WebSocket { .. } => self.websocket.is_available(target).await,
            BotDeliveryTarget::HttpProvider { .. } => self.provider.is_available(target).await,
            BotDeliveryTarget::A2a { .. } => match &self.a2a { Some(port) => port.is_available(target).await, None => false },
        }
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        match &cmd.target {
            BotDeliveryTarget::WebSocket { .. } => self.websocket.deliver(cmd).await,
            BotDeliveryTarget::HttpProvider { .. } => self.provider.deliver(cmd).await,
            BotDeliveryTarget::A2a { .. } => self.a2a()?.deliver(cmd).await,
        }
    }

    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        match &cmd.target {
            BotDeliveryTarget::WebSocket { .. } => self.websocket.abort(cmd).await,
            BotDeliveryTarget::HttpProvider { .. } => self.provider.abort(cmd).await,
            BotDeliveryTarget::A2a { .. } => self.a2a()?.abort(cmd).await,
        }
    }
}

pub struct HistoryRequestMux {
    websocket: Arc<dyn GroupHistoryBotRequestPort>,
    provider: Arc<HttpProviderTransport>,
}

impl HistoryRequestMux {
    pub fn new(
        websocket: Arc<dyn GroupHistoryBotRequestPort>,
        provider: Arc<HttpProviderTransport>,
    ) -> Self {
        Self {
            websocket,
            provider,
        }
    }
}

#[async_trait]
impl GroupHistoryBotRequestPort for HistoryRequestMux {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        method: &str,
        params: Value,
        timeout_ms: u64,
    ) -> Result<Value, String> {
        if matches!(&target, BotDeliveryTarget::A2a { .. }) {
            return Err("chat.history is deprecated and unsupported for A2A".into());
        }
        if target.is_http_provider() {
            self.provider
                .send_history_request(target, method, params, timeout_ms)
                .await
        } else {
            self.websocket
                .send_history_request(target, method, params, timeout_ms)
                .await
        }
    }
}

pub struct InteractionProviderMux {
    websocket: Arc<dyn InteractionProviderPort>,
    provider: Arc<HttpProviderTransport>,
    a2a:Option<Arc<dyn InteractionProviderPort>>,
}

impl InteractionProviderMux {
    pub fn new(
        websocket: Arc<dyn InteractionProviderPort>,
        provider: Arc<HttpProviderTransport>,
    ) -> Self {
        Self {
            websocket,
            provider,
            a2a:None,
        }
    }

    pub fn with_a2a(mut self,a2a:Arc<dyn InteractionProviderPort>)->Self {self.a2a=Some(a2a);self}
}

#[async_trait]
impl InteractionProviderPort for InteractionProviderMux {
    async fn resolve_interaction(
        &self,
        command: InteractionProviderCommand,
    ) -> ServiceResult<InteractionProviderAck> {
        match &command.target {
            BotDeliveryTarget::A2a { .. } => self.a2a.as_ref().ok_or_else(||ServiceError::InvalidOperation {
                message: "A2A Structured Input is not configured".into(), request_id: None })?.resolve_interaction(command).await,
            BotDeliveryTarget::WebSocket { .. } => self.websocket.resolve_interaction(command).await,
            BotDeliveryTarget::HttpProvider { .. } => {
                self.provider.resolve_interaction(command).await
            }
        }
    }
}
