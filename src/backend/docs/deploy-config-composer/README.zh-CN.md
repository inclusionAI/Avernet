# DeployConfigComposer — 按部署形态拼 create-bot payload

## 背景

`BaasService._build_create_bot_payload` 提交给 BaaS 的 `deploy_config` 里，有
三个字段描述的是**这个部署跑的容器长什么样**，其余字段都与容器形态无关：

| 字段 | 含义 |
| --- | --- |
| `after_create_cmd_hook` | 容器内把 bot 拉起来的那段 shell |
| `mount_points` | 挂进容器的目录 |
| `storage` | 挂给这个 bot 的存储卷（不带该字段 = 不挂） |

托管部署的答案是写死在它的 bot 镜像里的：`/home/admin/bin` 下四个脚本按序
`&&` 串联、三条 NAS 挂载、每个 bot 都带 storage。

ACK/ECI 部署跑的是开源引擎镜像，镜像自带 entrypoint，存储底座也不是 NAS。
它的答案不是"托管那套换个参数"，而是另一套答案——所以这里需要的是**两个实现**，
不是一堆开关。

## 接口

```
core/service_bot/services/deploy/
├── deploy_models.py            # Storage / MountPointEntry（从 baas_service 迁出）
├── deploy_config_composer.py   # BotDeployContext + DeployConfigComposer (ABC)
├── managed_composer.py         # ManagedDeployConfigComposer —— 托管镜像（现状）
└── ack_composer.py             # AckDeployConfigComposer —— ACK/ECI（待实现）
```

```python
class DeployConfigComposer(abc.ABC):
    @property
    def name(self) -> str: ...
    def build_start_command(self, ctx: BotDeployContext) -> str: ...
    def build_mount_points(self, ctx: BotDeployContext) -> list[MountPointEntry]: ...
    def build_storage(self, ctx: BotDeployContext) -> Storage | None: ...
```

三个方法放在同一个接口上，因为它们描述的是同一个容器：分开会让挂载和 storage
有机会各自演化到互相矛盾。

与隔壁 `DeployArtifactProducer` 一样，这是 **core strategy**，不是 plugin：
它只拼字符串和值对象，不跨边界，因此没有 `local`/`prod` 双实现的要求。

## 选择方式

```yaml
user_config:
  baas:
    deploy_runtime: "managed"   # managed | ack
```

在 `ServiceBotModule.deploy_config_composer` 里选一次（Rule 14：实现选择由配置
驱动、只在组装根发生）。**不认识的值直接 `ValueError` 中断启动**，不回退到
`managed`——回退的话，ACK 部署会拿到托管镜像的启动链和 NAS 挂载，Pod 起得来、
健康检查也过，但跑出来的 bot 不能用；启动时报错便宜得多。

## 留在 BaasService 的东西

composer 不需要知道、也不应该知道的部分都留在 `BaasService`：

- `nas_mount` 白名单（解析完的结论放进 `BotDeployContext.mount_home_dir_storage`）
- outbound rule、resource spec、envs / docker_image 覆写、template uuid
- **per-bot 启动脚本（issue #926）**：`BaasService` 把它追加在 composer 返回的
  启动链之后。这样任何部署的 bot 都能跑自己存的脚本，而不用每个 composer 各写
  一遍 `__OCB_RC` 退出码包装。

`mount_home_dir_storage` 由 `BaasService` 提前解析成 `bool` 再交给 composer：
以前挂载和 storage 两侧各自在拿到 `None` 时再查一次白名单，现在一次 payload 只
查一次，两侧也不可能读到不同结论。

## 给 ACK composer 实现者

`AckDeployConfigComposer` 三个方法目前全部 `raise NotImplementedError`——这是
刻意的：返回"看起来合理"的托管值会让 bot 在没人验证过的 runtime 上起来但不工作。
实现时注意：

- **`build_start_command`**：返回一条命令即可，不需要复刻托管镜像那四步。
  可以在返回值里保留 `{token}` / `{client_id}` 字面占位符——BaaS 在下发时用
  `_safe_format_hook` 替换，`client_id` 是 device UUID，backend 拼的时候拿不到。
  **不要**在这里追加 per-bot 启动脚本，`BaasService` 会做。
- **`build_mount_points`**：ACK 上这些会被 BaaS 转成 K8s volume（NAS 或 OSS）。
  注意 OSS 挂载目录文件数建议不超过 1000。
- **`build_storage`**：返回 `None` 就是不挂——`BotDeployConfig.to_dict` 直接不带
  `storage` 字段，这比发一个空的或 0 配额的块更准确。

`BotDeployContext` 带了拼装时刻能拿到的全部信息；托管侧对同样三个问题的答案见
`ManagedDeployConfigComposer`。

## 测试

| 文件 | 覆盖 |
| --- | --- |
| `tests/.../services/test_baas_service_deploy_config_golden.py` | **黄金 payload**：整个 `deploy_config` 逐字节钉死。改动它 = 改了 bot 实际跑的东西 |
| `tests/.../services/deploy/test_deploy_config_composer.py` | 接口契约、BaasService 的委托、ACK 侧未实现的失败方式 |
| `tests/community/di/test_deploy_runtime_selection.py` | `deploy_runtime` 选择与非法值 |
