"""SPI (Service Provider Interface) for teamclawgw plugins.

Each sub-module declares a ``Protocol`` that both the community bare
implementation and the enterprise SOFA implementation satisfy:

- ``gateway.community.spi.runner``   — ``AppRunnerPlugin``
- ``gateway.community.spi.logger``   — ``LoggerPlugin``
- ``gateway.community.spi.tracer``   — ``TracerPlugin``
- ``gateway.community.spi.cache``    — ``CachePlugin``
- ``gateway.community.spi.auth``     — ``AuthPlugin``
- ``gateway.community.spi.database`` — ``DataSourcePlugin``

Protocols keep the community package free of any ``sofapy_base`` import.
"""
