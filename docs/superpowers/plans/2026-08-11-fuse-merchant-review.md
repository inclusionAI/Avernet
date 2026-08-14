# Fuse 接入小店天团 Demo 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline execution) for this small, contained change. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改动小店天团主流程的前提下，让 4 个经营 Bot 在启动后默认开启 bcsfuse 画像公开（`fusion_enable`），并补充 profile 与文档，使外部开发者能在 SOP 生成后用右侧浮窗 Fuse 做一次多视角复核。

**架构：** 修改 singlebox bots 启动脚本，在 Bot 全部 ready 后自动调用 bcsfuse worker config API 开启 fusion_enable；通过轻微补充 4 Bot 的 MEMORY.md/TOOLS.md，让 fuse prompt 中的视角差异更明显；新增一篇附录文档说明浮窗入口与示例问题。

**Tech Stack:** bash, singlebox shell scripts, BCSFuse REST API, OpenClaw profile markdown files, docs markdown.

## Global Constraints
- 小店天团主流程（店主 → 店长 → 三平台专家 → 一次性自定义协作 → HumanInput 验收）保持不变。
- 不新增/删除角色，不重建 profile 目录结构。
- Fuse 结论不自动写回群聊，仅作为店主参考浮窗。
- 启动脚本修改必须兼容 `--standalone` 和非 standalone 模式，且不破坏其它 profile。

---

### Task 1: 删除旧版 Fuse Demo 未追踪文件

**Files:**
- Delete: `docs/fuse-demo-tutorial.zh-CN.md`
- Delete: `scripts/fuse_demo_profile/` (entire directory)

**Interfaces:**
- Consumes: Git working tree untracked files.
- Produces: Clean working tree without the standalone 5-bot fuse demo.

- [ ] **Step 1: Remove old files**

  Run:
  ```bash
  rm -f docs/fuse-demo-tutorial.zh-CN.md
  rm -rf scripts/fuse_demo_profile/
  ```

- [ ] **Step 2: Verify clean removal**

  Run:
  ```bash
  git status --short
  ```
  Expected: None of the removed files appear as untracked or tracked.

- [ ] **Step 3: Commit (if desired)**

  Wait for the remaining tasks to complete before committing, or commit this cleanup separately:
  ```bash
  git status --short
  ```

---

### Task 2: singlebox 启动 bots 后自动开启 fusion_enable

**Files:**
- Modify: `scripts/modules/bots.sh`
- Test: Local `./scripts/singlebox.sh --standalone start bots --profile-dir scripts/4bots_merchant_operations_profile`

**Interfaces:**
- Consumes:
  - `bcs_cli` available on PATH.
  - `BCSFUSE_URL` and `BCSFUSE_AUTH_TOKEN` environment variables from singlebox env.
  - Bot UUIDs from BCS registry/list.
- Produces:
  - New helper function(s) in `bots.sh` that list Bot UUIDs for current profile and call PUT `/v1/workers/{bot_uuid}/config` with `{"fusion_enable":true}`.

- [ ] **Step 1: Inspect existing ready-wait logic**

  Find where `bots_dynamic_wait_ready` ends and where `bots_start` returns success. This is the hook point for auto-enabling fusion.

- [ ] **Step 2: Add helper to fetch bot UUIDs**

  Add a function (e.g., `bots_dynamic_list_bot_uuids`) that parses `bcs-cli list` JSON and returns UUIDs for bots defined in the current manifest.

  ```bash
  bots_dynamic_list_bot_uuids() {
    local manifest bot_names
    manifest="$(bots_dynamic_manifest)"
    bot_names="$(jq -r '.bots[].name' "$manifest")"
    if ! command -v bcs-cli >/dev/null 2>&1; then
      log_warn "bcs-cli not found; skipping fusion_enable auto-enable"
      return 0
    fi
    bcs-cli list --json 2>/dev/null | jq -r --arg names "$bot_names" '
      .bots[]? | select(.name as $n | ($names | split("\\n")) | index($n)) | .bot_uuid
    '
  }
  ```

- [ ] **Step 3: Add helper to enable fusion for one bot**

  ```bash
  bots_dynamic_enable_fusion_for_bot() {
    local bot_uuid="$1"
    local bcsfuse_url bcsfuse_token
    bcsfuse_url="${BCSFUSE_URL:-http://127.0.0.1:8765}"
    bcsfuse_token="${BCSFUSE_AUTH_TOKEN:-dev-opencore-token}"

    if [ -z "$bot_uuid" ]; then
      return 0
    fi

    local response status
    response="$(curl -s -w "\\n%{http_code}" -X PUT "${bcsfuse_url}/v1/workers/${bot_uuid}/config" \
      -H "Authorization: Bearer ${bcsfuse_token}" \
      -H "Content-Type: application/json" \
      -d '{"fusion_enable":true}' 2>/dev/null)"
    status="$(echo "$response" | tail -n 1)"

    if [ "$status" = "200" ] || [ "$status" = "204" ]; then
      log_info "bcsfuse fusion enabled for ${bot_uuid}"
    else
      log_warn "Failed to enable fusion for ${bot_uuid} (HTTP ${status}); response: $(echo "$response" | head -n -1)"
    fi
  }
  ```

- [ ] **Step 4: Hook into start success path**

  After `bots_dynamic_wait_ready` succeeds (in `bots_start` or equivalent), call:

  ```bash
  if singlebox_target_enabled bcsfuse 2>/dev/null || [ -n "${BCSFUSE_URL:-}" ]; then
    local uuid
    for uuid in $(bots_dynamic_list_bot_uuids); do
      bots_dynamic_enable_fusion_for_bot "$uuid"
    done
  fi
  ```

  Note: `singlebox_target_enabled` may not exist; guard with a check that bcsfuse is actually reachable and degrade gracefully.

- [ ] **Step 5: Test the change**

  Run:
  ```bash
  ./scripts/singlebox.sh --standalone start bcs_frontend
  ./scripts/singlebox.sh --standalone start bcsfuse
  ./scripts/singlebox.sh --standalone start bots --profile-dir scripts/4bots_merchant_operations_profile
  ```

  Then verify each bot has fusion enabled:
  ```bash
  for uuid in $(./scripts/singlebox.sh --standalone status bots --profile-dir scripts/4bots_merchant_operations_profile | grep bot_uuid | awk '{print $2}'); do
    curl -s "http://127.0.0.1:8765/v1/workers/${uuid}/config" \
      -H "Authorization: Bearer dev-opencore-token" | jq '.fusion_enable'
  done
  ```
  Expected: All return `true`.

---

### Task 3: 补充 4 Bot profile 的 Fuse 提示

**Files:**
- Modify: `scripts/4bots_merchant_operations_profile/merchant-operations/MEMORY.md`
- Modify: `scripts/4bots_merchant_operations_profile/merchant-operations/TOOLS.md`
- Modify: `scripts/4bots_merchant_operations_profile/platform-marketing/MEMORY.md`
- Modify: `scripts/4bots_merchant_operations_profile/platform-marketing/TOOLS.md`
- Modify: `scripts/4bots_merchant_operations_profile/platform-data/MEMORY.md`
- Modify: `scripts/4bots_merchant_operations_profile/platform-data/TOOLS.md`
- Modify: `scripts/4bots_merchant_operations_profile/platform-supply-chain/MEMORY.md`
- Modify: `scripts/4bots_merchant_operations_profile/platform-supply-chain/TOOLS.md`

**Interfaces:**
- Consumes: Existing profile markdown files.
- Produces: Slightly augmented markdown that mentions profile fusion behavior.

- [ ] **Step 1: 店长日常运营**

  In `MEMORY.md`, add:
  ```markdown
  - 当收到“融合模式（fuse）”询问时，我会从商家侧统筹视角评估：目标是否完整、owner 是否明确、是否有与店主授权或毛利底线冲突的条款、是否需要回到 HumanInput 修改。
  ```
  In `TOOLS.md`, add:
  ```markdown
  - profile_fuse 复核：在需要时基于当前经营上下文参与多视角融合评估。
  ```

- [ ] **Step 2: 平台营销方案**

  In `MEMORY.md`, add:
  ```markdown
  - 当收到“融合模式（fuse）”询问时，我会从营销活动视角评估：券结构是否支持新客引流与老客转化、补贴与核销规则是否存在合规或亏损风险、宣传边界是否守住了不虚假宣传的底线。
  ```
  In `TOOLS.md`, add:
  ```markdown
  - profile_fuse 复核：基于营销专业知识参与多视角融合评估。
  ```

- [ ] **Step 3: 平台数据分析**

  In `MEMORY.md`, add:
  ```markdown
  - 当收到“融合模式（fuse）”询问时，我会从数据与产能视角评估：客流/转化假设是否有依据、产能与服务时长是否能支撑核销量、指标口径是否一致。
  ```
  In `TOOLS.md`, add:
  ```markdown
  - profile_fuse 复核：基于数据与产能校验能力参与多视角融合评估。
  ```

- [ ] **Step 4: 平台供应链**

  In `MEMORY.md`, add:
  ```markdown
  - 当收到“融合模式（fuse）”询问时，我会从履约与供应视角评估：护理耗材库存是否覆盖活动期、交期和 Plan A/B 是否清晰、品质门槛是否被妥协、最大新增现金占用是否越界。
  ```
  In `TOOLS.md`, add:
  ```markdown
  - profile_fuse 复核：基于库存、交期与品质校验能力参与多视角融合评估。
  ```

- [ ] **Step 5: Spot check**

  Run:
  ```bash
  grep -R "融合模式（fuse）" scripts/4bots_merchant_operations_profile/
  ```
  Expected: 4 hits in MEMORY.md and 4 hits in TOOLS.md.

---

### Task 4: 撰写小店天团 Fuse 复核附录文档

**Files:**
- Create: `docs/fuse-merchant-review.zh-CN.md`
- Optional: Update `scripts/4bots_merchant_operations_profile/README.md` to reference the new doc.

**Interfaces:**
- Consumes: Flow from existing 小店天团下篇文档； BCSFuse API contract.
- Produces: A short standalone tutorial appendix.

- [ ] **Step 1: Write the appendix**

  Content outline:

  1. **这是什么**：在店庆 SOP 进入 HumanInput 前/后，店主可以打开右侧“融合模式”浮窗，让 4 位 Bot 从各自视角再 review 一次方案。
  2. **前置条件**：BCS、前端、bcsfuse、4 Bot 已启动（singlebox 自动开启 fusion_enable 后无需手动 curl）。
  3. **入口**：群聊右下角“融合模式”按钮。
  4. **示例问题**：
     ```text
     当前这份周年庆 SOP 是否已经完整？营销、数据产能、供应履约和商家侧落地性分别有没有明显风险或互相冲突的假设？请给出带条件的 go/no-go 建议。
     ```
  5. **看懂结果**：
     - `perspectives`：4 位 Bot 各自立场。
     - `recommendation.summary`：融合后的综合判断与 checklist。
  6. **使用方式**：店主参考浮窗结论，在 HumanInput 中选择接受或要求修改。
  7. **常见问题**：浮窗不可用 → 确认 bcsfuse 已启动且 singlebox 启动日志里显示 fusion enabled。

- [ ] **Step 2: Validate links**

  Ensure relative links to the main merchant tutorial and to `scripts/4bots_merchant_operations_profile/README.md` are correct.

- [ ] **Step 3: Preview the markdown**

  Run:
  ```bash
  ls -l docs/fuse-merchant-review.zh-CN.md
  head -n 30 docs/fuse-merchant-review.zh-CN.md
  ```

---

## Self-Review

- **Spec coverage:**
  - Auto-enable fusion: Task 2.
  - Preserve main merchant flow: Global Constraints + Task 3/4 are additive only.
  - Documentation: Task 4.
  - Cleanup: Task 1.
- **Placeholder scan:** No TBD/TODO. All code shown.
- **Type consistency:** All HTTP endpoints match BcsfuseController.ts and existing curl examples. UUID extraction uses the same `bot_uuid` field as status output.
