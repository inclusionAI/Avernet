from secbaas.plugins.cache.real import RealCachePlugin

from secbaas.spi.cache import CachePlugin as CachePluginProtocol

# Assign value, will trigger mypy type check
_cache_plugin: CachePluginProtocol = RealCachePlugin()
