# BCS Grafana Dashboards

This directory keeps the maintained BCS Grafana dashboards:

- `bcs-monitoring-dashboard.json`: service and runtime monitoring.
- `bcs-kanban-dashboard.json`: business inventory and current activity.
- `bcs-troubleshooting-dashboard.json`: troubleshooting details for locating issues.
- `bcs-sla-dashboard.json`: portable SLA dashboard with HTTP, message-flow, delivery, and BCS-attributed direct-chat success rates.

The dashboards use a Grafana datasource variable named `datasource`. When importing, choose a Prometheus-compatible datasource.
