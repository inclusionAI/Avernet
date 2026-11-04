"""Authorization declarations for the tenant-visible Bot catalog."""

from .models_authorization import Authorization, NoCheck


CATALOG_AUTHORIZATION: dict[tuple[str, str], Authorization] = {
    ("GET", "/openapi/v1/bots/catalog/discover"): NoCheck("tenant-identical catalogue"),
    ("GET", "/openapi/v1/bots/catalog/search"): NoCheck("tenant-identical catalogue"),
    ("GET", "/openapi/v1/bots/catalog/profile/{bot_uuid}"): NoCheck(
        "tenant-identical catalogue: the profile is readable exactly when BCS returns the bot for this bot_uuid (catalog membership is itself the authorization decision; a bot outside the caller catalog 404s)"
    ),
}
