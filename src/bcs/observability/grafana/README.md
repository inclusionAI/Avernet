# BCS Grafana Dashboards

This directory keeps the maintained BCS Grafana dashboards:

- `bcs-monitoring-dashboard.json`: service and runtime monitoring.
- `bcs-kanban-dashboard.json`: business inventory and current activity.
- `bcs-troubleshooting-dashboard.json`: troubleshooting details for locating issues.
- `bcs-sla-dashboard.json`: portable SLA dashboard with HTTP, message-flow, delivery, and BCS-attributed direct-chat success rates.

The dashboards use a Grafana datasource variable named `datasource`. When importing, choose a Prometheus-compatible datasource.

HTTP API panels exclude `route="/health"` so probe traffic does not inflate
request volume or success-rate calculations. The raw
`bcs_http_requests_total` metric still includes health checks for independent
probe diagnostics. HTTP SLA panels return no value when there is no API
traffic instead of defaulting to 100%.
