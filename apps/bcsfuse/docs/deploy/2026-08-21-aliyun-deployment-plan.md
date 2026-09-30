# BCSFuse 阿里云部署任务记录

**日期**：2026-08-21  
**范围**：BCSFuse 独立部署、镜像化、ACK 接入、DB/向量存储接入

## 背景

当前要部署的是 **bcsfuse**，不是 backend，也不是 bcs。它是独立服务，具备自己的运行入口、配置体系、MySQL 持久层和本地 Qdrant 索引层。

用户补充的信息如下：

- bcsfuse 对前端开放的 OpenAPI 接口，走 gateway
- 其他内部接口走内网域名
- `bcs` 指 BCN，不等于 bcsfuse
- DB 接入优先用配置文件 / 环境变量 / Secret，不要求先接 mist
- 多机部署可做到“每台机器一个 Qdrant 实例 + 中心化 MySQL 持久库 + 重建索引”

## 已确认的实现现状

- 启动入口：`src/bcsfuse/main.py`
- 运行模式：`BCSFUSE_PROVIDER_MODE=runtime` 使用 MySQL + qdrant_local + real_http
- MySQL 配置来源：
  - `MYSQL_HOST`
  - `MYSQL_PORT`
  - `MYSQL_DATABASE`
  - `MYSQL_USER`
  - `MYSQL_PASSWORD`
- 向量存储实现：`QdrantMySQLVectorStore`
  - MySQL 是 durable source of truth
  - Qdrant 是 disposable local index
  - 支持 `rebuild_from_mysql()`

## 当前决策

### 域名

- 公网 OpenAPI：`avernet.alipay.com/openapi`，由 `api-gateway` 代理
- 内部接口：走内网域名即可
- bcsfuse 可以单独分配内部域名，例如：`bcsfuse.avernet-inc.com`

### 镜像化

- 根目录 `docker/` 下维护各服务 Dockerfile
- 统一 `docker/build_image.sh` 构建各服务镜像
- bcsfuse 需要独立 Dockerfile，不影响现有 singlebox / backend 启动链路

### 配置来源

- 先使用 ConfigMap + Secret + `configs/application.yaml`
- 暂不要求 bcsfuse 运行时直接接 mist
- 后续若统一密钥体系需要，可以再加 mist-backed provider

### 向量存储

- MySQL 作为中心化持久库
- Qdrant 作为每个实例本地索引
- Pod / 机器重建后从 MySQL 重建 Qdrant 索引
- 第一版优先单副本或每实例单索引模型，避免共享写冲突

## 待执行计划

1. 梳理 bcsfuse 的 ACK 镜像化改造范围
2. 设计 `docker/` 目录下的统一构建入口
3. 明确 bcsfuse 的部署环境变量清单
4. 补充部署文档，说明公网 / 内网域名、MySQL、Qdrant、Secret 的接入方式
5. 如需要，再补一份 Helm / K8s 部署建议

## 备注

- 本记录只保存任务背景和决策，不修改 singlebox 启动逻辑
- 生产凭据、内网地址和密钥不写入仓库
