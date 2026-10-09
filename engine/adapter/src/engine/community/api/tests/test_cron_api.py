"""
测试 Cron Web API 接口

测试场景:
1. 验证所有 CRUD 接口与文档一致
2. 测试各种字段的部分更新、整体更新
3. 测试 notify 的部分更新（只更新 enabled 或只更新 user_ids）

运行方式:
    PYTHONPATH=src python -m engine.community.api.tests.test_cron_api

环境变量:
    API_BASE_URL: API 基础地址 (默认: http://localhost:8000)
"""
import asyncio
import logging
import os
import sys
from typing import Optional

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("test-cron-web-api")

# 固定的测试用户ID（合成占位，非真实工号）
ALLOWED_USER_ID = "100000"


class CronWebAPITester:
    """Cron Web API 测试器"""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.test_jobs = []
        self.passed = 0
        self.failed = 0

    async def setup(self):
        """初始化测试环境"""
        import httpx

        log.info("=" * 70)
        log.info("初始化测试环境")
        log.info("=" * 70)

        # 检查服务是否可用
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(f"{self.base_url}/health", timeout=5)
                log.info(f"✓ 服务健康检查: {resp.status_code}")
                data = resp.json()
                log.info(f"  引擎: {data.get('engine', 'unknown')}")
                return data.get('engine')
            except Exception as e:
                log.error(f"✗ 服务不可用: {e}")
                raise

    async def cleanup(self):
        """清理测试环境"""
        import httpx

        log.info("")
        log.info("=" * 70)
        log.info("清理测试环境")
        log.info("=" * 70)

        async with httpx.AsyncClient() as client:
            for job_id in self.test_jobs:
                try:
                    resp = await client.delete(f"{self.base_url}/api/cron/{job_id}")
                    if resp.status_code == 200:
                        log.info(f"✓ 删除测试任务: {job_id}")
                    else:
                        log.warning(f"✗ 删除任务失败 {job_id}: {resp.status_code}")
                except Exception as e:
                    log.warning(f"✗ 删除任务异常 {job_id}: {e}")

    def assert_test(self, name: str, condition: bool, details: str = ""):
        """断言测试结果"""
        if condition:
            self.passed += 1
            log.info(f"✓ {name}")
            if details:
                log.info(f"  {details}")
        else:
            self.failed += 1
            log.error(f"✗ {name}")
            if details:
                log.error(f"  {details}")

    async def test_create_task(self) -> Optional[str]:
        """测试创建任务"""
        import httpx

        log.info("")
        log.info("=" * 70)
        log.info("测试 1: 创建任务")
        log.info("=" * 70)

        async with httpx.AsyncClient() as client:
            # 测试完整创建
            payload = {
                "name": "测试创建任务-完整",
                "schedule": "0 9 * * *",
                "command": "测试命令内容",
                "timezone": "Asia/Shanghai",
                "enabled": True,
                "timeout_secs": 60,
                "model": "custom-antchat-alipay-com::Kimi-K2-Thinking",
            }

            resp = await client.post(f"{self.base_url}/api/cron", json=payload)
            self.assert_test(
                "创建任务 (完整字段)",
                resp.status_code == 200,
                f"status={resp.status_code}"
            )

            if resp.status_code != 200:
                log.error(f"  响应: {resp.text}")
                return None

            data = resp.json().get("data", {})
            job_id = data.get("id")
            self.test_jobs.append(job_id)

            self.assert_test("  返回任务ID", bool(job_id), f"id={job_id}")
            self.assert_test("  名称正确", data.get("name") == payload["name"])
            self.assert_test("  启用状态正确", data.get("enabled") == payload["enabled"])
            self.assert_test("  schedule正确", data.get("schedule", {}).get("expr") == payload["schedule"])
            self.assert_test("  payload.message正确", data.get("payload", {}).get("message") == payload["command"])
            self.assert_test("  timeout_secs正确", data.get("payload", {}).get("timeout_secs") == payload["timeout_secs"])
            self.assert_test("  model正确", data.get("payload", {}).get("model") == payload["model"])

            return job_id

    async def test_get_task(self, job_id: str):
        """测试获取任务"""
        import httpx

        log.info("")
        log.info("=" * 70)
        log.info("测试 2: 获取任务")
        log.info("=" * 70)

        async with httpx.AsyncClient() as client:
            # 测试获取存在的任务
            resp = await client.get(f"{self.base_url}/api/cron/{job_id}")
            self.assert_test("获取存在的任务", resp.status_code == 200)

            if resp.status_code == 200:
                data = resp.json().get("data", {})
                self.assert_test("  返回正确ID", data.get("id") == job_id)

            # 测试获取不存在的任务
            resp = await client.get(f"{self.base_url}/api/cron/non-existent-id")
            self.assert_test("获取不存在的任务返回 404", resp.status_code == 404)

    async def test_list_tasks(self):
        """测试获取任务列表"""
        import httpx

        log.info("")
        log.info("=" * 70)
        log.info("测试 3: 获取任务列表")
        log.info("=" * 70)

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/api/cron")
            self.assert_test("获取任务列表", resp.status_code == 200)

            if resp.status_code == 200:
                data = resp.json()
                self.assert_test("  包含 data 字段", "data" in data)
                self.assert_test("  包含 total 字段", "total" in data)
                self.assert_test("  total 为整数", isinstance(data.get("total"), int))

    async def test_update_task_partial(self, job_id: str):
        """测试部分更新任务"""
        import httpx

        log.info("")
        log.info("=" * 70)
        log.info("测试 4: 部分更新任务")
        log.info("=" * 70)

        async with httpx.AsyncClient() as client:
            # 4.1 只更新名称
            resp = await client.put(
                f"{self.base_url}/api/cron/{job_id}",
                json={"name": "测试任务-已更新名称"}
            )
            self.assert_test("只更新 name", resp.status_code == 200)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                self.assert_test("  名称已更新", data.get("name") == "测试任务-已更新名称")
                self.assert_test("  其他字段保留", data.get("enabled"))

            # 4.2 只更新 enabled
            resp = await client.put(
                f"{self.base_url}/api/cron/{job_id}",
                json={"enabled": False}
            )
            self.assert_test("只更新 enabled", resp.status_code == 200)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                self.assert_test("  enabled 已更新", not data.get("enabled"))
                self.assert_test("  名称保留", data.get("name") == "测试任务-已更新名称")

            # 4.3 只更新 command
            resp = await client.put(
                f"{self.base_url}/api/cron/{job_id}",
                json={"command": "新的命令内容"}
            )
            self.assert_test("只更新 command", resp.status_code == 200)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                self.assert_test("  command 已更新", data.get("payload", {}).get("message") == "新的命令内容")

            # 4.4 只更新 schedule 和 timezone
            resp = await client.put(
                f"{self.base_url}/api/cron/{job_id}",
                json={"schedule": "0 10 * * *", "timezone": "Asia/Tokyo"}
            )
            self.assert_test("更新 schedule 和 timezone", resp.status_code == 200)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                schedule = data.get("schedule", {})
                self.assert_test("  schedule 已更新", schedule.get("expr") == "0 10 * * *")
                self.assert_test("  timezone 已更新", schedule.get("tz") == "Asia/Tokyo")

            # 4.5 只更新 timeout_secs
            resp = await client.put(
                f"{self.base_url}/api/cron/{job_id}",
                json={"timeout_secs": 120}
            )
            self.assert_test("只更新 timeout_secs", resp.status_code == 200)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                self.assert_test("  timeout_secs 已更新", data.get("payload", {}).get("timeout_secs") == 120)

            # 4.6 只更新 model
            resp = await client.put(
                f"{self.base_url}/api/cron/{job_id}",
                json={"model": "custom-antchat-alipay-com_anyuan::MiniMax-M2.5"}
            )
            self.assert_test("只更新 model", resp.status_code == 200)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                self.assert_test("  model 已更新", data.get("payload", {}).get("model") == "custom-antchat-alipay-com_anyuan::MiniMax-M2.5")

    async def test_update_task_full(self, job_id: str):
        """测试整体更新任务"""
        import httpx

        log.info("")
        log.info("=" * 70)
        log.info("测试 5: 整体更新任务")
        log.info("=" * 70)

        async with httpx.AsyncClient() as client:
            payload = {
                "name": "测试任务-整体更新",
                "enabled": True,
                "schedule": "0 12 * * *",
                "timezone": "America/New_York",
                "command": "整体更新的命令",
                "timeout_secs": 90,
                "model": "custom-antchat-alipay-com::Kimi-K2-Thinking",
            }

            resp = await client.put(f"{self.base_url}/api/cron/{job_id}", json=payload)
            self.assert_test("整体更新所有字段", resp.status_code == 200)

            if resp.status_code == 200:
                data = resp.json().get("data", {})
                self.assert_test("  名称正确", data.get("name") == payload["name"])
                self.assert_test("  enabled 正确", data.get("enabled") == payload["enabled"])
                self.assert_test("  schedule 正确", data.get("schedule", {}).get("expr") == payload["schedule"])
                self.assert_test("  timezone 正确", data.get("schedule", {}).get("tz") == payload["timezone"])
                self.assert_test("  command 正确", data.get("payload", {}).get("message") == payload["command"])
                self.assert_test("  timeout_secs 正确", data.get("payload", {}).get("timeout_secs") == payload["timeout_secs"])
                self.assert_test("  model 正确", data.get("payload", {}).get("model") == payload["model"])

    async def test_update_notify(self, job_id: str):
        """测试更新 notify 配置"""
        import httpx

        log.info("")
        log.info("=" * 70)
        log.info("测试 6: 更新 notify 配置")
        log.info("=" * 70)

        user_ids = [ALLOWED_USER_ID]

        async with httpx.AsyncClient() as client:
            # 6.1 启用通知并设置用户
            resp = await client.put(
                f"{self.base_url}/api/cron/{job_id}",
                json={"notify": {"enabled": True, "user_ids": user_ids}}
            )
            self.assert_test("启用 notify 并设置 user_ids", resp.status_code == 200)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                notify = data.get("notify", {})
                self.assert_test("  notify.enabled 正确", notify.get("enabled"))
                self.assert_test("  notify.user_ids 正确", notify.get("user_ids") == user_ids)

            # 6.2 只更新 enabled（部分更新 notify）
            resp = await client.put(
                f"{self.base_url}/api/cron/{job_id}",
                json={"notify": {"enabled": False}}
            )
            self.assert_test("只更新 notify.enabled（禁用通知）", resp.status_code == 200)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                notify = data.get("notify", {})
                self.assert_test("  notify.enabled 已禁用", not notify.get("enabled"))
                # user_ids 应该保留
                self.assert_test("  notify.user_ids 保留", notify.get("user_ids") == user_ids)

            # 6.3 只更新 user_ids（部分更新 notify）
            resp = await client.put(
                f"{self.base_url}/api/cron/{job_id}",
                json={"notify": {"user_ids": user_ids}}
            )
            self.assert_test("只更新 notify.user_ids", resp.status_code == 200)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                notify = data.get("notify", {})
                self.assert_test("  notify.user_ids 已更新", notify.get("user_ids") == user_ids)
                # enabled 应该保持上次的值
                self.assert_test("  notify.enabled 保留", not notify.get("enabled"))

            # 6.4 同时更新 notify 和其他字段
            resp = await client.put(
                f"{self.base_url}/api/cron/{job_id}",
                json={
                    "name": "带通知的任务",
                    "notify": {"enabled": True, "user_ids": user_ids}
                }
            )
            self.assert_test("同时更新 notify 和 name", resp.status_code == 200)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                self.assert_test("  name 已更新", data.get("name") == "带通知的任务")
                notify = data.get("notify", {})
                self.assert_test("  notify.enabled 已启用", notify.get("enabled"))
                self.assert_test("  notify.user_ids 正确", notify.get("user_ids") == user_ids)

    async def test_update_no_fields(self, job_id: str):
        """测试无字段更新"""
        import httpx

        log.info("")
        log.info("=" * 70)
        log.info("测试 7: 无字段更新")
        log.info("=" * 70)

        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{self.base_url}/api/cron/{job_id}",
                json={}
            )
            self.assert_test("空请求体返回 400", resp.status_code == 400)

    async def test_delete_task(self):
        """测试删除任务"""
        import httpx

        log.info("")
        log.info("=" * 70)
        log.info("测试 8: 删除任务")
        log.info("=" * 70)

        # 先创建一个任务来删除
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/api/cron",
                json={
                    "name": "待删除任务",
                    "schedule": "0 9 * * *",
                    "command": "测试命令",
                }
            )

            if resp.status_code != 200:
                self.assert_test("创建待删除任务", False, f"status={resp.status_code}")
                return

            job_id = resp.json().get("data", {}).get("id")
            self.assert_test("创建待删除任务", bool(job_id), f"id={job_id}")

            # 删除任务
            resp = await client.delete(f"{self.base_url}/api/cron/{job_id}")
            self.assert_test("删除任务", resp.status_code == 200)

            # 验证已删除
            resp = await client.get(f"{self.base_url}/api/cron/{job_id}")
            self.assert_test("删除后获取返回 404", resp.status_code == 404)

            # 删除不存在的任务
            resp = await client.delete(f"{self.base_url}/api/cron/non-existent-id")
            self.assert_test("删除不存在的任务返回 404", resp.status_code == 404)

    async def test_cron_status(self):
        """测试获取 cron 状态"""
        import httpx

        log.info("")
        log.info("=" * 70)
        log.info("测试 9: 获取 cron 状态")
        log.info("=" * 70)

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/api/cron/status")
            self.assert_test("获取 cron 状态", resp.status_code == 200)

            if resp.status_code == 200:
                data = resp.json()
                self.assert_test("  包含 success 字段", "success" in data)
                self.assert_test("  包含 data 字段", "data" in data)
                status_data = data.get("data", {})
                self.assert_test("  data.running 是布尔值", isinstance(status_data.get("running"), bool))
                self.assert_test("  data.job_count 是整数", isinstance(status_data.get("job_count"), int))

    async def run_all_tests(self):
        """运行所有测试"""
        await self.setup()

        # 创建测试任务
        job_id = await self.test_create_task()

        if job_id:
            # 依赖 job_id 的测试
            await self.test_get_task(job_id)
            await self.test_list_tasks()
            await self.test_update_task_partial(job_id)
            await self.test_update_task_full(job_id)
            await self.test_update_notify(job_id)
            await self.test_update_no_fields(job_id)

        # 独立测试
        await self.test_delete_task()
        await self.test_cron_status()

        await self.cleanup()

        # 测试总结
        log.info("")
        log.info("=" * 70)
        log.info("测试总结")
        log.info("=" * 70)
        log.info(f"通过: {self.passed}")
        log.info(f"失败: {self.failed}")
        log.info(f"总计: {self.passed + self.failed}")

        if self.failed == 0:
            log.info("🎉 所有测试通过!")
        else:
            log.error(f"⚠️ {self.failed} 个测试失败")

        return self.failed == 0


async def main():
    """主函数"""
    # 获取服务地址
    base_url = os.getenv("API_BASE_URL", "http://localhost:8000")

    tester = CronWebAPITester(base_url=base_url)
    success = await tester.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
