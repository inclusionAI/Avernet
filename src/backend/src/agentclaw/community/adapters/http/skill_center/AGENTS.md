# Skill Center Legacy BFF

修改本目录前，阅读 [Skill Center 模块维护说明](../../../core/skill_center/AGENTS.md)。
该文件统一定义 OpenAPI、Legacy BFF、领域服务与 Runtime 的职责。

本目录保留 `/api/skills`、`/api/skillsets` 等存量接口。修改时维护原有参数与响应兼容性，复用正式 Query、DirectActivation 和 SkillSetManagement 服务，并运行对应 Legacy endpoint 测试。
