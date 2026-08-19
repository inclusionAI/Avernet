/**
 * ThresholdChecker — evaluates analysis results against configured
 * thresholds and produces breach alerts.
 *
 * Used after WorkflowAnalyzer produces an AnalysisResult to determine
 * if any alerts should be triggered.
 */
import type { AnalysisConfig, ThresholdsConfig } from "../config/types.js";
import type { AnalysisResult, ThresholdBreach, HealthReport } from "./types.js";

export class ThresholdChecker {
  constructor(private config: AnalysisConfig) {}

  /**
   * Check an analysis result against configured thresholds.
   * Returns a full HealthReport with breaches listed.
   */
  check(result: AnalysisResult): HealthReport {
    const breaches: ThresholdBreach[] = [];

    if (!this.config.enabled) {
      return { result, breaches, hasBreaches: false };
    }

    const { thresholds } = this.config;

    // Health score: alert when BELOW threshold
    if (result.healthScore < thresholds.healthScore) {
      breaches.push({
        metric: "healthScore",
        value: result.healthScore,
        threshold: thresholds.healthScore,
        severity: result.healthScore < thresholds.healthScore / 2 ? "critical" : "warning",
        message: `Health score ${result.healthScore} is below threshold ${thresholds.healthScore} ` +
          `(${result.failedNodes} failed, ${result.retriedNodes} retried out of ${result.totalNodes} nodes)`,
      });
    }

    // Tool failure rate: alert when ABOVE threshold
    if (result.toolFailureRate > thresholds.toolFailureRate) {
      breaches.push({
        metric: "toolFailureRate",
        value: result.toolFailureRate,
        threshold: thresholds.toolFailureRate,
        severity: result.toolFailureRate > thresholds.toolFailureRate * 2 ? "critical" : "warning",
        message: `Tool failure rate ${result.toolFailureRate} exceeds threshold ${thresholds.toolFailureRate} ` +
          `(${result.failedNodes} of ${result.totalNodes} nodes failed)`,
      });
    }

    // Incomplete rate: alert when ABOVE threshold
    if (result.incompleteRate > thresholds.incompleteRate) {
      breaches.push({
        metric: "incompleteRate",
        value: result.incompleteRate,
        threshold: thresholds.incompleteRate,
        severity: result.incompleteRate > thresholds.incompleteRate * 2 ? "critical" : "warning",
        message: `Incomplete rate ${result.incompleteRate} exceeds threshold ${thresholds.incompleteRate} ` +
          `(${result.failedNodes + result.retriedNodes} of ${result.totalNodes} nodes incomplete)`,
      });
    }

    return {
      result,
      breaches,
      hasBreaches: breaches.length > 0,
    };
  }

  /**
   * Check if analysis is enabled in config.
   */
  get enabled(): boolean {
    return this.config.enabled;
  }

  /**
   * Update the config (e.g., when hot-reloaded).
   */
  updateConfig(config: AnalysisConfig): void {
    this.config = config;
  }
}