# BCSFuse ACK 部署说明

## 构建镜像

```bash
cd /path/to/Avernet
git checkout dev_mohan_bcsfuse_deploy_aliyun
git pull

export DOCKER_REGISTRY=avernet-registry.cn-beijing.cr.aliyuncs.com/avernet
export DOCKER_TAG=20260826
./docker/build_image.sh bcsfuse
```

镜像名：`avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/bcsfuse:20260826`

## 准备运行时配置

把 [`bcsfuse.env.example`](./bcsfuse.env.example) 复制到你的部署目录：

```bash
cp docker/bcsfuse.env.example /home/xhunter/avernet-deploy/bcsfuse.env
```

填入真实值（数据库密码、Token 等）。**不要把真实值提交到 git**。

## Kubernetes 部署

把 [`bcsfuse-deployment.example.yaml`](./bcsfuse-deployment.example.yaml) 复制到部署目录并替换占位符：

```bash
cp docker/bcsfuse-deployment.example.yaml /home/xhunter/avernet-deploy/bcsfuse-deployment.yaml
```

执行部署：

```bash
kubectl apply -f /home/xhunter/avernet-deploy/bcsfuse-deployment.yaml
```

## 安全提示

- 敏感配置必须放在 Kubernetes Secret 里，不要写入镜像或 ConfigMap。
- 生产环境建议使用阿里云 KMS / Opaque Secret + RBAC 限制读取。
- `.env.local` 文件已 `.gitignore`，不要提交到仓库。
