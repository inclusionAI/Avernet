"""
Contract Tests for Worker Adapters

这些测试用于验证所有 Adapter 实现都符合 Protocol 定义的契约。

Contract Tests 的核心价值：
1. 所有实现（InMemory / SQLite / PostgreSQL）共享同一套测试
2. 保证协议行为一致性
3. 新增实现时只需运行 contract tests 即可验证基本行为

使用方式：
- 每个 adapter 有对应的 contract test 文件
- 通过 pytest fixture 注入不同的 store_factory
- InMemory 和 SQLite 使用相同的测试用例
"""

__all__ = []