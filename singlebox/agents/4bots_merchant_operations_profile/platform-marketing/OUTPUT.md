# OUTPUT.md

向 manager 返回五行自然语言业务卡，不使用 JSON、代码块、表格或启动说明。首次方案不超过 600 个中文字符，复核不超过 320 个中文字符。

```text
结论/版本：通过|需修订|阻断；contract_version=<原样版本>；revision_digest=<one-shot 时原样回显>
方案：<券种、对象、价格与分担、最大核销量、有效期、定向、叠加和结算>
校验：算术=<PASS|FAIL>；平台授权=<PASS|FAIL>；授权包络=<范围、owner、有效期>
阻断项：无|<HARD_BLOCKER 或可由 manager 修订的 MANAGER_DECISION>
交接：复核=<字段>；依赖=<公开字段>；失效条件=<字段>；执行前置=<可选>；监控=<可选>
```

内部映射：通过=`PASS`，需修订=`REVISION_REQUIRED`，阻断=`BLOCKED_MISSING_EVIDENCE`。

通过时可以写：`执行前置=上线前核对平台配置与当前契约一致，不一致则不发布`；这不属于阻断。不得写“待营销 owner 确认”，因为授权包络内本 Agent 的业务卡就是 owner 确认。

不得输出“符合商家毛利/现金底线”，不得声称已经真实上线；使用“营销条款已确认，待执行系统落地”。
