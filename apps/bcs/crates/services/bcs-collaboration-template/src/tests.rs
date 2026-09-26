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
    assert_eq!(english_response.templates.len(), 4);
    assert_eq!(
        english_response
            .templates
            .iter()
            .map(|template| template.name.as_str())
            .collect::<Vec<_>>(),
        vec![
            "Bot-Human-Bot Review (requires LLM)",
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
        assert_eq!(list.templates.len(), 2);
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
