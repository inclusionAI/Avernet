"""Safe JSON text extraction on SQLite, MySQL and PostgreSQL."""

from sqlalchemy import String
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement


class SafeJsonDocument(FunctionElement):
    """Invalid legacy TEXT is a missing document, never a query failure."""

    type = String()
    inherit_cache = True


@compiles(SafeJsonDocument)
def _native_safe_json(element, compiler, **kwargs):
    document = compiler.process(next(iter(element.clauses)), **kwargs)
    return f"CASE WHEN JSON_VALID({document}) = 1 THEN {document} ELSE '{{}}' END"


@compiles(SafeJsonDocument, "postgresql")
def _postgres_safe_json(element, compiler, **kwargs):
    document = compiler.process(next(iter(element.clauses)), **kwargs)
    return f"CAST(ac_expert_try_jsonb({document}) AS TEXT)"


class JsonText(FunctionElement):
    type = String()
    inherit_cache = True


@compiles(JsonText)
def _mysql_json_text(element, compiler, **kwargs):
    args = ", ".join(compiler.process(arg, **kwargs) for arg in element.clauses)
    return f"JSON_UNQUOTE(JSON_EXTRACT({args}))"


@compiles(JsonText, "sqlite")
def _sqlite_json_text(element, compiler, **kwargs):
    args = ", ".join(compiler.process(arg, **kwargs) for arg in element.clauses)
    return f"JSON_EXTRACT({args})"


@compiles(JsonText, "postgresql")
def _postgres_json_text(element, compiler, **kwargs):
    document, path = (compiler.process(arg, **kwargs) for arg in element.clauses)
    # Paths are the internal $.field.child / $[index] vocabulary. Keep path
    # values bound so statement caching cannot reuse another array index.
    components = f"string_to_array(ltrim(replace(replace({path}, '[', '.'), ']', ''), '$.'), '.')"
    return f"(ac_expert_try_jsonb({document}) #>> {components})"
