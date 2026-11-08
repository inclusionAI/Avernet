"""Send scoped compatibility evidence to the configured DingTalk group."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from configure import ENGINES


class DeliveryError(Exception):
    """Only bounded, locally constructed diagnostic codes are allowed here."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def signed_url(webhook, secret, timestamp):
    url = urlsplit(webhook)
    query = dict(parse_qsl(url.query))
    if (url.scheme != 'https' or url.hostname != 'oapi.dingtalk.com'
            or url.port not in (None, 443) or url.path != '/robot/send'
            or url.username or url.password or url.fragment
            or not query.get('access_token')):
        raise DeliveryError('invalid_webhook_configuration')
    if not secret:
        return webhook
    message = f'{timestamp}\n{secret}'.encode()
    query.update(timestamp=str(timestamp), sign=base64.b64encode(
        hmac.new(secret.encode(), message, hashlib.sha256).digest()).decode())
    return urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), ''))


def read_rows(path):
    try:
        rows = json.loads(path.read_text())
        return rows if isinstance(rows, list) and all(isinstance(r, dict) for r in rows) else []
    except (OSError, ValueError):
        return []


def text(value):
    # Reports are scoped metadata, never include runtime logs or provider bodies.
    return str(value).replace('\n', ' ').replace('\r', ' ').replace('`', '')[:150]


def payload(rows, previous, jobs, run_url, attempt, now):
    current = {row.get('engine'): row for row in rows}
    prior = {row.get('engine'): row for row in previous}
    ready = all(current.get(e, {}).get('readiness') == 'READY' for e in ENGINES)
    success = ready and all(jobs.get(j) == 'success' for j in (
        'harness-tests', 'smoke', 'build-bcs', 'e2e', 'readiness'))
    title = 'BCS 引擎兼容性日报 · ' + ('全部通过' if success else '需要关注')
    lines = ['## ' + title, '', now.strftime('%Y-%m-%d %H:%M') + '（北京时间）',
             '', '模型：qwen3.8-flash', '']
    for engine in ENGINES:
        row, old = current.get(engine, {}), prior.get(engine, {})
        version = text(row.get('engine_version', 'unknown'))
        plugin = text(row.get('plugin_version', 'unknown'))
        lines += [f'### {engine}',
                  f'- 引擎：{version}；插件/桥接：{plugin}',
                  f'- BCS/bridge 提交：{text(row.get("commit") or "unknown")}',
                  f'- 版本选择：{text(row.get("selection", "unknown"))}',
                  f'- 模型 smoke：{text(row.get("smoke", "UNKNOWN"))}；BCS E2E：{text(row.get("e2e", "UNKNOWN"))}',
                  f'- 结论：{text(row.get("readiness", "UNKNOWN"))}']
        if row.get('readiness') != 'READY':
            lines.append('- 阻塞阶段：' + text(row.get('reason', 'missing_evidence')))
        if old and row:
            changes = [f'{label} {text(old.get(key, "unknown"))} → {text(row.get(key, "unknown"))}'
                       for key, label in (('engine_version', '引擎'), ('plugin_version', '插件'),
                                          ('commit', 'BCS/bridge 提交'))
                       if old.get(key) != row.get(key)]
            lines.append('- 对比上次定时报告：' + ('；'.join(changes) or '版本未变化'))
        else:
            lines.append('- 对比上次定时报告：无可用基线')
        lines.append('')
    failed = [f'{name}={text(result)}' for name, result in jobs.items() if result != 'success']
    if failed:
        lines += ['未成功任务：' + '；'.join(failed), '']
    lines += [f'[查看 Actions 运行与报告（attempt {text(attempt)}）]({run_url})', '',
              '固定版本通过不代表最新版兼容；E2E 覆盖范围以运行报告为准。']
    return {'msgtype': 'markdown', 'markdown': {'title': title, 'text': '\n'.join(lines)},
            'at': {'isAtAll': False}}


def send(webhook, secret, body):
    request = Request(signed_url(webhook, secret, int(time.time() * 1000)),
                      data=json.dumps(body, ensure_ascii=False).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    # No automatic retry: a timeout may occur after delivery, causing duplicates.
    with build_opener(NoRedirect()).open(request, timeout=20) as response:
        result = json.load(response)
    if not isinstance(result, dict) or result.get('errcode') != 0:
        code = result.get('errcode') if isinstance(result, dict) else None
        code = str(code) if type(code) is int and abs(code) < 1000000000 else 'unknown'
        message = str(result.get('errmsg', '')).lower() if isinstance(result, dict) else ''
        category = next((label for needle, label in (
            ('keyword', 'keyword'), ('sign', 'signature'), ('ip ', 'ip_allowlist'),
            ('token', 'token')) if needle in message), 'rejected')
        raise DeliveryError(f'api_{code}_{category}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--previous', type=Path, required=True)
    args = parser.parse_args()
    try:
        jobs = {name: job['result'] for name, job in json.loads(os.environ['NEEDS_JSON']).items()}
        run_url = (f'{os.environ["GITHUB_SERVER_URL"]}/{os.environ["GITHUB_REPOSITORY"]}'
                   f'/actions/runs/{os.environ["GITHUB_RUN_ID"]}')
        body = payload(read_rows(args.report), read_rows(args.previous), jobs, run_url,
                       os.environ['GITHUB_RUN_ATTEMPT'], datetime.now(timezone(timedelta(hours=8))))
        send(os.environ['BCS_ENGINE_COMPAT_DINGTALK_WEBHOOK'],
             os.environ.get('BCS_ENGINE_COMPAT_DINGTALK_SIGN_SECRET', ''), body)
    except Exception as error:
        # Only locally constructed codes or numeric HTTP status; never remote text/URLs.
        if isinstance(error, DeliveryError):
            category = str(error)
        elif isinstance(error, HTTPError):
            category = f'http_{error.code}'
        elif isinstance(error, (URLError, OSError)):
            category = 'transport_error'
        else:
            category = 'invalid_input_or_response'
        print(f'::error::DingTalk notification failed ({category}). Check BCS_ENGINE_COMPAT_DINGTALK_* Secrets and robot security settings. Delivery is unconfirmed.')
        return 1
    print('DingTalk accepted the compatibility report.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
