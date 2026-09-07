---
status: accepted
---

# TeamClaw 是 TC 托管 Skill 的写入权威

OCB/TeamClaw 是 Space、Space Membership、Skill Grant、Bot Binding 和 OCB Skill Identity 的事实来源。Skill Center 是外部发布与分发系统，不拥有 TC 托管 Skill 的内容编辑和协作权限。

TC 与 SC 通过显式 Space、Skill、Version 映射以及可对账操作连接。外部调用失败、超时或补偿不会改变领域所有权；SC 中的托管标签、隐藏和写入限制必须共同保证唯一编辑入口，只隐藏页面不等于建立单写权威。

该边界选择以 TC 支持产品化创作、权限和 Bot 跟版，同时继续利用 SC 分发能力；代价是必须建设幂等、补偿、映射和对账，而不能把两个系统当作一个原子数据库。
