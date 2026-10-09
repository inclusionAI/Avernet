use std::path::PathBuf;

use anyhow::{Context, Result};
use clap::Args;

use crate::{
    bcs_root,
    seed_loader::{
        TemplateSeedCatalog, TemplateSeedContent, TemplateSeedEntry, load_template_seed_catalog,
    },
};

#[derive(Debug, Args)]
pub struct SeedArgs {
    /// Seed source directory containing registry.yaml and language subdirectories.
    #[arg(long)]
    pub source: Option<PathBuf>,

    /// Emit SQL without connecting to a database.
    #[arg(long)]
    pub emit_sql: bool,

    /// Validate and summarize seed input without emitting SQL.
    #[arg(long)]
    pub dry_run: bool,

    /// Operator identity written to created_by/updated_by for system seed rows.
    #[arg(long, default_value = "bcs-admin")]
    pub actor: String,

    /// Wrap emitted seed SQL in START TRANSACTION / COMMIT.
    ///
    /// Disabled by default because some database review platforms only accept plain DML
    /// statements in pre-check parsers.
    #[arg(long)]
    pub include_transaction: bool,

    /// Remove existing tag relations for each seeded template before inserting current tags.
    ///
    /// By default seed SQL is insert-only for template tags, which keeps initial deployment
    /// scripts friendly to database change review. Enable this only for explicit tag sync.
    #[arg(long)]
    pub prune_stale_tags: bool,
}

pub fn emit_seed_sql(args: &SeedArgs, env: &str) -> Result<String> {
    let catalog = load_catalog(args)?;
    render_seed_sql(
        &catalog,
        env,
        &args.actor,
        SeedSqlOptions {
            include_transaction: args.include_transaction,
            prune_stale_tags: args.prune_stale_tags,
        },
    )
}

pub fn dry_run_seed(args: &SeedArgs) -> Result<String> {
    let catalog = load_catalog(args)?;
    let content_count: usize = catalog
        .templates
        .iter()
        .map(|template| template.contents.len())
        .sum();
    Ok(format!(
        "Loaded {} templates, {} localized contents, languages: {}",
        catalog.templates.len(),
        content_count,
        catalog.supported_languages.join(", ")
    ))
}

fn load_catalog(args: &SeedArgs) -> Result<TemplateSeedCatalog> {
    let source = args
        .source
        .clone()
        .unwrap_or_else(default_seed_source_dir);
    load_template_seed_catalog(&source)
        .with_context(|| format!("load collaboration template seed from '{}'", source.display()))
}

fn default_seed_source_dir() -> PathBuf {
    let relative = PathBuf::from("seeds/collaboration-templates");
    if let Ok(cwd) = std::env::current_dir() {
        let cwd_source = cwd.join(&relative);
        if cwd_source.exists() {
            return cwd_source;
        }
    }

    let source_tree_source = bcs_root().join(&relative);
    if source_tree_source.exists() {
        return source_tree_source;
    }

    relative
}

pub fn render_seed_sql(
    catalog: &TemplateSeedCatalog,
    env: &str,
    actor: &str,
    options: SeedSqlOptions,
) -> Result<String> {
    let mut output = String::new();
    if options.include_transaction {
        output.push_str("START TRANSACTION;\n\n");
    }
    for template in &catalog.templates {
        push_template_sql(&mut output, env, actor, template)?;
        push_tag_sql(&mut output, env, template, options.prune_stale_tags);
        for content in &template.contents {
            push_content_sql(&mut output, env, content)?;
        }
        output.push('\n');
    }
    if options.include_transaction {
        output.push_str("COMMIT;\n");
    }
    Ok(output)
}

#[derive(Debug, Clone, Copy, Default)]
pub struct SeedSqlOptions {
    pub include_transaction: bool,
    pub prune_stale_tags: bool,
}

fn push_template_sql(
    output: &mut String,
    env: &str,
    actor: &str,
    template: &TemplateSeedEntry,
) -> Result<()> {
    output.push_str("INSERT INTO `bcs_collaboration_templates` ");
    output.push_str("(`env`, `template_id`, `source_type`, `visibility`, `owner_user_id`, `priority`, `record_status`, `created_by`, `updated_by`) VALUES ");
    output.push_str(&format!(
        "({}, {}, 'system', 'public', NULL, {}, 'active', {}, {})",
        sql_string(env),
        sql_string(&template.id),
        template.priority,
        sql_string(actor),
        sql_string(actor),
    ));
    output.push_str(" ON DUPLICATE KEY UPDATE ");
    output.push_str("`source_type` = VALUES(`source_type`), ");
    output.push_str("`visibility` = VALUES(`visibility`), ");
    output.push_str("`owner_user_id` = VALUES(`owner_user_id`), ");
    output.push_str("`priority` = VALUES(`priority`), ");
    output.push_str("`record_status` = VALUES(`record_status`), ");
    output.push_str("`updated_by` = VALUES(`updated_by`);\n");
    Ok(())
}

fn push_tag_sql(
    output: &mut String,
    env: &str,
    template: &TemplateSeedEntry,
    prune_stale_tags: bool,
) {
    if prune_stale_tags {
        output.push_str(&format!(
            "DELETE FROM `bcs_collaboration_template_tags` WHERE `env` = {} AND `template_id` = {};\n",
            sql_string(env),
            sql_string(&template.id),
        ));
    }
    if template.tags.is_empty() {
        return;
    }

    output.push_str(
        "INSERT IGNORE INTO `bcs_collaboration_template_tags` (`env`, `template_id`, `tag`) VALUES ",
    );
    for (index, tag) in template.tags.iter().enumerate() {
        if index > 0 {
            output.push_str(", ");
        }
        output.push_str(&format!(
            "({}, {}, {})",
            sql_string(env),
            sql_string(&template.id),
            sql_string(tag),
        ));
    }
    output.push_str(";\n");
}

fn push_content_sql(
    output: &mut String,
    env: &str,
    content: &TemplateSeedContent,
) -> Result<()> {
    let participant_summary_json = serde_json::to_string(&content.participant_summary_json)?;
    let definition_json = serde_json::to_string(&content.definition_json)?;
    output.push_str("INSERT INTO `bcs_collaboration_template_contents` ");
    output.push_str("(`env`, `template_id`, `lang`, `name`, `description`, `participant_summary_json`, `definition_json`, `yaml_text`, `yaml_sha256`, `version`, `record_status`) VALUES ");
    output.push_str(&format!(
        "({}, {}, {}, {}, {}, {}, {}, {}, {}, {}, 'active')",
        sql_string(env),
        sql_string(&content.template_id),
        sql_string(&content.lang),
        sql_string(&content.name),
        sql_nullable_string(content.description.as_deref()),
        sql_string(&participant_summary_json),
        sql_string(&definition_json),
        sql_string(&content.yaml_text),
        sql_string(&content.yaml_sha256),
        content.version,
    ));
    output.push_str(" ON DUPLICATE KEY UPDATE ");
    output.push_str("`name` = VALUES(`name`), ");
    output.push_str("`description` = VALUES(`description`), ");
    output.push_str("`participant_summary_json` = VALUES(`participant_summary_json`), ");
    output.push_str("`definition_json` = VALUES(`definition_json`), ");
    output.push_str("`version` = IF(`yaml_sha256` <> VALUES(`yaml_sha256`), `version` + 1, `version`), ");
    output.push_str("`yaml_text` = VALUES(`yaml_text`), ");
    output.push_str("`yaml_sha256` = VALUES(`yaml_sha256`), ");
    output.push_str("`record_status` = VALUES(`record_status`);\n");
    Ok(())
}

fn sql_nullable_string(value: Option<&str>) -> String {
    value.map(sql_string).unwrap_or_else(|| "NULL".to_string())
}

fn sql_string(value: &str) -> String {
    let mut escaped = String::with_capacity(value.len() + 2);
    escaped.push('\'');
    for ch in value.chars() {
        match ch {
            '\0' => escaped.push_str("\\0"),
            '\n' => escaped.push_str("\\n"),
            '\r' => escaped.push_str("\\r"),
            '\t' => escaped.push_str("\\t"),
            '\u{0008}' => escaped.push_str("\\b"),
            '\u{001a}' => escaped.push_str("\\Z"),
            '\'' => escaped.push_str("''"),
            '\\' => escaped.push_str("\\\\"),
            _ => escaped.push(ch),
        }
    }
    escaped.push('\'');
    escaped
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_catalog() -> TemplateSeedCatalog {
        TemplateSeedCatalog {
            supported_languages: vec!["en-US".to_string()],
            templates: vec![TemplateSeedEntry {
                id: "sample".to_string(),
                tags: vec!["qa".to_string()],
                priority: 10,
                contents: vec![TemplateSeedContent {
                    template_id: "sample".to_string(),
                    lang: "en-US".to_string(),
                    name: "Sample".to_string(),
                    description: Some("Description".to_string()),
                    participant_summary_json: serde_json::json!({
                        "assistant": {
                            "display_name": "Assistant",
                            "required": true
                        }
                    }),
                    definition_json: serde_json::json!({
                        "name": "Sample"
                    }),
                    yaml_text: "name: Sample\n".to_string(),
                    yaml_sha256: "0".repeat(64),
                    version: 1,
                }],
            }],
        }
    }

    #[test]
    fn render_seed_sql_includes_templates_and_preserves_no_definition_id() -> Result<()> {
        let catalog = sample_catalog();

        let sql = render_seed_sql(&catalog, "dev", "tester", SeedSqlOptions::default())?;

        assert!(!sql.contains("START TRANSACTION;"));
        assert!(sql.contains("bcs_collaboration_templates"));
        assert!(sql.contains("'sample'"));
        assert!(sql.contains("'qa'"));
        assert!(!sql.contains("CONVERT(X'"));
        assert!(sql.contains("INSERT IGNORE INTO `bcs_collaboration_template_tags`"));
        assert!(!sql.contains("DELETE FROM `bcs_collaboration_template_tags`"));
        assert!(sql.contains(
            "`version` = IF(`yaml_sha256` <> VALUES(`yaml_sha256`), `version` + 1, `version`)"
        ));
        assert!(!sql.contains("COMMIT;"));
        assert!(!sql.contains("\"id\""));
        assert!(!sql.contains("\"version\""));
        Ok(())
    }

    #[test]
    fn render_seed_sql_can_prune_stale_tags_when_requested() -> Result<()> {
        let catalog = sample_catalog();

        let sql = render_seed_sql(
            &catalog,
            "dev",
            "tester",
            SeedSqlOptions {
                prune_stale_tags: true,
                ..SeedSqlOptions::default()
            },
        )?;

        assert!(sql.contains("DELETE FROM `bcs_collaboration_template_tags`"));
        assert!(sql.contains("INSERT IGNORE INTO `bcs_collaboration_template_tags`"));
        Ok(())
    }

    #[test]
    fn render_seed_sql_can_include_transaction_when_requested() -> Result<()> {
        let catalog = sample_catalog();

        let sql = render_seed_sql(
            &catalog,
            "dev",
            "tester",
            SeedSqlOptions {
                include_transaction: true,
                ..SeedSqlOptions::default()
            },
        )?;

        assert!(sql.starts_with("START TRANSACTION;\n\n"));
        assert!(sql.ends_with("COMMIT;\n"));
        Ok(())
    }

    #[test]
    fn sql_string_escapes_plain_text_literal_content() {
        let value = "line 1\nline '2' \\ \0";

        assert_eq!(
            sql_string(value),
            "'line 1\\nline ''2'' \\\\ \\0'"
        );
    }
}
