# Taxonomy Configuration

本目录包含 Fusion Intelligence V2 的统一配置管理。

## 文件说明

| 文件 | 说明 |
|------|------|
| `domains.yaml` | 领域定义配置 |
| `scenarios.yaml` | 场景定义配置 |
| `risk_signals.yaml` | 风险信号配置 |

## 使用方式

配置由 `TaxonomyRegistry` 加载，通过 Feature Flag `ENABLE_TAXONOMY_REGISTRY` 控制是否启用。

当配置文件缺失或格式错误时，系统自动 fallback 到 legacy 硬编码关键词。

## 版本历史

- v1.0 (2026-03-31): 初始版本，从业务代码迁移关键词