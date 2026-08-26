# BCSFuse ACK 部署说明

## 1. 登录构建机并拉代码

```bash
ssh xhunter@30.183.96.56
cd /path/to/Avernet
git checkout dev_mohan_bcsfuse_deploy_aliyun
git pull
```

## 2. 构建并推送镜像

方式 A（推荐，与 xhunter 流程一致）：

```bash
./docker/build-image.sh docker/bcsfuse.Dockerfile \
  --image avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse \
  --tag 20260826

docker push avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse:20260826
```

方式 B（service shortcut，自动推断 Dockerfile 和镜像名）：

```bash
DOCKER_REGISTRY=avernet-registry.cn-beijing.cr.aliyuncs.com/avernet \
DOCKER_TAG=20260826 \
  ./docker/build-image.sh bcsfuse

docker push avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/bcsfuse:20260826
```

> 如果 docker login 已做过，也可以在 build 时加 `--push`：
> `./docker/build-image.sh docker/bcsfuse.Dockerfile --image ... --tag 20260826 --push`

## 3. 生成部署配置

```bash
python docker/generate_deploy_config.py /home/xhunter/avernet-deploy \
  avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse:20260826
```

这会生成两个文件：
- `/home/xhunter/avernet-deploy/bcsfuse.env`
- `/home/xhunter/avernet-deploy/bcsfuse-deployment.yaml`

## 4. 填写 secrets

编辑 `/home/xhunter/avernet-deploy/bcsfuse.env`，把 `REPLACE_WITH_*` 替换成真实值：

- `MYSQL_PASSWORD`
- `EMBEDDING_AUTH_TOKEN`
- `LLM_AUTH_TOKEN`

## 5. 部署

如果 xhunter 机器上有 `./deploy.sh`：

```bash
cd /home/xhunter/avernet-deploy
./deploy.sh bcsfuse avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse:20260826
```

如果没有 `deploy.sh`，直接用 kubectl：

```bash
kubectl apply -f /home/xhunter/avernet-deploy/bcsfuse-deployment.yaml
```

## 6. 验证

```bash
kubectl get pods -l app=bcsfuse
kubectl logs -l app=bcsfuse --tail=100 -f
```

---

## 备用：使用 kube-deploy.sh

```bash
# 先生成 env 文件（同上第 3、4 步）
./docker/kube-deploy.sh \
  --service bcsfuse \
  --image avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse:20260826 \
  --env-file /home/xhunter/avernet-deploy/bcsfuse.env \
  --apply
```
