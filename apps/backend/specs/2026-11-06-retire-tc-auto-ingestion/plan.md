# Plan

Remove observer dependency and calls from the three legacy routes and the two
formal OpenAPI adapter methods. Reuse the unchanged SessionResourceService rather
than add flags, parallel flows, or new queues. Retain shared context/token wiring
and unused publisher contracts for compatibility instead of removing the whole
integration module.

The paired ECB change retires the strict resource-ready envelope at ingress and
removes production consumer wiring. Deploy upstream removal first, then ECB;
old senders receive an explicit 410 rather than false acceptance or transient409.

Regression: legacy route behavior without observer injection; real in-process
OpenAPI HTTP complete/status against READY records with an observer spy proving
zero notifications; session-resource/context/protocol/architecture tests.
Live Singlebox and deployment are separate evidence, not implied by unit passes.
