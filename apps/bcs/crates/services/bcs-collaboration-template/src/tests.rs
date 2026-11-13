use super::*;
use bcs_service_api::{
    CollaborationTemplateFormat, GetCollaborationTemplateQuery,
    ListCollaborationTemplatesQuery,
};

fn seed_template_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../seeds/collaboration-templates")
}

const MINIMAL_TEMPLATE_YAML: &str = r#"
name: Good Template
metadata:
  description: A valid minimal template.
participants:
  assistant:
    display_name: Assistant
    description: Answers the user request.
    required: true
runtime:
  kind: state_machine
  state_machine:
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: assistant
        instruction: Answer the user query.
        final_output: true
"#;

fn write_template(
    root: &Path,
    lang: &str,
    id: &str,
    yaml: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let lang_dir = root.join(lang);
    fs::create_dir_all(&lang_dir)?;
    fs::write(lang_dir.join(format!("{id}.yaml")), yaml)?;
    Ok(())
}

#[tokio::test]
async fn coverage_template_repo_surfaces_registry_io_and_cached_file_errors() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    fs::write(root.join("registry.yaml"), "templates: [broken").unwrap();
    let repo = FileCollaborationTemplateRepo::new(root);
    assert!(matches!(repo.list_entries().await, Err(ServiceError::InternalError(message)) if message.contains("registry") && message.contains("invalid")));
    fs::remove_file(root.join("registry.yaml")).unwrap();
    fs::create_dir(root.join("registry.yaml")).unwrap();
    assert!(matches!(repo.list_entries().await, Err(ServiceError::IoError(_))));
    fs::remove_dir(root.join("registry.yaml")).unwrap();
    write_template(root, "en-US", "valid", MINIMAL_TEMPLATE_YAML).unwrap();
    fs::write(root.join("ignored.txt"), "ignored").unwrap();
    fs::create_dir(root.join("invalid_language!")).unwrap();
    fs::create_dir(root.join("en-US/nested.yaml")).unwrap();
    fs::write(root.join("en-US/bad!.yaml"), MINIMAL_TEMPLATE_YAML).unwrap();
    fs::write(root.join("en-US/ignored.txt"), "ignored").unwrap();
    assert_eq!(repo.list_entries().await.unwrap().iter().map(|e| e.id.as_str()).collect::<Vec<_>>(), ["valid"]);
    assert_eq!(repo.available_languages("missing").await.unwrap(), Vec::<String>::new());
    fs::remove_file(root.join("en-US/valid.yaml")).unwrap();
    assert!(matches!(repo.get_raw_yaml("valid", "en-US").await, Err(ServiceError::IoError(_))));
    assert!(repo.get_raw_yaml("missing", "en-US").await.unwrap().is_none());
}

#[tokio::test]
async fn coverage_template_cache_poison_and_blocking_panic_become_service_errors() {
    let repo = FileCollaborationTemplateRepo::new("unused");
    let cache = repo.index_cache.clone();
    assert!(std::thread::spawn(move || { let _guard = cache.write().unwrap(); panic!("poison fixture"); }).join().is_err());
    assert!(matches!(repo.supported_languages().await, Err(ServiceError::InternalError(message)) if message.contains("cache poisoned")));
    let failed = spawn_blocking_service::<(), _>(|| panic!("blocking fixture failure")).await;
    assert!(matches!(failed, Err(ServiceError::InternalError(message)) if message.contains("blocking task failed")));
}

#[tokio::test]
async fn coverage_template_language_negotiation_and_input_errors_are_explicit() {
    let dir = tempfile::tempdir().unwrap();
    write_template(dir.path(), "en-US", "valid", MINIMAL_TEMPLATE_YAML).unwrap();
    let service = CollaborationTemplateServiceImpl::new(Arc::new(FileCollaborationTemplateRepo::new(dir.path())), "en-US");
    let invalid_tags = service.list_templates(ListCollaborationTemplatesQuery { tags: vec!["bad/tag".into()], ..Default::default() }).await;
    assert!(matches!(invalid_tags, Err(CollaborationTemplateError::InvalidTags(tag)) if tag == "bad/tag"));
    let invalid_lang = service.list_templates(ListCollaborationTemplatesQuery { requested_language: Some("../en".into()), ..Default::default() }).await;
    assert!(matches!(invalid_lang, Err(CollaborationTemplateError::InvalidLanguage(lang)) if lang == "../en"));
    let listed = service.list_templates(ListCollaborationTemplatesQuery {
        accept_language: Some(" , *, invalid!, fr;q=0.3, en-US;q=0.9, de;q=NaN, es;q=bad".into()), ..Default::default()
    }).await.unwrap();
    assert_eq!(listed.templates.len(), 1);
    assert_eq!(listed.templates[0].name, "Good Template");
    let missing = service.load_definition("absent", "en-US").await.unwrap_err();
    assert!(matches!(missing, CollaborationTemplateError::LanguageNotAvailable { id, requested } if id == "absent" && requested == "en-US"));
    fs::remove_file(dir.path().join("en-US/valid.yaml")).unwrap();
    assert!(matches!(service.load_definition("valid", "en-US").await, Err(CollaborationTemplateError::Io(_))));
}

#[tokio::test]
async fn lists_default_language_templates_by_priority() -> Result<(), Box<dyn std::error::Error>>
{
    let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
    let service =
        CollaborationTemplateServiceImpl::new(repo, "zh-CN").with_judge_templates_enabled(true);

    let response = service
        .list_templates(ListCollaborationTemplatesQuery::default())
        .await?;

    assert_eq!(response.default_language, "zh-CN");
    assert_eq!(response.supported_languages, vec!["zh-CN", "en-US"]);
    assert_eq!(
        response
            .templates
            .iter()
            .map(|template| template.id.as_str())
            .collect::<Vec<_>>(),
        vec![
            "write-and-review",
            "parallel-expert-review",
            "solution-and-risk-review",
            "bot-human-bot-review",
            "world-cup-preview-content-production",
            "micro-merchant-event-orchestration",
            "write-review-loop",
            "research-writing-loops",
            "nested-review-loops",
            "single-bot-guided-answer",
        ]
    );
    let first = response
        .templates
        .first()
        .ok_or_else(|| std::io::Error::other("missing first template"))?;
    assert_eq!(first.name, "写作质检协同");
    assert!(first.participants.contains_key("writer"));
    assert_eq!(response.tag_labels["judge"]["zh-CN"], "自动评审");
    assert_eq!(response.tag_labels["serial"]["zh-CN"], "串行");

    Ok(())
}

#[tokio::test]
async fn list_marks_judge_templates_when_disabled() -> Result<(), Box<dyn std::error::Error>>
{
    let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
    let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN");

    let response = service
        .list_templates(ListCollaborationTemplatesQuery::default())
        .await?;

    assert_eq!(
        response
            .templates
            .iter()
            .map(|template| template.id.as_str())
            .collect::<Vec<_>>(),
        vec![
            "parallel-expert-review",
            "solution-and-risk-review",
            "world-cup-preview-content-production",
            "micro-merchant-event-orchestration",
            "single-bot-guided-answer",
            "bot-human-bot-review",
            "nested-review-loops",
            "research-writing-loops",
            "write-and-review",
            "write-review-loop",
        ]
    );
    let judge_template = response
        .templates
        .iter().find(|template| template.id == "write-and-review")
        .ok_or_else(|| std::io::Error::other("missing judge template"))?;
    assert_eq!(judge_template.name, "写作质检协同（需要启用 LLM）");
    assert_eq!(judge_template.priority, DEFAULT_PRIORITY);

    let english_response = service
        .list_templates(ListCollaborationTemplatesQuery {
            requested_language: Some("en-US".to_string()),
            accept_language: None,
            tags: vec!["judge".to_string()],
        })
        .await?;
    assert_eq!(english_response.templates.len(), 5);
    assert_eq!(
        english_response
            .templates
            .iter()
            .map(|template| template.name.as_str())
            .collect::<Vec<_>>(),
        vec![
            "Bot-Human-Bot Review (requires LLM)",
            "Nested Delivery Review Loops (requires LLM)",
            "Research and Writing Loops (requires LLM)",
            "Write & Review (requires LLM)",
            "Writing Review Loop (requires LLM)",
        ]
    );

    Ok(())
}

#[tokio::test]
async fn filters_by_tag_and_language() -> Result<(), Box<dyn std::error::Error>> {
    let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
    let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN");

    let response = service
        .list_templates(ListCollaborationTemplatesQuery {
            requested_language: Some("en-US".to_string()),
            accept_language: None,
            tags: vec!["parallel".to_string()],
        })
        .await?;

    assert_eq!(
        response
            .templates
            .iter()
            .map(|template| template.id.as_str())
            .collect::<Vec<_>>(),
        vec![
            "parallel-expert-review",
            "solution-and-risk-review",
            "world-cup-preview-content-production",
            "micro-merchant-event-orchestration",
        ]
    );
    assert_eq!(
        response.templates[0].name,
        "Parallel Expert Review"
    );

    Ok(())
}

#[tokio::test]
async fn returns_localized_loop_template_with_editorial_judge() -> Result<(), Box<dyn std::error::Error>> {
    let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
    let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN").with_judge_templates_enabled(true);
    for (lang, name) in [("zh-CN", "写作评审循环"), ("en-US", "Writing Review Loop")] {
        let list = service.list_templates(ListCollaborationTemplatesQuery {
            requested_language: Some(lang.into()),
            accept_language: None,
            tags: vec!["loop".into()],
        }).await?;
        assert_eq!(list.templates.len(), 3);
        assert_eq!(list.templates[0].id, "write-review-loop");
        assert_eq!(list.templates[0].name, name);
        assert_eq!(list.templates[0].priority, 35);
        assert_eq!(list.templates[0].available_languages, vec!["zh-CN", "en-US"]);
        assert_eq!(list.tag_labels["loop"][lang], if lang == "zh-CN" { "循环" } else { "Loop" });
        for role in ["writer", "reviewer", "polisher"] {
            assert!(list.templates[0].participants[role].required);
        }
        let detail = service.get_template(GetCollaborationTemplateQuery {
            template_id: "write-review-loop".into(),
            requested_language: Some(lang.into()),
            accept_language: None,
            format: CollaborationTemplateFormat::Yaml,
        }).await?;
        assert_eq!(detail.name, name);
        assert_eq!(detail.lang, lang);
        assert_eq!(detail.definition["runtime"]["state_machine"]["version"], 2);
        assert_eq!(detail.definition["runtime"]["state_machine"]["nodes"]["revision_rounds"]["loop"]["max_iterations"], 3);
        let parsed: CollaborationDefinition = serde_yaml::from_str(&detail.yaml)?;
        assert!(parsed.uses_judge());
    }
    Ok(())
}

#[tokio::test]
async fn returns_localized_multiple_loop_template() -> Result<(), Box<dyn std::error::Error>> {
    let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
    let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN").with_judge_templates_enabled(true);
    for (lang, name) in [("zh-CN", "资料与写作双循环"), ("en-US", "Research and Writing Loops")] {
        let list = service.list_templates(ListCollaborationTemplatesQuery {
            requested_language: Some(lang.into()), accept_language: None, tags: vec!["loop".into()],
        }).await?;
        let template = list.templates.iter().find(|item| item.id == "research-writing-loops").unwrap();
        assert_eq!(template.name, name);
        assert_eq!(template.priority, 36);
        assert_eq!(template.available_languages, vec!["zh-CN", "en-US"]);
        for role in ["researcher", "writer", "reviewer", "polisher"] {
            assert!(template.participants[role].required);
        }
        let detail = service.get_template(GetCollaborationTemplateQuery {
            template_id: template.id.clone(), requested_language: Some(lang.into()),
            accept_language: None, format: CollaborationTemplateFormat::Yaml,
        }).await?;
        assert_eq!(detail.name, name);
        let nodes = &detail.definition["runtime"]["state_machine"]["nodes"];
        assert_eq!(nodes.as_object().unwrap().values().filter(|node| node["kind"] == "loop").count(), 2);
        assert_eq!(nodes["research_loop"]["transitions"]["approved"]["targets"], serde_json::json!(["writing_loop"]));
        let parsed: CollaborationDefinition = serde_yaml::from_str(&detail.yaml)?;
        assert!(parsed.uses_judge());
    }
    Ok(())
}

#[tokio::test]
async fn returns_localized_nested_loop_template() -> Result<(), Box<dyn std::error::Error>> {
    let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
    let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN").with_judge_templates_enabled(true);
    for (lang, name) in [("zh-CN", "嵌套交付评审循环"), ("en-US", "Nested Delivery Review Loops")] {
        let list = service.list_templates(ListCollaborationTemplatesQuery {
            requested_language: Some(lang.into()), accept_language: None, tags: vec!["loop".into()],
        }).await?;
        let template = list.templates.iter().find(|item| item.id == "nested-review-loops").unwrap();
        assert_eq!(template.name, name);
        assert_eq!(template.priority, 37);
        assert_eq!(template.available_languages, vec!["zh-CN", "en-US"]);
        for role in ["author", "reviewer", "owner"] {
            assert!(template.participants[role].required);
        }
        let detail = service.get_template(GetCollaborationTemplateQuery {
            template_id: template.id.clone(), requested_language: Some(lang.into()),
            accept_language: None, format: CollaborationTemplateFormat::Yaml,
        }).await?;
        assert_eq!(detail.name, name);
        let outer = &detail.definition["runtime"]["state_machine"]["nodes"]["delivery_cycles"];
        assert_eq!(outer["loop"]["max_iterations"], 2);
        assert_eq!(outer["loop"]["nodes"]["review_rounds"]["kind"], "loop");
        assert_eq!(outer["loop"]["nodes"]["review_rounds"]["loop"]["max_iterations"], 3);
        let parsed: CollaborationDefinition = serde_yaml::from_str(&detail.yaml)?;
        assert!(parsed.uses_judge());
    }
    Ok(())
}

#[tokio::test]
async fn returns_raw_yaml_and_parsed_definition() -> Result<(), Box<dyn std::error::Error>> {
    let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
    let service =
        CollaborationTemplateServiceImpl::new(repo, "zh-CN").with_judge_templates_enabled(true);

    let detail = service
        .get_template(GetCollaborationTemplateQuery {
            template_id: "write-and-review".to_string(),
            requested_language: Some("zh-CN".to_string()),
            accept_language: None,
            format: CollaborationTemplateFormat::Json,
        })
        .await?;

    assert_eq!(detail.id, "write-and-review");
    assert_eq!(detail.lang, "zh-CN");
    assert!(detail.yaml.contains("# 写作质检协同模板。"));
    assert_eq!(detail.definition["name"], "写作质检协同");
    assert!(detail.definition.get("id").is_none());

    Ok(())
}

#[tokio::test]
async fn returns_bot_human_bot_template_with_human_input_node(
) -> Result<(), Box<dyn std::error::Error>> {
    let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
    let service =
        CollaborationTemplateServiceImpl::new(repo, "zh-CN").with_judge_templates_enabled(true);

    let detail = service
        .get_template(GetCollaborationTemplateQuery {
            template_id: "bot-human-bot-review".to_string(),
            requested_language: Some("zh-CN".to_string()),
            accept_language: None,
            format: CollaborationTemplateFormat::Json,
        })
        .await?;

    assert_eq!(detail.name, "Bot-Human-Bot 三节点协作");
    assert_eq!(
        detail.definition["runtime"]["state_machine"]["nodes"]["human_review"]["kind"],
        "human_input"
    );
    assert!(
        detail.definition["runtime"]["state_machine"]
            .get("human_input_channel")
            .is_none()
    );
    assert!(
        detail.definition["runtime"]["state_machine"]["nodes"]["human_review"]
            .get("assignee")
            .is_none()
    );
    assert!(
        detail.definition["runtime"]["state_machine"]["nodes"]["human_review"]
            .get("notification")
            .is_none()
    );
    assert_eq!(
        detail.definition["runtime"]["state_machine"]["nodes"]["human_review"]
            ["node_timeout_ms"],
        86_400_000
    );

    Ok(())
}

#[tokio::test]
async fn get_returns_judge_template_when_disabled() -> Result<(), Box<dyn std::error::Error>> {
    let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
    let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN");

    let detail = service
        .get_template(GetCollaborationTemplateQuery {
            template_id: "write-and-review".to_string(),
            requested_language: Some("zh-CN".to_string()),
            accept_language: None,
            format: CollaborationTemplateFormat::Yaml,
        })
        .await?;

    assert_eq!(detail.id, "write-and-review");
    assert_eq!(detail.name, "写作质检协同");
    assert!(detail.yaml.contains("judge:"));
    Ok(())
}

#[tokio::test]
async fn rejects_path_traversal_template_id() -> Result<(), Box<dyn std::error::Error>> {
    let repo = Arc::new(FileCollaborationTemplateRepo::new(seed_template_dir()));
    let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN");

    let error = service
        .get_template(GetCollaborationTemplateQuery {
            template_id: "..".to_string(),
            requested_language: Some("zh-CN".to_string()),
            accept_language: None,
            format: CollaborationTemplateFormat::Yaml,
        })
        .await
        .err()
        .ok_or_else(|| std::io::Error::other("expected error"))?;

    assert!(matches!(error, CollaborationTemplateError::NotFound(_)));
    Ok(())
}

#[tokio::test]
async fn list_skips_malformed_template_yaml() -> Result<(), Box<dyn std::error::Error>> {
    let temp_dir = tempfile::tempdir()?;
    write_template(
        temp_dir.path(),
        "zh-CN",
        "good-template",
        MINIMAL_TEMPLATE_YAML,
    )?;
    write_template(
        temp_dir.path(),
        "zh-CN",
        "broken-template",
        "name: [not valid yaml",
    )?;

    let repo = Arc::new(FileCollaborationTemplateRepo::new(temp_dir.path()));
    let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN");

    let response = service
        .list_templates(ListCollaborationTemplatesQuery::default())
        .await?;

    assert_eq!(
        response
            .templates
            .iter()
            .map(|template| template.id.as_str())
            .collect::<Vec<_>>(),
        vec!["good-template"]
    );
    Ok(())
}

#[tokio::test]
async fn missing_template_dir_is_empty_and_not_found() -> Result<(), Box<dyn std::error::Error>>
{
    let temp_dir = tempfile::tempdir()?;
    let missing_dir = temp_dir.path().join("missing");
    let repo = Arc::new(FileCollaborationTemplateRepo::new(missing_dir));
    let service = CollaborationTemplateServiceImpl::new(repo, "zh-CN");

    let response = service
        .list_templates(ListCollaborationTemplatesQuery::default())
        .await?;
    assert!(response.templates.is_empty());
    assert!(response.supported_languages.is_empty());

    let error = service
        .get_template(GetCollaborationTemplateQuery {
            template_id: "anything".to_string(),
            requested_language: Some("zh-CN".to_string()),
            accept_language: None,
            format: CollaborationTemplateFormat::Yaml,
        })
        .await
        .err()
        .ok_or_else(|| std::io::Error::other("expected error"))?;
    assert!(matches!(error, CollaborationTemplateError::NotFound(_)));

    Ok(())
}
