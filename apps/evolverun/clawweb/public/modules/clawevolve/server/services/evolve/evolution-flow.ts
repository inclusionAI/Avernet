import type { StageKey } from "./stage-catalog.js";

export type EvolutionFlowKey = "bot_evolution" | "skill_evolution" | "skill_hardening";
export type FrozenStageSelection = Record<StageKey, boolean>;
export type RequestedStageSelection = Partial<Record<StageKey, boolean>>;
export type FrozenEvolutionFlow = {
  key: EvolutionFlowKey;
  version: "v1";
  stages: FrozenStageSelection;
};

export type FlowStartInput = {
  taskType: "diagnose" | "full" | "hardening";
  inputMode: string;
  goal: string;
  hasTargetSkill: boolean;
};

export type FlowStageDescription = {
  key: StageKey;
  name: string;
  purpose: string;
  enabled: boolean;
  canDisable: boolean;
  disabledReason?: string;
};

export type EvolutionFlowDescription = {
  key: EvolutionFlowKey;
  name: string;
  purpose: string;
  stages: FlowStageDescription[];
};

export type EvolutionFlow = {
  key: EvolutionFlowKey;
  describe(input: FlowStartInput): EvolutionFlowDescription;
  resolveSelection(input: FlowStartInput, requested: RequestedStageSelection | null | undefined): FrozenStageSelection;
  firstStage(selection: FrozenStageSelection): StageKey;
  nextStage(selection: FrozenStageSelection, completed: StageKey): StageKey | null;
};

const EVOLUTION_ORDER: StageKey[] = ["diagnose", "plan", "optimize"];
const HARDENING_ORDER: StageKey[] = ["hardening"];
const STAGES: Record<StageKey, Pick<FlowStageDescription, "name" | "purpose">> = {
  diagnose: {
    name: "诊断",
    purpose: "从真实 Session 中提取问题、成功案例和失败案例，为规划提供证据。",
  },
  hardening: {
    name: "Skill 加固",
    purpose: "在独立候选中检查并改进目标 Skill，保留业务意图并形成可审阅的新版本。",
  },
  plan: {
    name: "规划",
    purpose: "把诊断证据和进化目标整理为可执行的优化方案与 Bench 输入。",
  },
  optimize: {
    name: "优化",
    purpose: "按规划修改候选内容，通过真实 Bench 比较结果并决定是否继续下一轮。",
  },
};

function firstStage(order: readonly StageKey[], selection: FrozenStageSelection): StageKey {
  const stage = order.find((key) => selection[key]);
  if (!stage) throw new Error("至少需要启用一个 Stage");
  return stage;
}

function nextStage(order: readonly StageKey[], selection: FrozenStageSelection, completed: StageKey): StageKey | null {
  return order.slice(order.indexOf(completed) + 1).find((key) => selection[key]) ?? null;
}

function requestedBoolean(
  requested: RequestedStageSelection | null | undefined,
  key: StageKey,
  fallback: boolean,
): boolean {
  const value = requested?.[key];
  if (value == null) return fallback;
  if (typeof value !== "boolean") throw new Error(`Stage 开关不合法: ${key}`);
  return value;
}

function assertKnownStageKeys(requested: RequestedStageSelection | null | undefined, order: readonly StageKey[]): void {
  if (requested == null) return;
  if (typeof requested !== "object" || Array.isArray(requested)) {
    throw new Error("stageSelection 必须是 JSON 对象");
  }
  for (const [key, value] of Object.entries(requested)) {
    const known = Object.prototype.hasOwnProperty.call(STAGES, key);
    const available = order.includes(key as StageKey);
    if (!known || typeof value !== "boolean" || (!available && value !== false)) {
      throw new Error(`Stage 开关不合法: ${key}`);
    }
  }
}

function canDisableDiagnose(input: FlowStartInput): boolean {
  return input.taskType === "full" && Boolean(input.goal.trim());
}

function canSkipDiagnose(input: FlowStartInput): boolean {
  return canDisableDiagnose(input) && input.inputMode === "direct_goal";
}

function describeStages(
  order: readonly StageKey[],
  selection: FrozenStageSelection,
  canDisable: Partial<Record<StageKey, boolean>>,
  reasons: Partial<Record<StageKey, string>> = {},
): FlowStageDescription[] {
  return order.map((key) => ({
    key,
    ...STAGES[key],
    enabled: selection[key],
    canDisable: canDisable[key] === true,
    ...(canDisable[key] !== true && reasons[key] ? { disabledReason: reasons[key] } : {}),
  }));
}

export const botEvolutionFlow: EvolutionFlow = {
  key: "bot_evolution",
  describe(input) {
    const selection = this.resolveSelection(input, undefined);
    return {
      key: this.key,
      name: input.taskType === "diagnose" ? "Bot 诊断" : "Bot 自进化",
      purpose: input.taskType === "diagnose"
        ? "从 Bot 的真实运行记录中发现问题并形成可追溯的诊断结果。"
        : "从真实运行记录中发现问题、形成方案，并通过多轮 Bench 持续优化 Bot。",
      stages: describeStages(
        EVOLUTION_ORDER,
        selection,
        input.taskType === "diagnose"
          ? { diagnose: false, plan: true, optimize: false }
          : { diagnose: canDisableDiagnose(input), plan: false, optimize: false },
        {
          diagnose: input.taskType === "diagnose" ? "诊断任务必须执行诊断" : "填写直接进化目标后才能关闭诊断",
          plan: "完整自进化必须形成优化方案",
          optimize: input.taskType === "diagnose" ? "诊断任务不运行优化" : "完整自进化必须执行优化",
        },
      ),
    };
  },
  resolveSelection(input, requested) {
    assertKnownStageKeys(requested, EVOLUTION_ORDER);
    if (input.taskType === "diagnose") {
      const selection: FrozenStageSelection = {
        diagnose: requestedBoolean(requested, "diagnose", true),
        hardening: false,
        plan: requestedBoolean(requested, "plan", true),
        optimize: requestedBoolean(requested, "optimize", false),
      };
      if (!selection.diagnose || selection.optimize) {
        throw new Error("诊断任务必须执行 Diagnose，且不能执行 Optimize");
      }
      return selection;
    }
    const selection: FrozenStageSelection = {
      diagnose: requestedBoolean(requested, "diagnose", !canSkipDiagnose(input)),
      hardening: false,
      plan: requestedBoolean(requested, "plan", true),
      optimize: requestedBoolean(requested, "optimize", true),
    };
    if (!selection.diagnose && !canSkipDiagnose(input)) {
      throw new Error("没有直接进化目标时不能关闭诊断");
    }
    if (!selection.plan || !selection.optimize) {
      throw new Error("完整 Bot 自进化必须执行规划和优化");
    }
    return selection;
  },
  firstStage: (selection) => firstStage(EVOLUTION_ORDER, selection),
  nextStage: (selection, completed) => nextStage(EVOLUTION_ORDER, selection, completed),
};

export const skillEvolutionFlow: EvolutionFlow = {
  key: "skill_evolution",
  describe(input) {
    const selection = input.hasTargetSkill
      ? this.resolveSelection(input, undefined)
      : { diagnose: true, hardening: false, plan: true, optimize: true };
    return {
      key: this.key,
      name: "Skill 自进化",
      purpose: "冻结待进化 Skill 的当前内容，在独立候选中完成诊断、规划和优化，确认后发布为下一版本。",
      stages: describeStages(
        EVOLUTION_ORDER,
        selection,
        { diagnose: canDisableDiagnose(input), plan: false, optimize: false },
        {
          diagnose: "填写直接进化目标后才能关闭诊断",
          plan: "Skill 自进化必须形成优化方案",
          optimize: "Skill 自进化必须产出并验证候选版本",
        },
      ),
    };
  },
  resolveSelection(input, requested) {
    assertKnownStageKeys(requested, EVOLUTION_ORDER);
    if (input.taskType !== "full") throw new Error("Skill 自进化只支持完整进化任务");
    if (!input.hasTargetSkill) throw new Error("请选择待进化 Skill");
    const selection: FrozenStageSelection = {
      diagnose: requestedBoolean(requested, "diagnose", !canSkipDiagnose(input)),
      hardening: false,
      plan: requestedBoolean(requested, "plan", true),
      optimize: requestedBoolean(requested, "optimize", true),
    };
    if (!selection.diagnose && !canSkipDiagnose(input)) {
      throw new Error("没有直接进化目标时不能关闭诊断");
    }
    if (!selection.plan || !selection.optimize) {
      throw new Error("Skill 自进化必须执行规划和优化");
    }
    return selection;
  },
  firstStage: (selection) => firstStage(EVOLUTION_ORDER, selection),
  nextStage: (selection, completed) => nextStage(EVOLUTION_ORDER, selection, completed),
};

export const skillHardeningFlow: EvolutionFlow = {
  key: "skill_hardening",
  describe(input) {
    const selection = this.resolveSelection(input, undefined);
    return {
      key: this.key,
      name: "Skill 加固",
      purpose: "在独立候选中运行一次 Skill 加固，确认后发布为下一版本。",
      stages: describeStages(HARDENING_ORDER, selection, { diagnose: false, hardening: false, plan: false, optimize: false }, {
        hardening: "Skill 加固任务必须执行加固 Stage",
      }),
    };
  },
  resolveSelection(input, requested) {
    assertKnownStageKeys(requested, HARDENING_ORDER);
    if (input.taskType !== "hardening") throw new Error("Skill 加固流程只支持加固任务");
    if (!input.hasTargetSkill) throw new Error("请选择待加固 Skill");
    if (["diagnose", "plan", "optimize"].some((stage) => requested?.[stage as StageKey] === true)) {
      throw new Error("Skill 加固任务只执行加固 Stage");
    }
    if (requested?.hardening === false) throw new Error("Skill 加固任务必须执行加固 Stage");
    return { diagnose: false, hardening: true, plan: false, optimize: false };
  },
  firstStage: (selection) => firstStage(HARDENING_ORDER, selection),
  nextStage: (selection, completed) => nextStage(HARDENING_ORDER, selection, completed),
};

export function resolveEvolutionFlow(hasTargetSkill: boolean): EvolutionFlow {
  return hasTargetSkill ? skillEvolutionFlow : botEvolutionFlow;
}

export function freezeEvolutionFlow(
  flow: EvolutionFlow,
  stages: FrozenStageSelection,
): FrozenEvolutionFlow {
  return { key: flow.key, version: "v1", stages: structuredClone(stages) };
}

export function resolveFrozenEvolutionFlow(config: FrozenEvolutionFlow): EvolutionFlow {
  if (config.version !== "v1") throw new Error(`不支持的进化流程版本: ${config.version}`);
  if (config.key === "skill_hardening") return skillHardeningFlow;
  return config.key === "skill_evolution" ? skillEvolutionFlow : botEvolutionFlow;
}
