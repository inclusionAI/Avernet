# 专家模板发现 API

专家目录与运行中的公开 Bot 是独立资源。部署选择受控来源仓库，既有 GitSync
取得一次完整 commit 后扫描 `agents/<expert_id>/expert.yaml`。客户端不能指定
tenant、环境、来源仓库或 Git 版本；查询范围来自已验证 principal 与服务环境。

| 方法与路径 | 请求与响应 |
| --- | --- |
| `GET /openapi/v1/experts` | `keyword` 最长200字符，trim后按名称/说明/单个tag字面子串OR匹配；英文不区分大小写。`category`单选并AND；`sort=hot\|newest`默认hot；`page_num=1`，`page_size=20`且1..100。返回标准 `Envelope[Page[ExpertSummary]]`。 |
| `GET /openapi/v1/experts/categories` | 当前可见有效目录的全部分类字符串数组；不受某页搜索结果限制。 |
| `GET /openapi/v1/experts/{expert_id}` | 返回元信息、声明顺序的身份正文 `identity: [{type,content}]` 和标准Local Skill元信息 `skills: [{name,description}]`。不可见、已下架或未知条目返回标准404。 |

Summary字段为 `expert_id/name/description/category/tags/creation_count/first_added_at`。
接口不返回执行Manifest、完整取源配置、凭证、commit或后台诊断。空结果为成功且
`total=0/items=[]`，身份型专家的`skills=[]`是合法内容。

`hot`使用真实Bot初始来源的历史聚合（包含软删除与后续失败资产）；`newest`使用
首次有效登记时间。两种排序均在完整匹配集上执行后分页，以expert_id、source_repo_id
稳定收敛。目录没有派生Bot时次数为0；更新内容不重置首次登记。

单专家配置/引用无效保留最近有效内容，其他条目继续推进；完整枚举失败不执行
删除判断。完整枚举确认缺失或有效`enabled:false`仅关闭新发现/创建资格，保留目录
行与历史。有效输入、同commit文件引用及`sha256-json-v1`内容摘要持久存储，不依赖
下一轮覆盖的工作目录。数据库增量迁移见 `core/expert_market/sql/`。

本目录浏览切片不增加Bot创建接口。后续创建沿原Manifest/授权/任务合同接入；当前
构建不支持的Manifest扩展会明确拒绝，不删除作者声明以降级通过。
