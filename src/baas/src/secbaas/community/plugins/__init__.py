"""Plugin implementations — concrete realizations of SPI contracts.

Each sub-package mirrors ``spi/`` and provides production (``real/``),
community (``redis/``, ``kms/``) and test (``stub/``) implementations. Selected
and wired in ``bootstrap/`` composition roots via validated configuration.

Notable community options:

- ``plugins.cache = redis``   — ``plugins/cache/redis`` RedisCachePlugin (shared,
  persistent, server-side TTL).
- ``plugins.secret = aliyun_kms`` — ``plugins/secret/kms``
  ``AliyunKmsSecretStorePlugin`` (managed secret store on Alibaba Cloud).
"""
