"""Business logging policy for public and injected-host compositions."""

import logging
import os

from src.infra.trace_context import install_trace_record_factory

# These adapters contain legacy INFO-level construction/wire diagnostics.
# Keep them available in DEBUG, but let application stages summarize INFO.
_DETAIL_LOGGERS = (
    "src.interfaces.api.dependencies.fusion_dependencies",
    "src.interfaces.api.dependencies.worker_dependencies",
    "src.infra.public.vectorstores.qdrant_local_vector_store",
    "src.infra.embedding.providers.real_provider",
)


def resolve_business_log_level() -> int:
    """Resolve once at startup; explicit LOG_LEVEL wins over environment defaults."""
    environment = (
        os.getenv("SERVER_ENV")
        or os.getenv("REAL_SERVER_ENV")
        or os.getenv("ALIPAY_APP_ENV")
        or ""
    ).strip().lower()
    default = "DEBUG" if environment in {"pre", "prepub"} else "INFO"
    level_name = os.getenv("LOG_LEVEL", default).strip().upper()
    levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    if level_name not in levels:
        raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
    return levels[level_name]


def configure_business_logging(level: int) -> None:
    """Keep host handlers intact and do not enable third-party DEBUG logging."""
    install_trace_record_factory()
    logging.getLogger("src").setLevel(level)
    for name in _DETAIL_LOGGERS:
        logging.getLogger(name).setLevel(
            logging.DEBUG if level == logging.DEBUG else max(level, logging.WARNING)
        )
    # Wire logs expose URLs and duplicate the application's timing/error logs.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
    # basicConfig is a no-op when the hosting runtime already owns handlers.
    # In particular, do not force/reset SDK formatters, filters or file routing.
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - [%(traceid)s] - %(message)s",
    )
