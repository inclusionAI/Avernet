---
status: accepted
---

# 市场和空间 Skill 跟随最新已发布版本

Bot 对市场或空间 Skill 的控制面绑定持续引用稳定的 Skill 身份，并采用 Track Latest 策略；Skill 升级后，引用 Bot 的草稿态解析结果和草稿容器随之推进，Bot 对该引用内容只读。该决定不向用户提供 Bot 级固定版本或内容分叉语义；Bot 本地技能安装属于另一个领域对象。

Track Latest 不表示已发布运行时动态查询最新版。已经发布的服务 Bot Release 不参与 Skill 升级推送；服务 Bot 下次发布时，才从草稿态生成新的不可变 Artifact，并至少固化当时解析出的 `skill_uuid + external_version_key`。同一 Artifact 的多实例部署、重启和回滚必须继续使用相同版本，Skill 升级不能改写当前或历史 Artifact。
