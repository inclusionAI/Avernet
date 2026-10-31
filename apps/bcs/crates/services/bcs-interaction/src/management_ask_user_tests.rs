use super::*;

    #[tokio::test]
    async fn ask_user_submit_requires_answers_for_every_question() {
        let (service, _store, provider, _frontend) = service(true);
        let mut ask = requested("ask-1");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-1",
            "kind":"ask_user",
            "questions":[
                {"questionId":"target","question":"Where?","options":[
                    {"value":"staging","label":"Staging"},
                    {"value":"prod","label":"Production"}
                ]},
                {"questionId":"confirm","question":"Continue?","options":[
                    {"value":"yes","label":"Yes"},
                    {"value":"no","label":"No"}
                ]}
            ]
        });
        service.on_provider_requested(ask).await.unwrap();
        let mut incomplete = resolve("ask-1", "idem-ask");
        incomplete.resolution = json!({
            "action":"submit",
            "answers":{"target":{"values":["staging"]}}
        });
        assert!(matches!(
            service.resolve(incomplete).await,
            Err(InteractionServiceError::InvalidRequest(_))
        ));
        assert!(provider.calls.lock().await.is_empty());
    }

    #[tokio::test]
    async fn ask_user_submit_accepts_empty_array_as_explicit_skip() {
        let (service, _store, provider, _frontend) = service(true);
        let mut ask = requested("ask-skip");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-skip",
            "kind":"ask_user",
            "questions":[
                {
                    "questionId":"question_1",
                    "header":"Delete scope",
                    "question":"Which records should be deleted?",
                    "allowOther":false,
                    "options":[
                        {"value":"all","label":"All records"},
                        {"value":"none","label":"No records"}
                    ]
                },
                {
                    "questionId":"question_2",
                    "header":"Delete confirmation",
                    "question":"When should deletion happen?",
                    "options":[
                        {"value":"人确认后才删","label":"人确认后才删"},
                        {"value":"自动删除","label":"自动删除"}
                    ]
                }
            ]
        });
        service.on_provider_requested(ask).await.unwrap();

        let mut command = resolve("ask-skip", "idem-skip");
        command.resolution = json!({
            "action":"submit",
            "answers":{
                "question_1":{"values":[]},
                "question_2":{"values":["人确认后才删"]}
            }
        });

        let result = service.resolve(command).await.unwrap();

        assert!(result.accepted);
        let calls = provider.calls.lock().await;
        assert_eq!(calls.len(), 1);
        assert_eq!(
            calls[0].resolution,
            json!({
                "action":"submit",
                "answers":{
                    "question_1":{
                        "values":[],
                        "question":"Which records should be deleted?",
                        "header":"Delete scope"
                    },
                    "question_2":{
                        "values":["人确认后才删"],
                        "question":"When should deletion happen?",
                        "header":"Delete confirmation"
                    }
                }
            })
        );
    }

    #[tokio::test]
    async fn ask_user_submit_rejects_missing_non_string_and_blank_values() {
        for (index, (invalid_answer, expected_message)) in [
            (
                json!({}),
                "ask_user answer question_1 requires values",
            ),
            (
                json!({"values":[null]}),
                "ask_user answer question_1 values must be non-empty strings",
            ),
            (
                json!({"values":[""]}),
                "ask_user answer question_1 values must be non-empty strings",
            ),
            (
                json!({"values":["   "]}),
                "ask_user answer question_1 values must be non-empty strings",
            ),
        ]
        .into_iter()
        .enumerate()
        {
            let (service, _store, provider, _frontend) = service(true);
            let interaction_id = format!("ask-invalid-value-{index}");
            let mut ask = requested(&interaction_id);
            ask.kind = InteractionKind::AskUser;
            ask.payload = json!({
                "runId":"provider-run-1",
                "phase":"requested",
                "interactionId":interaction_id,
                "kind":"ask_user",
                "questions":[{
                    "questionId":"question_1",
                    "header":"Comment",
                    "question":"Anything else?"
                }]
            });
            service.on_provider_requested(ask).await.unwrap();

            let mut command = resolve(&interaction_id, &format!("idem-invalid-value-{index}"));
            command.resolution = json!({
                "action":"submit",
                "answers":{"question_1":invalid_answer}
            });

            assert_eq!(
                service.resolve(command).await.unwrap_err(),
                InteractionServiceError::InvalidRequest(expected_message.to_string())
            );
            assert!(provider.calls.lock().await.is_empty());
        }
    }

    #[tokio::test]
    async fn ask_user_submit_augments_answers_with_origin_question() {
        let (service, _store, provider, _frontend) = service(true);
        let mut ask = requested("ask-1");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-1",
            "kind":"ask_user",
            "questions":[
                {"questionId":"target","header":"Deployment environment","question":"Where should this be deployed?","options":[
                    {"value":"staging","label":"Staging"},
                    {"value":"prod","label":"Production"}
                ]},
                {"questionId":"components","header":"Components","question":"Which components?","multiSelect":true,"options":[
                    {"value":"web","label":"Web"},
                    {"value":"worker","label":"Worker"}
                ]}
            ]
        });
        service.on_provider_requested(ask).await.unwrap();

        let mut command = resolve("ask-1", "idem-ask");
        command.resolution = json!({
            "action":"submit",
            "answers":{
                "target":{
                    "values":["staging"],
                    "question":"frontend question",
                    "header":"frontend header"
                },
                "components":{"values":["web","worker"]}
            }
        });
        let result = service.resolve(command).await.unwrap();
        assert!(result.accepted);

        let calls = provider.calls.lock().await;
        let resolution = &calls[0].resolution;
        assert_eq!(resolution["action"], "submit");
        assert_eq!(
            resolution["answers"]["target"]["values"],
            json!(["staging"])
        );
        assert_eq!(
            resolution["answers"]["target"]["question"],
            "Where should this be deployed?"
        );
        assert_eq!(
            resolution["answers"]["target"]["header"],
            "Deployment environment"
        );
        assert_eq!(
            resolution["answers"]["components"]["values"],
            json!(["web", "worker"])
        );
        assert_eq!(
            resolution["answers"]["components"]["question"],
            "Which components?"
        );
        assert_eq!(resolution["answers"]["components"]["header"], "Components");
    }

    #[tokio::test]
    async fn ask_user_submit_defaults_missing_allow_other_to_true() {
        let (service, _store, provider, _frontend) = service(true);
        let mut ask = requested("ask-custom");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-custom",
            "kind":"ask_user",
            "questions":[
                {
                    "questionId":"target",
                    "header":"Deployment environment",
                    "question":"Where should this be deployed?",
                    "options":[{"value":"staging","label":"Staging"}]
                },
                {
                    "questionId":"components",
                    "header":"Components",
                    "question":"Which components?",
                    "multiSelect":true,
                    "allowOther":true,
                    "options":[
                        {"value":"web","label":"Web"},
                        {"value":"worker","label":"Worker"}
                    ]
                }
            ]
        });
        service.on_provider_requested(ask).await.unwrap();

        let mut command = resolve("ask-custom", "idem-custom");
        command.resolution = json!({
            "action":"submit",
            "answers":{
                "target":{
                    "values":["private cloud"]
                },
                "components":{
                    "values":["web", "scheduler"]
                }
            }
        });

        let result = service.resolve(command).await.unwrap();

        assert!(result.accepted);
        let calls = provider.calls.lock().await;
        assert_eq!(calls.len(), 1);
        assert_eq!(
            calls[0].resolution["answers"]["target"],
            json!({
                "values":[],
                "customValues":["private cloud"],
                "question":"Where should this be deployed?",
                "header":"Deployment environment"
            })
        );
        assert_eq!(
            calls[0].resolution["answers"]["components"],
            json!({
                "values":["web"],
                "customValues":["scheduler"],
                "question":"Which components?",
                "header":"Components"
            })
        );
    }

    #[tokio::test]
    async fn ask_user_submit_rejects_custom_value_when_allow_other_is_false_and_logs_context() {
        let (service, _store, provider, _frontend) = service(true);
        let mut ask = requested("ask-disallowed");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-disallowed",
            "kind":"ask_user",
            "questions":[{
                "questionId":"target",
                "question":"Where?",
                "allowOther":false,
                "options":[{"value":"staging","label":"Staging"}]
            }]
        });
        service.on_provider_requested(ask).await.unwrap();

        let mut command = resolve("ask-disallowed", "idem-invalid-custom");
        command.resolution = json!({
            "action":"submit",
            "answers":{"target":{"values":["private cloud"]}}
        });
        let logs = capture_logs();
        logs.0.lock().expect("log buffer lock").clear();

        let result = service.resolve(command).await;

        assert_eq!(
            result.unwrap_err(),
            InteractionServiceError::InvalidRequest(
                "ask_user question target does not allow custom answers; select one of the provided options"
                    .to_string()
            )
        );
        assert!(provider.calls.lock().await.is_empty());
        let logs =
            String::from_utf8(logs.0.lock().expect("log buffer lock").clone()).expect("UTF-8 logs");
        for expected in [
            "interaction resolution rejected",
            "bcs_run_id=bcs-run-1",
            "interaction_id=ask-disallowed",
            "bcs_session_id=session-1",
            "group_id=group-1",
            "bot_id=bot-1",
            "provider_run_id=provider-run-1",
            "resolver_actor_id=human_1",
            "ask_user question target does not allow custom answers; select one of the provided options",
        ] {
            assert!(
                logs.contains(expected),
                "missing {expected:?} in logs: {logs}"
            );
        }
    }

    #[tokio::test]
    async fn ask_user_submit_omits_header_when_requested_header_is_absent() {
        let (service, _store, provider, _frontend) = service(true);
        let mut ask = requested("ask-1");
        ask.kind = InteractionKind::AskUser;
        ask.payload = json!({
            "runId":"provider-run-1",
            "phase":"requested",
            "interactionId":"ask-1",
            "kind":"ask_user",
            "questions":[{
                "questionId":"target",
                "question":"Where?",
                "options":[{"value":"staging","label":"Staging"}]
            }]
        });
        service.on_provider_requested(ask).await.unwrap();

        let mut command = resolve("ask-1", "idem-ask");
        command.resolution = json!({
            "action":"submit",
            "answers":{
                "target":{
                    "values":["staging"],
                    "question":"frontend question",
                    "header":"untrusted"
                }
            }
        });
        let result = service.resolve(command).await.unwrap();
        assert!(result.accepted);

        let calls = provider.calls.lock().await;
        let answer = &calls[0].resolution["answers"]["target"];
        assert_eq!(answer["question"], "Where?");
        assert!(answer.get("header").is_none());
    }

    #[tokio::test]
    async fn ask_user_rejects_all_secret_field_aliases() {
        for secret_field in ["secret", "isSecret"] {
            let (service, _store, _provider, _frontend) = service(true);
            let mut ask = requested("ask-secret");
            ask.kind = InteractionKind::AskUser;
            ask.payload = json!({
                "runId":"provider-run-1",
                "phase":"requested",
                "interactionId":"ask-secret",
                "kind":"ask_user",
                "questions":[{
                    "questionId":"credential",
                    "question":"Enter credential",
                    (secret_field): true
                }]
            });

            assert!(service.on_provider_requested(ask).await.is_err());
        }
    }

    #[tokio::test]
    async fn rejects_oversized_requested_payload_before_storing_or_publishing() {
        let (service, store, _provider, frontend) = service(true);
        let mut oversized = requested("oversized");
        oversized.payload["command"] = json!("x".repeat(crate::management::MAX_INTERACTION_PAYLOAD_BYTES));

        assert!(service.on_provider_requested(oversized).await.is_err());
        assert!(store.list_pending("session-1").await.unwrap().is_empty());
        assert!(frontend.calls.lock().await.is_empty());
    }
