use super::*;

impl BotCore {
    pub(super) async fn resolve_delivery_target_impl(&self, bot_id: &str, for_abort:bool) -> ServiceResult<BotDeliveryTarget> {
        let bot = self
            .repo
            .get(bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(bot_id.to_string()))?;

        let Some(bindings) = &self.provider_bindings else {
            debug!(bot_id = %bot_id, "resolve_delivery_target: provider_bindings repo not configured, fallback to websocket");
            return Ok(BotDeliveryTarget::WebSocket { bot_id: bot.bot_uuid });
        };
        let Some(binding) = bindings.get_binding_by_bot_uuid(bot_id).await? else {
            debug!(bot_id = %bot_id, "resolve_delivery_target: no provider binding, fallback to websocket");
            return Ok(BotDeliveryTarget::WebSocket { bot_id: bot.bot_uuid });
        };
        if binding.disabled {
            warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, provider_id = %binding.provider_id, "resolve_delivery_target: provider bot binding disabled");
            return Err(ServiceError::InvalidOperation {
                message: format!("provider bot '{}' is disabled", bot_id),
                request_id: None,
            });
        }

        let providers = self.provider_repo.as_ref().ok_or_else(|| {
            ServiceError::InternalError("provider repo not configured".to_string())
        })?;
        let provider = providers
            .get_provider(&binding.provider_id)
            .await?
            .ok_or_else(|| {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, provider_id = %binding.provider_id, "resolve_delivery_target: provider not found");
                ServiceError::InvalidOperation {
                    message: format!("provider '{}' not found", binding.provider_id),
                    request_id: None,
                }
            })?;
        if provider.disabled {
            warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, provider_id = %provider.provider_id, "resolve_delivery_target: provider disabled");
            return Err(ServiceError::InvalidOperation {
                message: format!("provider '{}' is disabled", provider.provider_id),
                request_id: None,
            });
        }
        let downlink = parse_downlink_config(&provider.config)?;
        if !downlink.enabled {
            warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, provider_id = %provider.provider_id, "resolve_delivery_target: provider downlink disabled");
            return Err(ServiceError::InvalidOperation {
                message: format!("provider '{}' downlink is disabled", provider.provider_id),
                request_id: None,
            });
        }

        let card = match &self.a2a_cards {
            Some(cards) => cards.get(bot_id).await?,
            None => None,
        };
        if let Some(card) = &card {
            if card.provider_id != binding.provider_id || card.provider_bot_ref != binding.provider_bot_ref {
                return Err(ServiceError::InvalidOperation {
                    message: "A2A configuration unavailable or binding changed".into(), request_id: None });
            }
        }
        let credential_kind = "downlink_bcs_to_provider";
        let credentials = self.provider_credentials.as_ref().ok_or_else(|| {
            ServiceError::InternalError("provider credential repo not configured".to_string())
        })?;
        let credential = credentials
            .get_credential_by_kind(&provider.provider_id, credential_kind)
            .await?
            .ok_or_else(|| {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, provider_id = %provider.provider_id, "resolve_delivery_target: downlink credential not found");
                ServiceError::InvalidOperation {
                    message: format!(
                        "provider '{}' downlink credential not found",
                        provider.provider_id
                    ),
                    request_id: None,
                }
            })?;
        if credential.disabled {
            warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, provider_id = %provider.provider_id, "resolve_delivery_target: downlink credential disabled");
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "provider '{}' downlink credential is disabled",
                    provider.provider_id
                ),
                request_id: None,
            });
        }

        if let Some(card) = card {
            if credential.secret_value.trim().is_empty() {
                return Err(ServiceError::InvalidOperation { message: "A2A credential unavailable".into(), request_id: None });
            }
            let extensions = if for_abort {vec![]} else {card.agent_card.pointer("/capabilities/extensions").and_then(serde_json::Value::as_array)
                .into_iter().flatten().map(|value| serde_json::from_value(value.clone()))
                .collect::<Result<Vec<_>,_>>()?};
            return Ok(BotDeliveryTarget::A2a { bot_id: bot.bot_uuid, provider_id: provider.provider_id,
                provider_bot_ref: binding.provider_bot_ref, interface: self.select_a2a_route(&card.agent_card)?,
                credential_kind: credential_kind.into(), bearer: RedactedToken::new(credential.secret_value),
                card_revision: card.revision, extensions });
        }
        let webhook_url = binding.webhook_url.as_ref().or(downlink.webhook_url.as_ref())
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: "missing_delivery_endpoint: configure a Bot or Provider webhook_url".to_string(),
                request_id: None,
            })?.clone();
        info!(
            bot_id = %bot_id,
            provider_id = %provider.provider_id,
            provider_bot_ref = %binding.provider_bot_ref,
            protocol_version = %downlink.protocol_version,
            "resolve_delivery_target: resolved http provider target"
        );
        Ok(BotDeliveryTarget::HttpProvider {
            bot_id: bot.bot_uuid,
            provider_id: provider.provider_id,
            provider_bot_ref: binding.provider_bot_ref,
            webhook_url,
            bcs_to_provider_token: RedactedToken::new(credential.secret_value),
            protocol_version: downlink.protocol_version,
        })
    }

    pub(super) async fn try_runtime_activity_for_impl(
        &self,
        bot_ids: &[String],
    ) -> ServiceResult<RuntimeActivitySnapshot> {
        let observed_at_ms = unix_millis_now();
        // ONE observation of the current instance's WS
        // connection set.
        let ws_connected = self
            .repo
            .list_connected()
            .await
            .into_iter()
            .collect::<HashSet<_>>();
        let mut active: HashSet<String> = bot_ids
            .iter()
            .filter(|bot_id| ws_connected.contains(*bot_id))
            .cloned()
            .collect();
        let mut provider_candidates = bot_ids
            .iter()
            .filter(|bot_id| !ws_connected.contains(*bot_id))
            .cloned()
            .collect::<Vec<_>>();
        // The core NEVER returns duplicates regardless of
        // caller input.
        provider_candidates.dedup();

        let Some(provider_bindings) = &self.provider_bindings else {
            return Ok(self.activity_snapshot(observed_at_ms, bot_ids, active));
        };
        let Some(provider_repo) = &self.provider_repo else {
            return Ok(self.activity_snapshot(observed_at_ms, bot_ids, active));
        };
        let Some(provider_credentials) = &self.provider_credentials else {
            return Ok(self.activity_snapshot(observed_at_ms, bot_ids, active));
        };

        // Bounded provider resolution: at most
        // AUTHORITY_QUERY_CHUNK ids per bindings query (spec
        // §8.0: 有界块数而非 N 次 DB 往返).
        let mut bindings = Vec::new();
        for chunk in provider_candidates.chunks(
            bcs_service_api::types::bot_authority::AUTHORITY_QUERY_CHUNK,
        ) {
            bindings.extend(
                provider_bindings
                    .list_bindings_by_bot_uuids(chunk)
                    .await?,
            );
        }
        let provider_ids = bindings
            .iter()
            .filter(|binding| !binding.disabled)
            .map(|binding| binding.provider_id.clone())
            .collect::<HashSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        if provider_ids.is_empty() {
            return Ok(self.activity_snapshot(observed_at_ms, bot_ids, active));
        }

        let providers = provider_repo
            .list_providers_by_ids(&provider_ids)
            .await?
            .into_iter()
            .map(|provider| (provider.provider_id.clone(), provider))
            .collect::<HashMap<_, _>>();
        let credentials = provider_credentials
            .list_credentials_by_kind_for_providers(
                &provider_ids,
                "downlink_bcs_to_provider",
            )
            .await?
            .into_iter()
            .map(|credential| (credential.provider_id.clone(), credential))
            .collect::<HashMap<_, _>>();

        let a2a = self.a2a_availability(&provider_candidates).await?;
        // Same routability predicates as the legacy
        // per-bot target resolution: binding, provider and
        // credential enabled, downlink enabled and one
        // delivery endpoint resolvable.
        for binding in bindings {
            if binding.disabled {
                continue;
            }
            let card = a2a.get(&binding.bot_uuid);
            if card.is_some_and(|card| !card.ready || card.provider_id != binding.provider_id
                || card.provider_bot_ref != binding.provider_bot_ref) { continue; }
            let Some(provider) = providers.get(&binding.provider_id) else {
                continue;
            };
            if provider.disabled {
                continue;
            }
            let Ok(downlink) = parse_downlink_config(&provider.config) else {
                continue;
            };
            if !downlink.enabled
                || (card.is_none() && binding.webhook_url.as_ref().or(downlink.webhook_url.as_ref()).is_none())
            {
                continue;
            }
            let Some(credential) = credentials.get(&binding.provider_id) else {
                continue;
            };
            if credential.disabled {
                continue;
            }
            active.insert(binding.bot_uuid);
        }
        Ok(self.activity_snapshot(observed_at_ms, bot_ids, active))
    }

    pub(super) async fn list_runtime_active_bot_ids_impl(
        &self,
        bot_ids: &[String],
    ) -> Vec<String> {
        let mut active = HashSet::new();
        let mut provider_candidates = Vec::new();
        for bot_id in bot_ids {
            if self.repo.is_connected(bot_id).await {
                active.insert(bot_id.clone());
            } else {
                provider_candidates.push(bot_id.clone());
            }
        }

        let a2a = match self.a2a_availability(&provider_candidates).await {
            Ok(states) => states,
            Err(error) => {
                warn!(%error, "A2A availability read failed");
                return active.into_iter().collect();
            }
        };
        let Some(provider_bindings) = &self.provider_bindings else {
            return active.into_iter().collect();
        };
        let Some(provider_repo) = &self.provider_repo else {
            return active.into_iter().collect();
        };
        let Some(provider_credentials) = &self.provider_credentials else {
            return active.into_iter().collect();
        };

        let bindings = match provider_bindings
            .list_bindings_by_bot_uuids(&provider_candidates)
            .await
        {
            Ok(bindings) => bindings,
            Err(error) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, error = %error, "list_runtime_active_bot_ids: failed to list provider bindings");
                return active.into_iter().collect();
            }
        };
        let provider_ids = bindings
            .iter()
            .filter(|binding| !binding.disabled)
            .map(|binding| binding.provider_id.clone())
            .collect::<HashSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        if provider_ids.is_empty() {
            return active.into_iter().collect();
        }

        let providers = match provider_repo.list_providers_by_ids(&provider_ids).await {
            Ok(providers) => providers,
            Err(error) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, error = %error, "list_runtime_active_bot_ids: failed to list providers");
                return active.into_iter().collect();
            }
        };
        let credentials = match provider_credentials
            .list_credentials_by_kind_for_providers(
                &provider_ids,
                "downlink_bcs_to_provider",
            )
            .await
        {
            Ok(credentials) => credentials,
            Err(error) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, error = %error, "list_runtime_active_bot_ids: failed to list provider credentials");
                return active.into_iter().collect();
            }
        };

        let providers = providers
            .into_iter()
            .map(|provider| (provider.provider_id.clone(), provider))
            .collect::<HashMap<_, _>>();
        let credentials = credentials
            .into_iter()
            .map(|credential| (credential.provider_id.clone(), credential))
            .collect::<HashMap<_, _>>();

        for binding in bindings {
            if binding.disabled {
                continue;
            }
            let card = a2a.get(&binding.bot_uuid);
            if card.is_some_and(|card| !card.ready || card.provider_id != binding.provider_id
                || card.provider_bot_ref != binding.provider_bot_ref) { continue; }
            let Some(provider) = providers.get(&binding.provider_id) else {
                continue;
            };
            if provider.disabled {
                continue;
            }
            let Ok(downlink) = parse_downlink_config(&provider.config) else {
                continue;
            };
            if !downlink.enabled
                || (card.is_none() && binding.webhook_url.as_ref().or(downlink.webhook_url.as_ref()).is_none())
            {
                continue;
            }
            let Some(credential) = credentials.get(&binding.provider_id) else {
                continue;
            };
            if credential.disabled {
                continue;
            }
            active.insert(binding.bot_uuid);
        }

        active.into_iter().collect()
    }
    fn select_a2a_route(&self, card:&serde_json::Value)->ServiceResult<bcs_service_api::types::a2a_bot::A2aInterface> {
        use bcs_service_api::types::a2a_bot::{select_a2a_interface,A2aBinding};
        select_a2a_interface(card,|binding,url|match binding {
            A2aBinding::HttpJson=>self.a2a_url_guard.validate_configured_http_url(url).is_ok(),
            A2aBinding::Grpc=>self.a2a_grpc_urls.iter().any(|allowed|allowed==url),
        }).map_err(|_|ServiceError::InvalidOperation {message:"A2A Card has no interface allowed by current deployment policy".into(),request_id:None})
    }
    async fn a2a_availability(&self, ids: &[String]) -> ServiceResult<HashMap<String, bcs_service_api::types::a2a_bot::A2aBotAvailability>> {
        let mut result = HashMap::new();
        if let Some(cards) = &self.a2a_cards {
            for chunk in ids.chunks(256) {
                result.extend(cards.availability_many(chunk).await?.into_iter().map(|mut state| {
                    state.ready=state.ready && self.select_a2a_route(&serde_json::json!({"supportedInterfaces":state.interfaces})).is_ok();
                    (state.bot_uuid.clone(),state)
                }));
            }
        }
        Ok(result)
    }
}
