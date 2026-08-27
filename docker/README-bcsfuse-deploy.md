# BCSFuse ACK 部署说明

## 1. 登录构建机并拉代码

```bash
ssh xhunter@30.183.96.56
cd /path/to/Avernet
git checkout dev_mohan_bcsfuse_deploy_aliyun
git pull
```

## 2. 构建并推送镜像

```bash
./docker/build-image.sh docker/bcsfuse.Dockerfile \
  --image avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse \
  --tag 20260826 \
  --push
```

> 也可以用 service shortcut：
> ```bash
> DOCKER_REGISTRY=avernet-registry.cn-beijing.cr.aliyuncs.com/avernet \
> DOCKER_TAG=20260826 \
>   ./docker/build-image.sh bcsfuse
> docker push avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/bcsfuse:20260826
> ```

## 3. 部署

推荐用 `docker/deploy.sh`：

```bash
cd /home/xhunter/avernet-deploy

# 第一次会生成 bcsfuse.env 模板，填写后再跑一次
/path/to/Avernet/docker/deploy.sh bcsfuse \
  avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse:20260826
```

首次运行会创建 `bcsfuse.env` 并提示你填写 `REPLACE_WITH_*`。填好后再次执行同一命令即可部署。

## 4. 验证

```bash
kubectl get pods -l app=bcsfuse
kubectl logs -l app=bcsfuse --tail=100 -f
```

---

## 备用：手动生成配置

```bash
# 1. 准备 env
cp docker/bcsfuse.env.example /home/xhunter/avernet-deploy/bcsfuse.env
vi /home/xhunter/avernet-deploy/bcsfuse.env

# 2. 生成 yaml
python docker/generate_deploy_config.py \
  --env /home/xhunter/avernet-deploy/bcsfuse.env \
  --no-mask \
  /home/xhunter/avernet-deploy \
  avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse:20260826

# 3. 部署
kubectl apply -f /home/xhunter/avernet-deploy/bcsfuse-deployment.yaml
```
