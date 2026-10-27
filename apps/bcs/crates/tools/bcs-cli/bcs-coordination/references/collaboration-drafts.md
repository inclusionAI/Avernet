# 协作定义的固定版本存储（按需使用）

仅在需要保存、下载或管理定义版本时使用 Draft；普通 YAML 编写、校验和运行不要求先创建 Draft。
Draft 保存文件原文，不属于聊天 Session，也不创建群、绑定 Bot 或启动 Run。
管理操作沿用当前 Bot 认证；固定版本的读取和校验无需额外凭据，勿保存不应共享的内容。

```bash
# 为本次请求保留一个规范 UUID；创建未知时仍复用它。
draft_id="$(uuidgen | tr '[:upper:]' '[:lower:]')"
bcs collaboration draft create "$candidate_file" --draft-id "$draft_id" --title "协作定义"
bcs collaboration draft get "$draft_id" --revision 1 --output "$new_file"
bcs collaboration draft update "$draft_id" "$new_file" --expected-revision 1
bcs collaboration draft validate "$draft_id" --revision 2
```

CLI 输出为 JSON，按命令读取实际结果：

- `create/update`：上传原文件、核对完整性并校验已存版本。读取 `draft_id`、`revision`、`content_hash`、`size_bytes`、`saved`、`phase` 和 `validation.validation.valid`；保存成功不等于校验通过。
- `get`：原文写入 `--output` 新文件，拒绝覆盖已有文件；stdout 返回版本信息和路径，不包含 YAML。
- `validate`：返回固定版本校验结果，定义结论在 `validation.valid`。
- `preview <id> --revision <n>`：取得固定版校验结果和展示字段，不保存新版本；允许返回无效定义的诊断，因此 `phase=ready` 不等于定义有效。
- `create/update/preview` 成功取得引用后还返回 `reference`、`message` 展示字段，CLI 不发送消息，也不保证当前聊天支持展示。

原文上限 65,536 UTF-8 字节；可选标题须非空白且最多 120 Unicode 码点。下载保留原文，不重新格式化。
修改先 get 指定版本，update 明确 `expected-revision=n`。创建结果未知时复用同一 ID，核对第 1 版；更新未知或冲突只核对 n+1 的原文、hash 和标题，不查 head、不自动提高基线或合并。
保存后校验或引用取得失败仍可能返回 `saved=true` 并退出非零；按 `phase` 报告实际阶段，保留已存版本恢复，不重复创建。

以下动作仅在明确管理请求下执行：

```bash
bcs collaboration draft revoke "$draft_id" --revision 2
bcs collaboration draft delete "$draft_id"
```

`revoke` 撤销该版本的读取，不影响其他版本；不得复制新版绕过撤销。`delete` 删除原文并保留 ID 删除标记，不能复用该 ID 恢复。两者均不能追回已下载内容。
