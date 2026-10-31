//! SQL predicates for Card-owned capability text. Column names are static
//! repository expressions; every search value is a bound parameter.
use bcs_db_api::{DbSqlFlavor, DbValue};

pub(crate) fn card_text_match(flavor: DbSqlFlavor, column: &str, query: &str) -> (String, Vec<DbValue>) {
    match flavor {
        DbSqlFlavor::Sqlite => (format!("(INSTR(LOWER(COALESCE(json_extract({column}, '$.name'), '')), ?) > 0 OR \
            INSTR(LOWER(COALESCE(json_extract({column}, '$.description'), '')), ?) > 0 OR \
            EXISTS (SELECT 1 FROM json_each({column}, '$.skills') a2a_skill WHERE \
            INSTR(LOWER(COALESCE(json_extract(a2a_skill.value, '$.id'), '')), ?) > 0 OR \
            INSTR(LOWER(COALESCE(json_extract(a2a_skill.value, '$.name'), '')), ?) > 0 OR \
            INSTR(LOWER(COALESCE(json_extract(a2a_skill.value, '$.description'), '')), ?) > 0 OR \
            EXISTS (SELECT 1 FROM json_each(a2a_skill.value, '$.tags') a2a_tag WHERE INSTR(LOWER(a2a_tag.value), ?) > 0)))"),
            vec![DbValue::from(query.to_lowercase()); 6]),
        DbSqlFlavor::Mysql => (format!("JSON_SEARCH(LOWER({column}), 'one', ?, '~', '$.name', '$.description', \
            '$.skills[*].id', '$.skills[*].name', '$.skills[*].description', '$.skills[*].tags[*]') IS NOT NULL"),
            vec![DbValue::from(format!("%{}%", escape_mysql(query)))]),
    }
}

pub(crate) fn card_skill_match(flavor: DbSqlFlavor, column: &str, skill: &str) -> (String, Vec<DbValue>) {
    match flavor {
        DbSqlFlavor::Sqlite => (format!("EXISTS (SELECT 1 FROM json_each({column}, '$.skills') a2a_skill WHERE \
            LOWER(json_extract(a2a_skill.value, '$.id')) = ? OR LOWER(json_extract(a2a_skill.value, '$.name')) = ?)"),
            vec![DbValue::from(skill.to_lowercase()); 2]),
        DbSqlFlavor::Mysql => (format!("JSON_SEARCH(LOWER({column}), 'one', ?, '~', '$.skills[*].id', '$.skills[*].name') IS NOT NULL"),
            vec![DbValue::from(escape_mysql(skill))]),
    }
}

fn escape_mysql(value: &str) -> String {
    value.to_lowercase().replace('~', "~~").replace('%', "~%").replace('_', "~_")
}
