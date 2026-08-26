# BCSFuse ACK 部署说明

## 构建镜像

```bash
ssh xhunter@30.183.96.56
cd /path/to/Avernet
git checkout dev_mohan_bcsfuse_deploy_aliyun
git pull

./docker/build-image.sh docker/bcsfuse.Dockerfile \
  --image avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse \
  --tag 20260826
```

## 推送镜像

```bash
docker push avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse:20260826
```

## 准备运行时配置

```bash
cp docker/bcsfuse.env.example /home/xhunter/avernet-deploy/bcsfuse.env
# Edit /home/xhunter/avernet-deploy/bcsfuse.env and fill in REPLACE_WITH_* values.
```

## 执行部署

```bash
cd /home/xhunter/avernet-deploy
./deploy.sh bcsfuse avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse:20260826
```

---

Alternative: use the helper script to generate a `bcsfuse-deployment.yaml` directly:

```bash
python docker/generate_deploy_config.py /home/xhunter/avernet-deploy \
  avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/service-bcsfuse:20260826
```
