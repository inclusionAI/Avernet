import type { OfficialStageDefinition, StageExtensionMode, StageKey } from "./stage-catalog.js";

export function stageDevelopmentBackground(stage: Pick<OfficialStageDefinition, "stage" | "name">, mode: StageExtensionMode, skillFlow: boolean): string {
  const label = `${stage.name}${mode === "replace" ? "" : mode === "preprocess" ? "前置" : "后置"} Skill`;
  const purpose = skillFlow
    ? "Skill 自进化任务用于改进用户指定的 Skill：结合真实会话发现问题，制定修改方案，并通过评测检查修改效果。下文将被改进的 Skill 称为“目标 Skill”。"
    : "Bot 自进化任务用于改进所选 Bot 的执行表现：结合真实会话发现问题，制定修改方案，并通过评测检查修改效果。";
  const isolation = skillFlow
    ? "平台会读取目标 Skill，创建本次任务的独立副本。任务中的修改在副本上进行，不会直接覆盖原始 Skill。"
    : "本次围绕所选 Bot 的能力进行改进，具体修改范围由任务目标和规划方案确定。";
  const downstream: Record<StageKey, string> = {
    diagnose: "再进入规划和优化", plan: "再按照规划结果进入优化", optimize: "再根据本轮结果决定结束任务或继续下一轮优化",
  };
  const position = mode === "preprocess"
    ? `平台会在默认${stage.name}开始前调用${label}，处理完成后继续执行默认${stage.name}，${downstream[stage.stage]}环节。`
    : mode === "postprocess"
      ? `平台会在默认${stage.name}完成后调用${label}，提供已经生成的结果。你可以补充或修正开放的结果字段；平台合并并校验结果后，${downstream[stage.stage]}。`
      : "";
  const preparation: Record<StageKey, string> = {
    diagnose: skillFlow ? "检查目标 Skill 的说明，在任务允许的范围内补充明确的执行约束，为后续诊断和优化做好准备" : "检查诊断目标和会话来源，为默认诊断做好准备",
    plan: "检查上游诊断案例与改进目标，为默认规划补充明确的约束",
    optimize: "检查规划方案和当前轮次所需的资源，为默认优化做好准备",
  };
  const business: Record<StageKey, string> = {
    diagnose: "真实会话分析、问题识别和成功、失败案例提取",
    plan: "问题分析、优化策略制定和评测要求生成",
    optimize: "改进方案执行、优化效果分析和后续策略调整",
  };
  const purposeOfSkill = mode === "preprocess"
    ? `在${stage.name}开始前完成准备工作`
    : mode === "postprocess"
      ? `在${stage.name}完成后检查并补充或修正结果`
      : stage.stage === "plan" && skillFlow
        ? "分析目标 Skill 并制定改进方案"
        : `提供${stage.name}环节所需的${business[stage.stage]}能力`;
  return [
    purpose,
    "", isolation, "", "任务主要包含三个环节：", "",
    `- 诊断：读取真实会话，找出与${skillFlow ? "目标 Skill " : "Bot 执行表现"}相关的问题和成功、失败案例。`,
    "- 规划：根据诊断结果，确定改进目标、修改方案和评测案例。",
    `- 优化：按照方案${skillFlow ? "修改目标 Skill 的副本" : "改进 Bot 的相关能力"}，通过评测判断效果，决定保留、回退或继续调整。`,
    "",
    mode === "preprocess"
      ? `本次需要你开发一个“${label}”，提供${stage.name}开始前的准备逻辑。你可以${preparation[stage.stage]}。`
      : mode === "postprocess"
        ? `本次需要你开发一个“${label}”，提供${stage.name}完成后的结果检查、补充与修正逻辑。处理范围限于平台开放的结果字段。`
        : `本次需要你开发一个“${label}”，提供${stage.name}环节中的${business[stage.stage]}逻辑，供平台调用。`,
    ...(position ? ["", position] : []),
    "",
    skillFlow
      ? `“目标 Skill”是本次自进化任务要改进的对象；“${label}”是你需要开发的能力，用于${purposeOfSkill}。`
      : `所选 Bot 是本次自进化任务要改进的对象；“${label}”是你需要开发的能力，用于${purposeOfSkill}。`,
  ].join("\n");
}

