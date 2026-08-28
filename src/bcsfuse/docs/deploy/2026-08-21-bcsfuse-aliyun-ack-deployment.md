# BCSFuse Aliyun ACK 部署指南

**版本**: 1.0  
**日期**: 2026-08-21  
**适用范围**: Avernet 内网 / 阿里云 ACK 独立部署 BCSFuse

> 本指南面向把 **bcsfuse** 作为独立服务部署到阿里云 ACK 的场景；不影响现有 singlebox / 本地 macOS 启动路径。

## 1. 部署形态

| 项 | 说明 |
|---|---|
| 服务 | bcsfuse |
| 部署方式 | K8s 镜像化部署，ACK-app 集群 |
| 运行模式 | `BCSFUSE_PROVIDER_MODE=runtime` |
| 端口 | 8765 |
| 数据库 | 阿里云 OB/MySQL（由基础设施统一购买，内网可访问） |
| 向量索引 | 每个 Pod 本地启动嵌入式 Qdrant，MySQL 作为持久化事实源 |
| 密钥 | 优先 K8s Secret + ConfigMap；暂不接 mist |
| 公网访问 | OpenAPI 由 `api-gateway` 代理：`avernet.alipay.com/openapi` |
| 内网访问 | 内部域名建议：`bcsfuse.avernet-inc.com` |

## 2. 镜像构建

在项目根目录执行统一构建脚本：

```bash
./docker/build_image.sh bcsfuse
```

可选环境变量：

```bash
# 指定镜像仓库前缀和标签
DOCKER_REGISTRY=registry.avernet-inc.com/namespace \
DOCKER_TAG=2026-08-21-001 \
  ./docker/build_image.sh bcsfuse
```

构建产物 Dockerfile 为 `docker/bcsfuse.Dockerfile`：

- 基于 `python:3.12-slim`
- 仅选择性地复制 `src/bcsfuse` 的源码
- 默认以非 root 用户运行
- 默认 `BCSFUSE_PROVIDER_MODE=runtime`

## 3. MySQL / OB 接入

BCSFuse 通过标准环境变量读取数据库配置，**无需修改代码**，也**不在仓库中保存真实凭据**。

### 3.1 需要注入的环境变量

```yaml
# 取自 Secret（机密信息）
- name: MYSQL_USER
  valueFrom:
    secretKeyRef:
      name: bcsfuse-db-secret
      key: username
- name: MYSQL_PASSWORD
  valueFrom:
    secretKeyRef:
      name: bcsfuse-db-secret
      key: password

# 取自 ConfigMap（非机密信息）
- name: MYSQL_HOST
  valueFrom:
    configMapKeyRef:
      name: bcsfuse-db-config
      key: host
- name: MYSQL_PORT
  valueFrom:
    configMapKeyRef:
      name: bcsfuse-db-config
      key: port
- name: MYSQL_DATABASE
  valueFrom:
    configMapKeyRef:
      name: bcsfuse-db-config
      key: database
```

### 3.2 建议 ConfigMap 示例

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: bcsfuse-db-config
  namespace: avernet
data:
  host: "ob.avernet-inc.com"        # 内部办公网/VPC 可访问的地址
  port: "3306"
  database: "bcsfuse_prod"
```

### 3.3 建议 Secret 示例

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: bcsfuse-db-secret
  namespace: avernet
type: Opaque
stringData:
  username: "bcsfuse_user"
  password: "<REPLACE_ME>"          # 由 DBA / KMS 提供
```

> 注：后期如需统一接入 mist，可新增一个 secret provider；当前优先保持简单，避免阻塞上线。

## 4. Qdrant 本地索引

| 项 | 配置 |
|---|---|
| 后端 | `VECTOR_BACKEND=qdrant_local`（默认） |
| 本地路径 | `QDRANT_LOCAL_PATH=/app/data/qdrant` |
| 持久化源 | MySQL 中的 `worker_profile_content` 等表 |

### 4.1 多机/Pod 模型

- 每台机器 / 每个 Pod 启动一个本地 Qdrant 实例。
- MySQL 是唯一的中心化持久库。
- Pod 重建时，Qdrant 索引从 MySQL 重新拉取并重建（代码通过 `QdrantMySQLVectorStore.rebuild_from_mysql()` 支持）。

### 4.2 Pod 挂载建议

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bcsfuse
spec:
  template:
    spec:
      containers:
        - name: bcsfuse
          image: bcsfuse:local
          env:
            - name: QDRANT_LOCAL_PATH
              value: "/app/data/qdrant"
          volumeMounts:
            - name: qdrant-data
              mountPath: /app/data/qdrant
      volumes:
        - name: qdrant-data
          emptyDir:
            medium: Memory          # 也可以改用 PVC
```

如索引较大或希望加速重启，可把 `emptyDir` 替换为 PVC；但即使无 PVC，也能从 MySQL 重建。

## 5. LLM / Embedding / Reranker 接入

通过 K8s Secret 注入对应凭据：

```yaml
- name: LLM_BASE_URL
  valueFrom: { secretKeyRef: { name: bcsfuse-llm-secret, key: base_url } }
- name: LLM_AUTH_TOKEN
  valueFrom: { secretKeyRef: { name: bcsfuse-llm-secret, key: auth_token } }
```

关键的维度配置：

```yaml
- name: EMBEDDING_DIMENSION
  value: "4096"        # 与模型输出维度一致，切勿随意改动
```

## 6. 域名与网关路由

### 6.1 公网 OpenAPI

由 `api-gateway` 统一代理：

```text
avernet.alipay.com/openapi  ->  bcsfuse.avernet-inc.com
```

bcsfuse 内部只监听 `0.0.0.0:8765`，不直接暴露公网。

### 6.2 内网域名

```text
bcsfuse.avernet-inc.com
```

通过阿里云 PrivateZone 配置。

## 7. 最小 Deployment 示例

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bcsfuse
  namespace: avernet
spec:
  replicas: 2            # 根据负载调整
  selector:
    matchLabels:
      app: bcsfuse
  template:
    metadata:
      labels:
        app: bcsfuse
    spec:
      containers:
        - name: bcsfuse
          image: bcsfuse:2026-08-21-001
          ports:
            - containerPort: 8765
          envFrom:
            - configMapRef:
                name: bcsfuse-db-config
          env:
            - name: BCSFUSE_PROVIDER_MODE
              value: runtime
            - name: SERVICE_HOST
              value: 0.0.0.0
            - name: SERVICE_PORT
              value: "8765"
            - name: VECTOR_BACKEND
              value: qdrant_local
            - name: QDRANT_LOCAL_PATH
              value: /app/data/qdrant
            - name: MYSQL_USER
              valueFrom:
                secretKeyRef: { name: bcsfuse-db-secret, key: username }
            - name: MYSQL_PASSWORD
              valueFrom:
                secretKeyRef: { name: bcsfuse-db-secret, key: password }
            - name: LLM_BASE_URL
              valueFrom:
                secretKeyRef: { name: bcsfuse-llm-secret, key: base_url }
            - name: LLM_AUTH_TOKEN
              valueFrom:
                secretKeyRef: { name: bcsfuse-llm-secret, key: auth_token }
            - name: EMBEDDING_BASE_URL
              valueFrom:
                secretKeyRef: { name: bcsfuse-embedding-secret, key: base_url }
            - name: EMBEDDING_AUTH_TOKEN
              valueFrom:
                secretKeyRef: { name: bcsfuse-embedding-secret, key: auth_token }
            - name: EMBEDDING_DIMENSION
              value: "4096"
          volumeMounts:
            - name: qdrant-data
              mountPath: /app/data/qdrant
          livenessProbe:
            httpGet:
              path: /health
              port: 8765
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /ready
              port: 8765
            initialDelaySeconds: 5
            periodSeconds: 10
      volumes:
        - name: qdrant-data
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: bcsfuse
  namespace: avernet
spec:
  selector:
    app: bcsfuse
  ports:
    - port: 80
      targetPort: 8765
```

## 8. 本地验证（不影响 singlebox）

### 8.1 仅做镜像构建和 dev_smoke 启动

```bash
# 构建
./docker/build_image.sh bcsfuse

# 启动一个 dev_smoke 容器，不连接真实数据库
podman run --rm -p 8765:8765 \
  -e BCSFUSE_PROVIDER_MODE=dev_smoke \
  -e BCSFUSE_PORT=8765 \
  bcsfuse:local
```

访问：

```bash
curl http://localhost:8765/health
curl http://localhost:8765/ready
```

### 8.2 验证 MySQL 连接

把 DBA 提供的 MySQL 地址和凭据作为环境变量传入：

```bash
podman run --rm -p 8765:8765 \
  -e BCSFUSE_PROVIDER_MODE=runtime \
  -e MYSQL_HOST=<db_host> \
  -e MYSQL_PORT=3306 \
  -e MYSQL_USER=bcsfuse_user \
  -e MYSQL_PASSWORD=<password> \
  -e MYSQL_DATABASE=bcsfuse_oss \
  -e LLM_BASE_URL=<...> \
  -e LLM_AUTH_TOKEN=<...> \
  -e EMBEDDING_BASE_URL=<...> \
  -e EMBEDDING_AUTH_TOKEN=<...> \
  -e EMBEDDING_DIMENSION=4096 \
  bcsfuse:local
```

观察日志无 MySQL 连接错误、健康检查返回 200 即可。

## 9. 注意事项

1. **仓库内不要提交真实地址、密码或 token**。所有真实凭据通过 K8s Secret 在 ACK 注入。
2. **Qdrant 目录不需要跨 Pod 共享**。每个 Pod 独立重建索引，避免分布式写冲突。
3. **singlebox 路径未改动**。根目录的 `docker/`、`Dockerfile.ocb`、`docker-compose.yml` 保持原样，仅在新增文件上扩展。
4. **OpenAPI 只走 api-gateway**。bcsfuse 自身的内部接口使用内网域名，不直接暴露公网。

## 10. 后续可选增强

- 接入阿里云 KMS / mist 作为 Secret 来源。
- 增加 HPA（Horizontal Pod Autoscaler）按 CPU / 内存扩缩容。
- 增加 Prometheus / Grafana 监控指标（已内置 `prometheus-client`）。
- Qdrant 持久化从 `emptyDir` 迁移为 SSD-backed PVC。
