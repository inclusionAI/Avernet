# 固定版本协作定义与只读分享

Draft 保存协作定义原文。它不属于聊天 Session，不创建群、不绑定 Bot、不启动 Run。
管理身份沿用当前认证 Bot；读取和校验固定版本不需要凭据或自定义请求头。卡片只携带 schemaVersion、draftId、revision，可直接转发。
新服务的引用接口为已部署 CLI 保留固定 `readToken` 占位字段，不落库、不鉴权；旧 CLI 可继续使用，新 CLI 生成卡片时丢弃它。副屏忽略旧卡片的该字段。

```bash
# 为本次请求保留一个规范 UUID；创建未知时仍复用它。
draft_id="$(uuidgen | tr '[:upper:]' '[:lower:]')"
bcs collaboration draft create "$candidate_file" --draft-id "$draft_id" --title "协作定义"
bcs collaboration draft get "$draft_id" --revision 1 --output "$new_file"
bcs collaboration draft update "$draft_id" "$new_file" --expected-revision 1
bcs collaboration draft validate "$draft_id" --revision 2
bcs collaboration draft preview "$draft_id" --revision 2
```

create/update 一次读取 UTF-8 文件，保存、核对 hash/长度/身份、校验已存固定版并取得引用。
stdout 为短 JSON：saved、draft_id、revision、content_hash、size_bytes、phase、validation、
reference、message；原文不进入 stdout。`validation.validation.valid` 是本次定义结论。
正常交付需 phase=ready；工具成功不代表聊天已经发送。
将 message 通过当前聊天正常回复，可附必要说明；不要手拼标签、转抄 YAML 或调用 CLI 发最终消息。

原文上限 65,536 UTF-8 字节；标题省略为无标题，非空白且最多 120 Unicode 码点。
保留所有换行/缩进和字符，不格式化或重新 dump。get 核对固定版并原子写入新路径，拒绝覆盖已有文件。

更新必须明确基线 n。结果不确定或 409 时只核对 n+1 的 hash、原文、标题；匹配仅说明内容已保存。
不同为冲突，无法读取为未知；不改查 head、不提高基线、不自动合并。创建重试核对第 1 版，即使 head 已推进。
保存成功但校验无效/不可用或引用失败时退出非零，保留 saved=true 和失败 phase，无成功 message。
按该版本恢复，不重新创建。preview 不保存；允许显式查看无效定义，但必须如实说明实际诊断。

重发同一消息复用同一引用；重新取得同版 message 用 draft preview，不追加 revision。
以下动作仅在明确管理请求下执行：

```bash
bcs collaboration draft revoke "$draft_id" --revision 2
bcs collaboration draft delete "$draft_id"
```

revoke 永久撤销该版所有副本，不能自动恢复或复制新版绕过；其他版本不受影响。
delete 删除原文并保留 ID 删除标记，迟到创建不能复活。不能追回已下载内容。
共享 Bot 不提供不同私聊用户间的工具侧管理隔离。

沿用现有地址/认证发现，不新增 app_id、用户委托、轮次凭据或聊天 sessionId；
不要读取或打印 Bot token；Bot token 绝不进入消息。
旧 `collaboration preview <file>` 是离线 schema 1 消费兼容，不用于新的帮助工作流。
