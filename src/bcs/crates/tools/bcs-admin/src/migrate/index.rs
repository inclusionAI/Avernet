use super::*;

const MYSQL_UTF8MB4_MAX_INDEX_BYTES: usize = 3072;
const MYSQL_UTF8MB4_BYTES_PER_CHAR: usize = 4;

pub(super) fn validate_mysql_index_lengths(migrations: &[Migration]) -> Result<()> {
    for migration in migrations {
        let mut current_table: Option<(String, Vec<String>)> = None;
        for line in migration.sql.lines() {
            if let Some((table_name, body)) = current_table.as_mut() {
                if line.trim_start().starts_with(") DEFAULT CHARSET = utf8mb4") {
                    validate_mysql_table_index_lengths(migration, table_name, body)?;
                    current_table = None;
                } else {
                    body.push(line.to_string());
                }
                continue;
            }

            if let Some(table_name) = parse_mysql_create_table_name(line) {
                current_table = Some((table_name, Vec::new()));
            }
        }
    }
    Ok(())
}

pub(super) fn validate_mysql_table_index_lengths(
    migration: &Migration,
    table_name: &str,
    body: &[String],
) -> Result<()> {
    let mut column_chars = BTreeMap::new();
    for line in body {
        if let Some((name, chars)) = parse_mysql_char_column(line) {
            column_chars.insert(name, chars);
        }
    }

    for line in body {
        let Some((index_name, parts)) = parse_mysql_named_index(line) else {
            continue;
        };
        let indexed_chars = parts
            .iter()
            .map(|part| {
                part.prefix_chars
                    .or_else(|| column_chars.get(&part.column).copied())
                    .unwrap_or(0)
            })
            .sum::<usize>();
        let indexed_bytes = indexed_chars * MYSQL_UTF8MB4_BYTES_PER_CHAR;
        if indexed_bytes > MYSQL_UTF8MB4_MAX_INDEX_BYTES {
            bail!(
                "mysql migration {:03} ({}) index {}.{} is too long for utf8mb4: {} bytes > {} bytes",
                migration.number,
                migration.name,
                table_name,
                index_name,
                indexed_bytes,
                MYSQL_UTF8MB4_MAX_INDEX_BYTES
            );
        }
    }
    Ok(())
}

pub(super) fn parse_mysql_create_table_name(line: &str) -> Option<String> {
    let rest = line
        .trim_start()
        .strip_prefix("CREATE TABLE IF NOT EXISTS `")?;
    Some(rest.split('`').next()?.to_string())
}

pub(super) fn parse_mysql_char_column(line: &str) -> Option<(String, usize)> {
    let trimmed = line.trim_start();
    let (name, rest) = parse_backtick_ident(trimmed)?;
    let rest = rest.trim_start().to_ascii_lowercase();
    let type_rest = rest
        .strip_prefix("varchar(")
        .or_else(|| rest.strip_prefix("char("))?;
    let length = type_rest.split(')').next()?.parse::<usize>().ok()?;
    Some((name, length))
}

pub(super) fn parse_mysql_named_index(line: &str) -> Option<(String, Vec<MysqlIndexPart>)> {
    let trimmed = line.trim_start();
    let rest = trimmed
        .strip_prefix("UNIQUE KEY `")
        .or_else(|| trimmed.strip_prefix("KEY `"))?;
    let index_name = rest.split('`').next()?.to_string();
    let columns_start = rest.find('(')?;
    let columns_end = rest.rfind(')')?;
    if columns_end <= columns_start {
        return None;
    }
    Some((
        index_name,
        parse_mysql_index_parts(&rest[columns_start + 1..columns_end]),
    ))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct MysqlIndexPart {
    pub(super) column: String,
    pub(super) prefix_chars: Option<usize>,
}

pub(super) fn parse_mysql_index_parts(input: &str) -> Vec<MysqlIndexPart> {
    let mut parts = Vec::new();
    let mut rest = input;
    while let Some(start) = rest.find('`') {
        rest = &rest[start..];
        let Some((column, after_column)) = parse_backtick_ident(rest) else {
            break;
        };
        let after_column = after_column.trim_start();
        let prefix_chars = after_column
            .strip_prefix('(')
            .and_then(|value| value.split(')').next())
            .and_then(|value| value.parse::<usize>().ok());
        parts.push(MysqlIndexPart {
            column,
            prefix_chars,
        });
        rest = after_column;
    }
    parts
}

pub(super) fn parse_backtick_ident(input: &str) -> Option<(String, &str)> {
    let rest = input.strip_prefix('`')?;
    let end = rest.find('`')?;
    Some((rest[..end].to_string(), &rest[end + 1..]))
}
