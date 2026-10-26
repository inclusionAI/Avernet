"""SQL JSON text extraction with the same value on SQLite and MySQL."""

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy import String


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
