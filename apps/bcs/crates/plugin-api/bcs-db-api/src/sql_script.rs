//! Split SQL migration scripts without treating quoted or commented semicolons as boundaries.
//! This lexer supports the statement forms used by BCS migrations, not stored routines
//! or custom SQL delimiters. It does not validate SQL syntax.

/// Return nonempty SQL statements, preserving comments and quoted content.
pub fn split_sql_statements(sql: &str) -> Vec<String> {
    let mut statements = Vec::new();
    let mut current = String::new();
    let mut chars = sql.chars().peekable();
    let mut state = SqlSplitState::Normal;

    while let Some(ch) = chars.next() {
        match state {
            SqlSplitState::Normal => match ch {
                ';' => push_sql_statement(&mut statements, &mut current),
                '\'' | '"' | '`' => {
                    current.push(ch);
                    state = match ch {
                        '\'' => SqlSplitState::SingleQuote,
                        '"' => SqlSplitState::DoubleQuote,
                        _ => SqlSplitState::Backtick,
                    };
                }
                '-' if chars.peek() == Some(&'-') => {
                    current.push(ch);
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                    state = SqlSplitState::LineComment;
                }
                '#' => {
                    current.push(ch);
                    state = SqlSplitState::LineComment;
                }
                '/' if chars.peek() == Some(&'*') => {
                    current.push(ch);
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                    state = SqlSplitState::BlockComment;
                }
                _ => current.push(ch),
            },
            SqlSplitState::SingleQuote => {
                current.push(ch);
                if ch == '\\' {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                } else if ch == '\'' && chars.peek() != Some(&'\'') {
                    state = SqlSplitState::Normal;
                } else if ch == '\'' {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                }
            }
            SqlSplitState::DoubleQuote => {
                current.push(ch);
                if ch == '\\' {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                } else if ch == '"' && chars.peek() != Some(&'"') {
                    state = SqlSplitState::Normal;
                } else if ch == '"' {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                }
            }
            SqlSplitState::Backtick => {
                current.push(ch);
                if ch == '`' && chars.peek() == Some(&'`') {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                } else if ch == '`' {
                    state = SqlSplitState::Normal;
                }
            }
            SqlSplitState::LineComment => {
                current.push(ch);
                if ch == '\n' {
                    state = SqlSplitState::Normal;
                }
            }
            SqlSplitState::BlockComment => {
                current.push(ch);
                if ch == '*' && chars.peek() == Some(&'/') {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                    state = SqlSplitState::Normal;
                }
            }
        }
    }
    push_sql_statement(&mut statements, &mut current);
    statements
}

fn push_sql_statement(statements: &mut Vec<String>, current: &mut String) {
    if sql_statement_has_code(current) {
        statements.push(current.trim().to_string());
    }
    current.clear();
}

fn sql_statement_has_code(statement: &str) -> bool {
    let mut chars = statement.chars().peekable();
    let mut state = SqlSplitState::Normal;
    while let Some(ch) = chars.next() {
        match state {
            SqlSplitState::Normal => match ch {
                ch if ch.is_whitespace() => {}
                '-' if chars.peek() == Some(&'-') => {
                    let _ = chars.next();
                    state = SqlSplitState::LineComment;
                }
                '#' => state = SqlSplitState::LineComment,
                '/' if chars.peek() == Some(&'*') => {
                    let _ = chars.next();
                    state = SqlSplitState::BlockComment;
                }
                _ => return true,
            },
            SqlSplitState::LineComment => {
                if ch == '\n' {
                    state = SqlSplitState::Normal;
                }
            }
            SqlSplitState::BlockComment => {
                if ch == '*' && chars.peek() == Some(&'/') {
                    let _ = chars.next();
                    state = SqlSplitState::Normal;
                }
            }
            SqlSplitState::SingleQuote | SqlSplitState::DoubleQuote
            | SqlSplitState::Backtick => {
                return true;
            }
        }
    }
    false
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SqlSplitState {
    Normal,
    SingleQuote,
    DoubleQuote,
    Backtick,
    LineComment,
    BlockComment,
}

#[cfg(test)]
mod tests {
    use super::split_sql_statements;

    #[test]
    fn quoted_and_commented_semicolons_are_not_statement_boundaries() {
        let sql = "-- comment;\nSELECT ';', 'it''s;', \"a;\", `b;`; /* ; */ SELECT 2; -- only;";
        let statements = split_sql_statements(sql);
        assert_eq!(statements.len(), 2);
        assert!(statements[0].ends_with("SELECT ';', 'it''s;', \"a;\", `b;`"));
        assert_eq!(statements[1], "/* ; */ SELECT 2");
        assert!(split_sql_statements("-- only;\n/* ; */").is_empty());
    }

    #[test]
    fn published_a2a_migration_keeps_comment_with_first_statement() {
        let sql = include_str!("../../../../migrations/sqlite/039_bot_a2a.sql");
        let statements = split_sql_statements(sql);
        assert_eq!(statements.len(), 9);
        assert!(statements[0].contains("-- New capability only;"));
        assert!(statements[0].ends_with("ALTER TABLE bcs_bots ADD COLUMN agent_card TEXT DEFAULT NULL"));
        assert_eq!(statements.iter().filter(|sql| sql.contains("CREATE TABLE")).count(), 3);
    }
}
