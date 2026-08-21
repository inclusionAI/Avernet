"""任务 prompt 跨模块共享常量(零依赖,shared kernel)。

teamclaw 运行环境出于安全原因不支持联网搜索:规划/派发/执行/验收的 prompt 一律注入此约束,
强制 bot 仅依据给定上下文与自身知识产出,不调用联网搜索/web_search 工具。
"""

#: 联网搜索禁令(注入到 planning/search/execute/verify prompt 末尾)
NO_WEB_SEARCH_CONSTRAINT = (
    "## 执行约束\n"
    "- 运行环境禁止联网搜索:不要调用任何联网搜索/web_search 工具,也不要尝试访问外部网络;"
    "仅依据上方给定上下文与自身知识完成任务并产出结论。\n"
)
