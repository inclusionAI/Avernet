use super::*;
#[async_trait]
impl CollaborationTemplateRepoPort for DbCollaborationTemplateRepo {
    async fn list_entries(&self) -> ServiceResult<Vec<CollaborationTemplateEntry>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT template_id, priority \
                   FROM bcs_collaboration_templates \
                  WHERE env = ? \
                    AND visibility = 'public' \
                    AND record_status = 'active' \
                  ORDER BY priority, template_id",
                vec![DbValue::from(self.env.clone())],
            ))
            .await
            .map_err(|error| db_plugin_error("list templates", error))?;

        let mut entries = Vec::with_capacity(rows.len());
        let mut ids = Vec::with_capacity(rows.len());
        for row in rows {
            let id = db_string(&row, "template_id")?;
            let priority = template_priority_from_row(&row)?;
            ids.push(id.clone());
            entries.push(CollaborationTemplateEntry {
                id,
                tags: Vec::new(),
                priority,
                available_languages: Vec::new(),
            });
        }
        if ids.is_empty() {
            return Ok(entries);
        }

        let tags_by_template = self.tags_for_templates(&ids).await?;
        let languages_by_template = self.languages_for_templates(&ids).await?;
        for entry in &mut entries {
            entry.tags = tags_by_template.get(&entry.id).cloned().unwrap_or_default();
            entry.available_languages = languages_by_template
                .get(&entry.id)
                .cloned()
                .unwrap_or_default();
        }

        Ok(entries)
    }

    async fn get_raw_yaml(&self, id: &str, lang: &str) -> ServiceResult<Option<String>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT c.yaml_text \
                   FROM bcs_collaboration_template_contents c \
                   JOIN bcs_collaboration_templates t \
                     ON t.env = c.env AND t.template_id = c.template_id \
                  WHERE c.env = ? \
                    AND c.template_id = ? \
                    AND c.lang = ? \
                    AND c.record_status = 'active' \
                    AND t.record_status = 'active' \
                    AND t.visibility = 'public' \
                  LIMIT 1",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(id.to_string()),
                    DbValue::from(lang.to_string()),
                ],
            ))
            .await
            .map_err(|error| db_plugin_error("get template YAML", error))?;

        rows.first()
            .map(|row| db_string(row, "yaml_text"))
            .transpose()
    }

    async fn available_languages(&self, id: &str) -> ServiceResult<Vec<String>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT c.lang \
                   FROM bcs_collaboration_template_contents c \
                   JOIN bcs_collaboration_templates t \
                     ON t.env = c.env AND t.template_id = c.template_id \
                  WHERE c.env = ? \
                    AND c.template_id = ? \
                    AND c.record_status = 'active' \
                    AND t.record_status = 'active' \
                    AND t.visibility = 'public' \
                  ORDER BY c.lang",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(id.to_string()),
                ],
            ))
            .await
            .map_err(|error| db_plugin_error("list template languages", error))?;
        collect_string_column(rows, "lang")
    }

    async fn supported_languages(&self) -> ServiceResult<Vec<String>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT DISTINCT c.lang \
                   FROM bcs_collaboration_template_contents c \
                   JOIN bcs_collaboration_templates t \
                     ON t.env = c.env AND t.template_id = c.template_id \
                  WHERE c.env = ? \
                    AND c.record_status = 'active' \
                    AND t.record_status = 'active' \
                    AND t.visibility = 'public' \
                  ORDER BY c.lang",
                vec![DbValue::from(self.env.clone())],
            ))
            .await
            .map_err(|error| db_plugin_error("list supported template languages", error))?;
        collect_string_column(rows, "lang")
    }
}

impl DbCollaborationTemplateRepo {
    async fn tags_for_templates(
        &self,
        ids: &[String],
    ) -> ServiceResult<BTreeMap<String, Vec<String>>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                format!(
                    "SELECT template_id, tag \
                       FROM bcs_collaboration_template_tags \
                      WHERE env = ? AND template_id IN ({}) \
                      ORDER BY template_id, tag",
                    placeholders(ids.len())
                ),
                env_and_ids_params(&self.env, ids),
            ))
            .await
            .map_err(|error| db_plugin_error("list template tags", error))?;

        let mut tags_by_template: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for row in rows {
            let id = db_string(&row, "template_id")?;
            let tag = db_string(&row, "tag")?;
            tags_by_template.entry(id).or_default().push(tag);
        }
        Ok(tags_by_template)
    }

    async fn languages_for_templates(
        &self,
        ids: &[String],
    ) -> ServiceResult<BTreeMap<String, Vec<String>>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                format!(
                    "SELECT template_id, lang \
                       FROM bcs_collaboration_template_contents \
                      WHERE env = ? \
                        AND record_status = 'active' \
                        AND template_id IN ({}) \
                      ORDER BY template_id, lang",
                    placeholders(ids.len())
                ),
                env_and_ids_params(&self.env, ids),
            ))
            .await
            .map_err(|error| db_plugin_error("list template content languages", error))?;

        let mut languages_by_template: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
        for row in rows {
            let id = db_string(&row, "template_id")?;
            let lang = db_string(&row, "lang")?;
            languages_by_template.entry(id).or_default().insert(lang);
        }
        Ok(languages_by_template
            .into_iter()
            .map(|(id, languages)| (id, languages.into_iter().collect()))
            .collect())
    }
}

fn collect_string_column(rows: Vec<DbRow>, column: &str) -> ServiceResult<Vec<String>> {
    rows.into_iter()
        .map(|row| db_string(&row, column))
        .collect()
}

fn template_priority_from_row(row: &DbRow) -> ServiceResult<u32> {
    let priority = db_u64(row, "priority")?;
    u32::try_from(priority).map_err(|error| {
        ServiceError::InternalError(format!(
            "template priority out of range: {priority} ({error})"
        ))
    })
}

fn placeholders(count: usize) -> String {
    std::iter::repeat_n("?", count)
        .collect::<Vec<_>>()
        .join(", ")
}

fn env_and_ids_params(env: &str, ids: &[String]) -> Vec<DbValue> {
    let mut params = Vec::with_capacity(ids.len() + 1);
    params.push(DbValue::from(env.to_string()));
    params.extend(ids.iter().cloned().map(DbValue::from));
    params
}
