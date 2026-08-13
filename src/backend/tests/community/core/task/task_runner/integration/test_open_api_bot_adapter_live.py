"""OpenApiBotAdapter.send_and_wait live 集成测试(unittest;打真实 BaaS Open API,不经 MockTransport)。

默认跳过:下方配置常量置空占位,手动填入真实 BaaS Open API 配置(API_KEY / BASE_URL / BOT_ID 等)后,
skipUnless 条件满足才会真正执行(unittest 默认不缓冲 stdout,可直接看打印的 run dict)。
"""

import os
import unittest

from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import (
    OpenApiBotAdapter,
)


# ===== live 配置(API_KEY/BASE_URL 支持同名环境变量覆盖;外面未设则用此处填的值作默认)=====
API_KEY = os.environ.get("API_KEY") or "SqbfTxLyfwDG2uoAjYcQ1QPLgCiSQ97G"
BASE_URL = os.environ.get("BASE_URL") or "https://secbaas-prod.alipay.com"
COOKIE = 'session.cookieNameId=ALIPAYBUMNGJSESSIONID; antLoginLang=zh_CN; __TRACERT_COOKIE_bucUserId=35983; receive-cookie-deprecation=1; buservice_domain_id=KOUBEI_SALESCRM; userId=35983; antcode_user_extern_no=35983; cna=WkP4IgG/8C4BASQBsYC5wDYj; isg=BGdnAc_fwQ_QMUWDfF-yXr4e9p0x7DvO7ThfhDnUefYNKITqQbyAHhsrTii29hNG; tfstk=gdCijywKPHi7bpoUZmR_LfHrZ32LACOXjihvDIK4LH-BDEdOHjxcPHU6MOdviS-hchp9_npVmM72lOdv6K8VXMIOmZa6unSVmnBTp7Q15IO42iV8wN6mDAvfnf8auB89uyLN2KTbwIO42kUoxgvMGGLtCJJN8yYvlFlV0E7eYHTy0xS2bpoerUO20iSqL28Jzm823dlU-Ete0IRV0wzHkH-2gISV8fOq8hIVf6r-h-nUSEudTFvM4N-NWNCEGdk15Hcqg6j1n3lDxjlVtF7fEHb-_7xFBt59T1rEVB75pG8HZS0D-Tbe_FjbNfONzwW2Is2xPn6h8TphdVU6-1bliUvEYPxf6GfDC_r-OhWhOsAOplcJcKWd1L1TYfAPFNdO3MzqgnXeug-iLYR84jTUk6kjhd8B-3hk-EtGXEe8-y4nUT9wRFP8-yDjod8B-3U3-YoBQeTaw; isEnableLocale=disabled; LOCALE=zh_CN; acLoginFrom=antcloud_login; nav_original_path=iam.alipay.com; ALIPAYJSESSIONID=GZ00B2B796E277FC4ABEA1735C0CB2D29B1CkujutaGZ00; ALIPAYBUMNGJSESSIONID=GZ00Imbg7LwV3NWarlLQiUDKJs5JWJantbuserviceGZ00; ctoken=dMXVYAUt-uy6c6CM; IAM_TOKEN=eyJraWQiOiJkZWZhdWx0IiwidHlwIjoiSldUIiwiYWxnIjoiUlMyNTYifQ.eyJjbmwiOiJCVUMiLCJzdWIiOiJqaWFuLmppYW5naiIsImF1dGhfdHAiOlsiWkZBUkIiXSwiaXNzIjoiYnVtbmcuYWxpcGF5LmNvbSIsIm5vbmNlIjoiYTczNjlkMiIsInNpZCI6IjEwMDc1MCIsImF1ZCI6IioiLCJuYmYiOjE3ODY1OTI2MjIsInNubyI6IjM1OTgzIiwidG50X2lkIjoiQUxJUFczQ04iLCJuYW1lIjoi6JSj5bu6IiwiZXhwIjoxNzg2Njc5MTQyLCJpYXQiOjE3ODY1OTI3NDIsImp0aSI6IjliNjU1YWE3YWI0ODQwODVhMTFmYTUyOTJkNTA3MTkwIn0.Rrrq8BzJeBNs44bl-oU9XgaplY8QZ9C_BDC62ILXE7UGiMfYY5VnQTj1A3bwSwzTPq25IbM-aEulXURU_iQX4A; JSESSIONID=GZ00Imbg7LwV3NWarlLQiUDKJs5JWJantbuserviceGZ00; authorization=hmac%200000112639-1%3AWU45dXBaYWI0VG1FSll1MFNGbnQ1VmpPZjV0WU0xdFI%3D~0$MYJF; authorization=hmac%200000112639-1%3AWU45dXBaYWI0VG1FSll1MFNGbnQ1VmpPZjV0WU0xdFI%3D~0$MYJF; A3_USER_COOKIE=a90a8adb7c934cf39f501a36c6ee3f08f7589a8ea6ae7e0be5cbbd3ae2eb0f4334472874cbf65e7377011c01346333af0ebbfe5ba2da318d43f2163b6d5596599dd4442afb818f288f0acf94ff62718e4210e77096280ef274643f01d5d3a7b4dd05798c7606f050fab3fa1cb680dd811e434c0dc493fb71563b10b3bf616bfb7b5b3b7abc9bbe956c7af5d1aa23641521ef16e31ce4e8abff1dc5661cae5f9965919410a118c53ba8d6d7cbb786c69aa41da5a72985942cf3fb0998c1ffb4a6a94cf2f3f6e5473ef4f06d955ef65e55402dbab9def1accb66442207610753b0123930fb33669e4e7982d5747e955fcd2f12dbb09a3f0a730cb7902b83b219772bc21022457b4720045ba34178d44253af5c15532e328170f4aeeb0057a871d54b165e69099b7a8ec754751908965d1865c922b90b9c389e06794efe84ef87ab; zone=GZ00F; rtk=B9b4QUc8Oi3G8mXjVj4OFGyjV/MpSoSaHvoGdRZ54AZaL/4vVjv; zt-id-stsq=1786628800.hS2x4aw6.180381222; x-hng=lang=zh-CN; _CHIPS-x-hng=lang=zh-CN; spanner=iNFc6H4zQOX9D0Mip5VgrcnA85Rfso2zXt2T4qEYgj0=; BUSERVICE_SSO_V2=A9926B3F3A65553ACC919027DD0A7F45BEF6417D09B60FCA48C51FB559FF55E83D87023A0725258C1CCB13F1AD1EFED3'
BOT_ID = "20260806_aynoiowe:35983"
MESSAGE = "帮我写一首赞美成都的古诗"

_LIVE_ENABLED = bool(API_KEY and BASE_URL and BOT_ID)


class _LiveKey:
    """真实 ApiKeyProvider(读取上方配置常量;填入后生效)。"""

    api_key = API_KEY
    api_key_prefix = ''
    base_url = BASE_URL
    cookie = COOKIE
    referer = ''


@unittest.skipUnless(
    _LIVE_ENABLED,
    "填入 BaaS Open API 配置(API_KEY / BASE_URL / BOT_ID)后启用 live 测试",
)
class TestSendAndWaitLive(unittest.TestCase):
    def test_returns_terminal_run(self):
        adapter = OpenApiBotAdapter(_LiveKey())  # 真实 httpx.AsyncClient(base_url),非 MockTransport
        run = adapter.send_and_wait(bot_id=BOT_ID, message=MESSAGE, timeout=180.0, poll_interval=5.0)
        # send_and_wait 仅在终态返回(超时抛 OpenApiTimeoutError);此处断言终态并打印回答。
        self.assertIn(run["status"], ("COMPLETED", "FAILED"))
        print(run)  # 查看真实回答 / 错误详情


if __name__ == "__main__":
    unittest.main()
