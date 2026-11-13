"""mlx_lm.server 启动 shim：模型名别名 + 请求级 LoRA 开关 + 可选关思考。

背景：mlx_lm.server 只把请求里的 `model: "default_model"` 映射到启动参数
`--model` / `--adapter-path`；其它字符串会被当成 HF repo/路径重新加载——
不仅 401，还会丢掉 LoRA adapter。本 shim 把任何未知别名重定向到
"default_model"，OpenAI 兼容客户端（OpenClaw / 本项目 infer）就能直接用
"ling-3.0-tiny" 作为 model id。

请求级 LoRA 开关（16G 内存单实例双角色）：单实例同时服务「底座」（OpenClaw
5 玩家）与「LoRA 微调」（本项目玩家），靠 LoRALinear.scale 置零/恢复切换——
scale=0 时输出与底座逐位一致（`y + scale*z`），模型只加载一次、不重载。
请求体带 `"adapters": "<adapter目录>"` → 微调档；不带 → 底座档。
（mlx_lm 的 adapter 键会触发整模型重载，且对 "default_model" 恒被启动参数
覆盖，所以不用它的机制，只借它的字段名。）

用法（等价于 python3 -m mlx_lm.server ...）：
    python3 serve.py --model ~/models/Ling-3.0-tiny-4bit \
        --adapter-path adapters/ --host 127.0.0.1 --port 8080 --trust-remote-code

环境变量：
- MLX_CPU=1  把整个服务放到 CPU 后端（16G 内存双模型共存：CPU 权重可被系统换页，
  Metal 缓冲不行；适合输出极短的玩家 Bot）。
- MLX_THINKING=1  保留模型的 reasoning 模式。默认关闭：Ling-3.0-tiny 是推理模型，
  thinking 开启时裁判一个回合能烧 6 分钟 reasoning，被 OpenClaw 的停滞检测
  中途 abort（content 迟迟不来）。聊天模板支持 enable_thinking，本 shim 对未
  显式传 chat_template_kwargs 的请求统一注入 enable_thinking=False。
"""

import os

from mlx_lm import server as mlx_server

if os.environ.get("MLX_CPU") == "1":
    import mlx.core as mx

    mx.set_default_device(mx.cpu)

# ---- 请求级 LoRA 开关：scale 置零 = 底座，恢复原值 = 微调 ----
_LORA_SCALES = {}  # id(model) -> [(module, 原 scale), ...]


def _set_lora_mode(model, enabled):
    from mlx_lm.tuner.lora import LoRALinear

    key = id(model)
    if key not in _LORA_SCALES:
        _LORA_SCALES[key] = [
            (m, m.scale) for m in model.modules() if isinstance(m, LoRALinear)
        ]
    for m, orig in _LORA_SCALES[key]:
        m.scale = orig if enabled else 0.0


_orig_handle_completion = mlx_server.APIHandler.handle_completion


def _handle_completion_routed(self, request, stop_words):
    # do_POST 已解析 body：self.adapter = body.get("adapters")（见 mlx_lm server）
    _set_lora_mode(self.response_generator.model_provider.model,
                   bool(getattr(self, "adapter", None)))
    if os.environ.get("MLX_THINKING") != "1":
        kwargs = getattr(self, "chat_template_kwargs", None)
        if not isinstance(kwargs, dict):
            kwargs = {}
            self.chat_template_kwargs = kwargs
        kwargs.setdefault("enable_thinking", False)
    return _orig_handle_completion(self, request, stop_words)


mlx_server.APIHandler.handle_completion = _handle_completion_routed

if os.environ.get("MLX_REQ_LOG"):
    import io
    import json as _json
    import time as _time

    _orig_do_POST = mlx_server.APIHandler.do_POST

    def _do_POST_logged(self):
        # body 在原 do_POST 内部才解析；这里先读一遍记录原始请求体，再放回内存流
        content_length = self.headers.get("Content-Length")
        if content_length:
            raw = self.rfile.read(int(content_length))
            try:
                body = _json.loads(raw.decode())
                # 消息正文截到 300 字符，其余字段完整保留，避免日志行超长
                compact = dict(body)
                msgs = []
                for m in body.get("messages", []) or []:
                    m2 = dict(m)
                    c = m2.get("content")
                    if isinstance(c, str) and len(c) > 300:
                        m2["content"] = c[:300] + f"...<+{len(c) - 300} chars>"
                    msgs.append(m2)
                compact["messages"] = msgs
                with open(os.environ["MLX_REQ_LOG"], "a") as f:
                    f.write(_json.dumps(
                        {"ts": _time.time(), "path": self.path, "body": compact},
                        ensure_ascii=False) + "\n")
            except Exception:
                pass
            self.rfile = io.BytesIO(raw)
        return _orig_do_POST(self)

    mlx_server.APIHandler.do_POST = _do_POST_logged

_orig_load = mlx_server.ModelProvider.load


def _load_with_alias(self, model_path, adapter_path=None, draft_model_path=None):
    if model_path not in self._model_map:
        model_path = "default_model"
    out = _orig_load(self, model_path, adapter_path, draft_model_path)
    # 混档请求（底座/LoRA）不能进同一个 batch（scale 是模型级状态），
    # 强制走串行 serve_single；节点任务本就链式派发，吞吐不受影响。
    self.is_batchable = False
    return out


mlx_server.ModelProvider.load = _load_with_alias

if __name__ == "__main__":
    mlx_server.main()
