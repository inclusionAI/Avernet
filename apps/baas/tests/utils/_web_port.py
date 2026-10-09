"""Web port helper — shared across E2E and unit tests."""

from secbaas.community.config import ConfigLoader


def load_web_port() -> int:
    """Read the web server port from application config (module_config.web.port).

    Raises RuntimeError if the port is not configured — this is a hard requirement
    shared by E2E tests, integration tests, and any code that needs to reach the
    running application.
    """
    config = ConfigLoader.load()
    if not config.module_config.web or not config.module_config.web.port:
        raise RuntimeError(
            "module_config.web.port is not configured in application.yaml — "
            "the web server port is required for tests that contact the running app"
        )
    return config.module_config.web.port
