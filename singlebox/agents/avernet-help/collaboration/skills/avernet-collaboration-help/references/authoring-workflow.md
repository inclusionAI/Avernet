# Authoring 提问与交付

仅补齐缺失的目标、逻辑角色、串并行/循环和唯一最终输出；已有信息不重复追问。
按已安装 schema 编写，不凭猜测增加循环；不能满足的业务语义明确说明，不删掉语义来通过校验。

## 修改与版本

指定 `draftId + revision`，get 到新的独立文件；修改后 update 明确 expected-revision。
没有原引用时先确认要修改的资源，不从历史聊天中的 YAML 转抄恢复。冲突不自动覆盖或提高基线。
创建重试保持同 draftId；更新结果未知只核对 n+1；消息重试保持同引用。

## 交付

create/update 直接上传原文件，服务端校验该固定版；读取结构化 stdout 的 saved、phase、draft_id、revision 和 validation，忽略其中的 message。
保存且校验通过后，显式执行 `bcs-cli collaboration draft preview <id> --revision <n>`，使用已确认保存的 ID 和版本。
preview 返回 phase=ready、validation.validation.valid=true 且 message 非空后，仅正常回复这次 preview 的短 message，可附流程说明；不回退使用 create/update 的 message，不手拼 AixUI，不转抄完整 YAML，不使用 CLI 发消息。
无效定义只有在用户明确要诊断预览时才用固定版 preview，并如实说明。
网络/认证失败不等于 YAML 无效；保存成功但后续失败保留版本，使用同版 preview 恢复，不重复保存或伪造成功卡片。
预览不等于建群、绑定或执行授权；只读分享可转发和撤销，YAML 不得包含不应共享的数据。
