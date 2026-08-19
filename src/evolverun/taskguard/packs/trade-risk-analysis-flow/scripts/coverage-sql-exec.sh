#!/usr/bin/bash
set -a  # 自动 export 所有变量，保证 Python 子进程可读取
# coverage-sql-exec.sh — 覆盖分析 SQL 执行脚本（安全版本 + 增强日志）
# 执行 Step 1~4: 参数校验 → 权限检查 → 建样本表 → 关联稽核宽表
# 输出 JSON 给下游 coverage-analysis 节点消费
# ⚠️ 重要：stdout 必须是有效 JSON，所有日志写到 stderr

# ============================================
# 全局变量
# ============================================
DEBUG="${DEBUG:-0}"
_LAST_SQL=""          # 记录最近一次执行的 SQL（错误上报用）
_LAST_QUERY_ID=""     # 记录最近一次 query_id
_LAST_MC_RESPONSE=""  # 记录最近一次 mcporter 原始响应
_LAST_STEP=""         # 记录当前步骤名
_SQL_HISTORY_FILE=""  # 记录所有执行 SQL 的临时 JSON 文件
_EXEC_LOG_FILE=""     # 记录关键执行日志的临时文件（用于 stdout JSON）

# ============================================
# 日志函数（所有日志写 stderr）
# ============================================
log() {
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  local line="[$ts] $*"
  echo "$line" >&2
  # 同时回写到执行日志文件（供 stdout JSON 透出）
  if [ -n "$_EXEC_LOG_FILE" ] && [ -f "$_EXEC_LOG_FILE" ]; then
    echo "$line" >> "$_EXEC_LOG_FILE"
  fi
}

log_debug() {
  if [ "$DEBUG" = "1" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [DEBUG] $*" >&2
  fi
}

log_sql() {
  local step="$1"
  local sql="$2"
  log "📜 [$step] SQL 内容:"
  # 逐行打印 SQL，避免截断
  echo "$sql" | while IFS= read -r line; do
    echo "    $line" >&2
  done
}

# 将执行的 SQL 记录到临时 JSON 文件，最终输出到 stdout
capture_sql_for_output() {
  local step="$1"
  local sql="$2"
  if [ -n "$_SQL_HISTORY_FILE" ] && [ -f "$_SQL_HISTORY_FILE" ]; then
    python3 - "$step" "$sql" "$_SQL_HISTORY_FILE" <<'PY' 2>/dev/null || true
import json, sys
step, sql, path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path, 'r', encoding='utf-8') as f:
        arr = json.load(f)
    arr.append({"step": step, "sql": sql})
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(arr, f, ensure_ascii=False)
except Exception:
    pass
PY
  fi
}

# ============================================
# 全局输出函数（任何路径最终都要调用 success 或 error）
# ============================================
output_success() {
  python3 <<'PY'
import json, os
data = {
    'success': True,
    'permissionChecked': os.environ.get('PERMISSION_OK', '') == 'true',
    'sampleTable': os.environ.get('SAMPLE_TABLE', ''),
    'allinTable': os.environ.get('ALLIN_TABLE', ''),
    'sampleCount': int(os.environ.get('TOTAL_COUNT', '0') or '0'),
    'matchedCount': int(os.environ.get('MATCHED_COUNT', '0') or '0'),
    'riskCount': int(os.environ.get('RISK_COUNT', '0') or '0'),
    'dateStart': os.environ.get('DATE_START', ''),
    'dateEnd': os.environ.get('DATE_END', ''),
    'sceneAbbr': os.environ.get('SCENE_ABBR', ''),
    'executedSqls': [],
    'extractedParams': {
        'uid': os.environ.get('ALIPAY_UID', ''),
        'tradeNo': os.environ.get('TRADE_NO', ''),
        'digitalPoiId': os.environ.get('DIGITAL_POI_ID', ''),
        'sampleScope': os.environ.get('SAMPLE_SCOPE', ''),
        'dateRange': os.environ.get('DATE_RANGE', ''),
        'tables': os.environ.get('TABLES', '')
    },
    'diagnostics': {
        'sampleEmptyWarning': os.environ.get('_SAMPLE_EMPTY_WARNING', '') == 'true',
        'countQueryId': os.environ.get('_COUNT_QUERY_ID', ''),
        'countSql': os.environ.get('_COUNT_SQL', ''),
        'step3Strategy': os.environ.get('_STEP3_STRATEGY', ''),
        'step3Status': int(os.environ.get('_STEP3_STATUS', '0') or '0'),
        'step3Sql': os.environ.get('_STEP3_SQL', ''),
        'step3QueryId': os.environ.get('_STEP3_QUERY_ID', ''),
        'step4Status': int(os.environ.get('_STEP4_STATUS', '0') or '0'),
        'step4Sql': os.environ.get('_STEP4_SQL', ''),
        'step4QueryId': os.environ.get('_STEP4_QUERY_ID', ''),
        'validationWarnings': os.environ.get('_VALIDATE_WARNINGS', ''),
        'sampleSqlRawValue': os.environ.get('_SAMPLE_SCOPE_RAW', ''),
        'sampleSqlInvalid': os.environ.get('_SAMPLE_SCOPE_INVALID', '') == 'true'
    },
    'queryIds': []
}
hist_path = os.environ.get('_SQL_HISTORY_FILE', '')
if hist_path and os.path.exists(hist_path):
    try:
        with open(hist_path, 'r', encoding='utf-8') as f:
            data['executedSqls'] = json.load(f)
    except Exception:
        pass
# 读取执行日志
log_path = os.environ.get('_EXEC_LOG_FILE', '')
if log_path and os.path.exists(log_path):
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            data['executionLogs'] = [ln.rstrip('\n') for ln in f.readlines()]
    except Exception:
        pass
# 读取 query_id 历史
qid_path = os.environ.get('_QUERY_ID_HISTORY_FILE', '')
if qid_path and os.path.exists(qid_path):
    try:
        with open(qid_path, 'r', encoding='utf-8') as f:
            data['queryIds'] = json.load(f)
    except Exception:
        pass
print(json.dumps(data, ensure_ascii=False))
PY
  exit 0
}

output_error() {
  local msg="$1"
  local detail="${2:-}"

  # 组装详细错误上下文
  local ctx=""
  if [ -n "$_LAST_STEP" ];    then ctx="${ctx}步骤: $_LAST_STEP; "; fi
  if [ -n "$_LAST_QUERY_ID" ]; then ctx="${ctx}query_id: $_LAST_QUERY_ID; "; fi
  if [ -n "$_LAST_SQL" ];     then
    # SQL 可能很长，截取前 800 字符
    local sql_snippet
    sql_snippet="$(echo "$_LAST_SQL" | head -c 800)"
    ctx="${ctx}SQL片段: $sql_snippet; "
  fi

  # 如果是 DEBUG 模式，附加 mcporter 完整响应
  if [ "$DEBUG" = "1" ] && [ -n "$_LAST_MC_RESPONSE" ]; then
    local resp_snippet
    resp_snippet="$(echo "$_LAST_MC_RESPONSE" | head -c 1500)"
    ctx="${ctx}MC响应: $resp_snippet; "
  fi

  # 附加校验警告
  if [ -n "$_VALIDATE_WARNINGS" ]; then
    ctx="${ctx}校验警告: $_VALIDATE_WARNINGS; "
  fi

  detail="$detail | 上下文: $ctx"

  # 通过环境变量传递 msg/detail，避免 shell 插值破坏 Python 语法
  export _ERR_MSG="$msg" _ERR_DETAIL="$detail"

  python3 <<'PY'
import json, os
data = {
    'success': False,
    'error': os.environ.get('_ERR_MSG', ''),
    'errorDetail': os.environ.get('_ERR_DETAIL', ''),
    'permissionChecked': os.environ.get('PERMISSION_OK', '') == 'true',
    'sampleTable': os.environ.get('SAMPLE_TABLE', ''),
    'allinTable': os.environ.get('ALLIN_TABLE', ''),
    'sampleCount': int(os.environ.get('SAMPLE_COUNT', '0') or '0'),
    'matchedCount': int(os.environ.get('MATCHED_COUNT', '0') or '0'),
    'riskCount': int(os.environ.get('RISK_COUNT', '0') or '0'),
    'dateStart': os.environ.get('DATE_START', ''),
    'dateEnd': os.environ.get('DATE_END', ''),
    'executedSqls': [],
    'extractedParams': {
        'uid': os.environ.get('ALIPAY_UID', ''),
        'tradeNo': os.environ.get('TRADE_NO', ''),
        'digitalPoiId': os.environ.get('DIGITAL_POI_ID', ''),
        'sampleScope': os.environ.get('SAMPLE_SCOPE', ''),
        'dateRange': os.environ.get('DATE_RANGE', ''),
        'tables': os.environ.get('TABLES', '')
    },
    'diagnostics': {
        'validationWarnings': os.environ.get('_VALIDATE_WARNINGS', ''),
        'sampleSqlRawValue': os.environ.get('_SAMPLE_SCOPE_RAW', ''),
        'sampleSqlInvalid': os.environ.get('_SAMPLE_SCOPE_INVALID', '') == 'true',
        'step3Strategy': os.environ.get('_STEP3_STRATEGY', ''),
        'step3Sql': os.environ.get('_STEP3_SQL', ''),
        'step3QueryId': os.environ.get('_STEP3_QUERY_ID', ''),
        'step4Sql': os.environ.get('_STEP4_SQL', ''),
        'step4QueryId': os.environ.get('_STEP4_QUERY_ID', '')
    },
    'queryIds': []
}
hist_path = os.environ.get('_SQL_HISTORY_FILE', '')
if hist_path and os.path.exists(hist_path):
    try:
        with open(hist_path, 'r', encoding='utf-8') as f:
            data['executedSqls'] = json.load(f)
    except Exception:
        pass
# 读取执行日志
log_path = os.environ.get('_EXEC_LOG_FILE', '')
if log_path and os.path.exists(log_path):
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            data['executionLogs'] = [ln.rstrip('\n') for ln in f.readlines()]
    except Exception:
        pass
# 读取 query_id 历史
qid_path = os.environ.get('_QUERY_ID_HISTORY_FILE', '')
if qid_path and os.path.exists(qid_path):
    try:
        with open(qid_path, 'r', encoding='utf-8') as f:
            data['queryIds'] = json.load(f)
    except Exception:
        pass
print(json.dumps(data, ensure_ascii=False))
PY
  exit 0
}

# ============================================
# 辅助函数：日期格式标准化（确保 YYYYMMDD）
# ============================================
normalize_date() {
  local d="$1"
  # 支持 2026-06-15 或 20260615 → 统一为 20260615
  echo "$d" | python3 -c "import sys; d=sys.stdin.read().strip(); print(d.replace('-',''))" 2>/dev/null || echo "$d"
}

# ============================================
# 辅助函数：场景缩写安全化（MaxCompute 表名仅允许 [a-zA-Z0-9_]）
# 策略: 1) pypinyin 转 pinyin → 2) CJK 转 u4e2d 格式 → 3) strip 非 ASCII → 4) 兜底 "scene"
# ============================================
sanitize_scene_abbr() {
  local raw="$1"
  if [ -z "$raw" ]; then
    echo "scene"
    return
  fi
  python3 - "$raw" <<'PY'
import re, sys, hashlib

def sanitize(raw):
    if not raw:
        return "scene"

    # --- 策略1: 尝试 pypinyin 转 pinyin ---
    try:
        from pypinyin import pinyin, Style
        parts = []
        for ch in raw:
            if '一' <= ch <= '鿿':
                parts.append(pinyin(ch, style=Style.NORMAL)[0])
            else:
                parts.append(ch)
        result = ''.join(parts)
    except Exception:
        # --- 策略2: CJK → unicode hex 前缀, 非 CJK 保持原样 ---
        parts = []
        for ch in raw:
            if ord(ch) > 127:
                parts.append(f'u{ord(ch):x}')
            else:
                parts.append(ch)
        result = ''.join(parts)

    # --- 策略3: 只保留 [a-zA-Z0-9_], 其余替换为 _ ---
    result = re.sub(r'[^a-zA-Z0-9_]', '_', result)
    # 合并连续下划线, 去除首尾下划线
    result = re.sub(r'_+', '_', result).strip('_')

    # --- 策略4: 兜底 ---
    return result if result else "scene"

print(sanitize(sys.argv[1]))
PY
}

# ============================================
# 参数解析
# ============================================
PROJECT_ID="${PROJECT_ID:-410894}"
SCENE_ABBR="${SCENE_ABBR:-scene}"
DATE_START="${DATE_START:-}"
DATE_END="${DATE_END:-}"

# 标准化日期格式（兜底：无论上游传 ISO 还是 YYYYMMDD 都统一）
DATE_START=$(normalize_date "$DATE_START")
DATE_END=$(normalize_date "$DATE_END")

# ============================================
# Step 1a: 如果 DATE_START/DATE_END 为空，从 SAMPLE_SCOPE / USER_INPUT 中推断
# 这是纯机制性兜底，不依赖 LLM：只要 SQL 里有 dt=YYYYMMDD 就能抽出
# ============================================
if [ -z "$DATE_START" ] || [ -z "$DATE_END" ]; then
  log "📋 DATE_START/DATE_END 为空，尝试从 SAMPLE_SCOPE / USER_INPUT 推断..."

  # 优先从 SAMPLE_SCOPE 推断，回退到 USER_INPUT
  _DATE_SOURCE="$SAMPLE_SCOPE"
  if [ -z "$_DATE_SOURCE" ]; then
    _DATE_SOURCE="$USER_INPUT"
  fi

  _DERIVED=$(echo "$_DATE_SOURCE" | python3 -c "
import sys, re
text = sys.stdin.read()
patterns = [
    # dt BETWEEN Y AND Z
    r'(?i)\bdt\s+BETWEEN\s+[\\'\\\"]?(\d{4})-?(\d{2})-?(\d{2})[\\'\\\"]?\s+AND\s+[\\'\\\"]?(\d{4})-?(\d{2})-?(\d{2})[\\'\\\"]?',
    # dt >= Y AND dt <= Z
    r'(?i)\bdt\s*>=\s*[\\'\\\"]?(\d{4})-?(\d{2})-?(\d{2})[\\'\\\"]?\s+AND\s+\bdt\s*<=\s*[\\'\\\"]?(\d{4})-?(\d{2})-?(\d{2})[\\'\\\"]?',
    # dt = YYYYMMDD 或 dt = 'YYYY-MM-DD'
    r'(?i)\bdt\s*=\s*[\\'\\\"]?(\d{4})-?(\d{2})-?(\d{2})[\\'\\\"]?',
]
for pat in patterns:
    m = re.search(pat, text)
    if m:
        g = m.groups()
        if len(g) >= 6:
            # BETWEEN / >= AND <= 模式：起始日期 + 结束日期
            s = f'{g[0]}{g[1]}{g[2]}'
            e = f'{g[3]}{g[4]}{g[5]}'
            print(f'{s}|{e}')
        elif len(g) >= 3:
            # 单日期 = 模式：同一天
            d = f'{g[0]}{g[1]}{g[2]}'
            print(f'{d}|{d}')
        sys.exit(0)
print('')
" 2>/dev/null)

  if [ -n "$_DERIVED" ] && echo "$_DERIVED" | grep -q '|'; then
    _DERIVED_START=$(echo "$_DERIVED" | cut -d'|' -f1)
    _DERIVED_END=$(echo "$_DERIVED" | cut -d'|' -f2)
    # 只覆盖空值，已有值（来自 param-extractor）保留
    if [ -z "$DATE_START" ]; then
      DATE_START="$_DERIVED_START"
      log "✅ 从 SAMPLE_SCOPE 推断 DATE_START=$DATE_START"
    fi
    if [ -z "$DATE_END" ]; then
      DATE_END="$_DERIVED_END"
      log "✅ 从 SAMPLE_SCOPE 推断 DATE_END=$DATE_END"
    fi
  else
    log "⚠️ 无法从 SAMPLE_SCOPE / USER_INPUT 推断日期，将使用空日期（可能导致覆盖分析结果为空）"
  fi
fi

# ⚠️ 关键: SCENE_ABBR 安全化 — 确保表名不包含中文等非 ASCII 字符（MaxCompute 表名仅允许 [a-zA-Z0-9_]）
if echo "$SCENE_ABBR" | grep -qP '[^\x00-\x7F]'; then
  log "⚠️ SCENE_ABBR 含非 ASCII 字符: '$SCENE_ABBR', 执行安全化转换..."
  _RAW_SCENE_ABBR="$SCENE_ABBR"
  SCENE_ABBR=$(sanitize_scene_abbr "$SCENE_ABBR")
  log "✅ SCENE_ABBR 安全化: '$_RAW_SCENE_ABBR' → '$SCENE_ABBR'"
fi

SAMPLE_TABLE="alipaybipub_dev.sc_fzb_scene_trd_${SCENE_ABBR}_${DATE_START}"
ALLIN_TABLE="alipaybipub_dev.sc_fzb_scene_trd_allin_${SCENE_ABBR}_${DATE_START}"

log "=========================================="
log "=== 覆盖分析 SQL 执行开始 ==="
log "=========================================="
log "📥 输入参数:"
log "   场景(sceneAbbr): $SCENE_ABBR"
log "   日期范围: $DATE_START ~ $DATE_END"
log "   样本表: $SAMPLE_TABLE"
log "   宽表: $ALLIN_TABLE"
log "   用户输入(userInput): ${USER_INPUT:-(空)}"
log "   UID: ${ALIPAY_UID:-(空)}"
log "   TradeNo: ${TRADE_NO:-(空)}"
log "   DigitalPoiId: ${DIGITAL_POI_ID:-(空)}"
log "   SampleScope: ${SAMPLE_SCOPE:-(空)}"
log "   DimaSqlQuery: ${DIMA_SAMPLE_SQL_QUERY:-(空)}"
log "   DateRange: ${DATE_RANGE:-(空)}"
log "   Tables: ${TABLES:-(空)}"
log "   RiskType: ${RISK_TYPE:-(空)}"
log "   ProjectId: ${PROJECT_ID}"
log_debug "DEBUG 模式已开启"

# 初始化 SQL 执行历史记录（用于 stdout JSON 输出）
_SQL_HISTORY_FILE=$(mktemp /tmp/coverage_sql_history.XXXXXX.json)
echo '[]' > "$_SQL_HISTORY_FILE"
export _SQL_HISTORY_FILE

# 初始化执行日志文件（用于 stdout JSON 输出 executionLogs）
_EXEC_LOG_FILE=$(mktemp /tmp/coverage_exec_log.XXXXXX.log)
: > "$_EXEC_LOG_FILE"
export _EXEC_LOG_FILE

# 初始化 query_id 历史记录（用于 stdout JSON 输出 queryIds）
_QUERY_ID_HISTORY_FILE=$(mktemp /tmp/coverage_qid_history.XXXXXX.json)
echo '[]' > "$_QUERY_ID_HISTORY_FILE"
export _QUERY_ID_HISTORY_FILE

# 记录原始 SAMPLE_SCOPE 值（供 diagnostics 使用）
export _SAMPLE_SCOPE_RAW="$SAMPLE_SCOPE"
export _VALIDATE_WARNINGS="${_VALIDATE_WARNINGS:-}"

# 处理 TABLES 参数
if [ -n "${TABLES:-}" ]; then
  CLEAN_TABLES=$(echo "$TABLES" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    if isinstance(data, list):
        print(','.join(data))
    else:
        print(data)
except:
    print(sys.stdin.read().strip())
" 2>/dev/null)
  if [ -n "${CLEAN_TABLES:-}" ]; then
    TABLES="$CLEAN_TABLES"
    log "📋 表名列表(已解析): $TABLES"
  fi
fi

# ============================================
# 辅助函数：SQL 执行
# ============================================

# 执行 SQL 提交，返回 query_id
run_sql() {
  local sql="$1"
  _LAST_SQL="$sql"

  local args_json
  args_json=$(python3 -c "
import json, sys, os
project_id = int(os.environ.get('PROJECT_ID', '410894'))
print(json.dumps({'project_id': project_id, 'sql_query': sys.argv[1]}))
" "$sql" 2>/dev/null)

  if [ -z "$args_json" ]; then
    log "❌ 构造 SQL 参数失败"
    echo ""
    return 1
  fi

  log_debug "mcporter args: $args_json"
  log "📤 mcporter 提交 SQL..."

  local result
  result=$(mcporter call mcp.ant.rpc.dpagent.dataprocess.run_sql_query \
    --args "$args_json" \
    --output json 2>&1)
  local mc_exit=$?

  _LAST_MC_RESPONSE="$result"

  if [ $mc_exit -ne 0 ]; then
    log "❌ mcporter 调用失败: exit=$mc_exit"
    log_debug "mcporter 原始输出: $result"
    echo ""
    return 1
  fi

  log_debug "mcporter 原始响应: $result"

  # 提取 query_id
  local query_id
  query_id=$(echo "$result" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    qid = d.get('data', {}).get('queryId', '')
    if not qid:
        qid = d.get('queryId', '')
    print(qid)
except Exception as e:
    print('')
" 2>/dev/null)

  if [ -z "$query_id" ]; then
    log "❌ mcporter 响应中无 query_id"
    log_debug "完整响应: ${result:0:2000}"
    echo ""
    return 1
  fi

  _LAST_QUERY_ID="$query_id"
  log "✅ SQL 提交成功, query_id=$query_id"
  echo "$query_id"
  return 0
}

# 查询 SQL 状态
get_sql_status() {
  local query_id="$1"
  local args_json
  args_json=$(python3 -c "
import json
print(json.dumps({'query_id': '$query_id'}))
" 2>/dev/null)

  local result
  result=$(mcporter call mcp.ant.rpc.dpagent.dataprocess.query_sql_status \
    --args "$args_json" \
    --output json 2>&1)
  local mc_exit=$?

  if [ $mc_exit -ne 0 ]; then
    log "⚠️ 查询状态 mcporter 失败: exit=$mc_exit"
    echo "QUERY_FAILED"
    return 1
  fi

  log_debug "查询状态响应: $result"

  echo "$result" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    status = d.get('data', {}).get('status', 'UNKNOWN')
    if not status:
        status = d.get('status', 'UNKNOWN')
    print(status)
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN"
}

# 获取 SQL 结果
get_sql_result_json() {
  local query_id="$1"
  local args_json
  args_json=$(python3 -c "
import json
print(json.dumps({'query_id': '$query_id', 'response_format': 'CONCISE'}))
" 2>/dev/null)

  local result
  result=$(mcporter call mcp.ant.rpc.dpagent.dataprocess.query_sql_result \
    --args "$args_json" \
    --output json 2>&1)
  local mc_exit=$?

  if [ $mc_exit -ne 0 ]; then
    log "⚠️ 查询结果 mcporter 失败: exit=$mc_exit"
    echo ""
    return 1
  fi

  log_debug "查询结果响应: ${result:0:2000}"
  echo "$result"
  return 0
}

# 轮询 SQL 直到完成/失败/超时
# 返回: 0=成功 1=失败 2=超时
check_sql_done() {
  local query_id="$1"
  local max_wait="${2:-900}"
  local elapsed=0
  local wait_sec=30
  local last_status=""

  log "⏳ 开始轮询 SQL 状态 (query_id=$query_id, max_wait=${max_wait}s, 约 $((max_wait/60)) min)..."

  while [ $elapsed -lt $max_wait ]; do
    local status
    status=$(get_sql_status "$query_id")
    local get_status_exit=${PIPESTATUS[0]:-0}

    # 只在状态变化时打印日志
    if [ "$status" != "$last_status" ]; then
      log "   状态变化: ${last_status:-(无)} → $status (已等待 ${elapsed}s)"
      last_status="$status"
    fi

    case "$status" in
      SUCCESS)
        log "✅ SQL 执行成功 (query_id=$query_id, 总耗时 ${elapsed}s)"
        return 0
        ;;
      FAILED)
        log "❌ SQL 执行失败 (query_id=$query_id, 总耗时 ${elapsed}s)"
        return 1
        ;;
      RUNNING|SUBMITTED|PENDING)
        log_debug "   SQL 状态: $status, 已等待 ${elapsed}s, 下次等待 ${wait_sec}s..."
        sleep $wait_sec
        elapsed=$((elapsed + wait_sec))
        # 加速轮询
        if [ $elapsed -gt 180 ]; then
          wait_sec=10
        fi
        ;;
      QUERY_FAILED)
        log "⚠️ 查询状态失败 (get_status_exit=$get_status_exit), 10s 后继续轮询"
        sleep 10
        elapsed=$((elapsed + 10))
        ;;
      *)
        log "❓ 未知状态: $status, 10s 后继续轮询"
        sleep 10
        elapsed=$((elapsed + 10))
        ;;
    esac
  done

  log "⏰ SQL 轮询超时 (query_id=$query_id, 已等待 ${elapsed}s / max=${max_wait}s)"
  return 2
}

# 一步执行：提交+等待
exec_sql_and_wait() {
  local sql="$1"
  local max_wait="${2:-900}"
  local step_name="${3:-SQL}"
  _LAST_STEP="$step_name"

  log "📤 [$step_name] 准备提交 SQL..."
  log_sql "$step_name" "$sql"
  capture_sql_for_output "$step_name" "$sql"

  local query_id
  query_id=$(run_sql "$sql")
  local submit_exit=$?

  if [ $submit_exit -ne 0 ] || [ -z "$query_id" ]; then
    log "❌ [$step_name] SQL 提交失败 (exit=$submit_exit)"
    return 10
  fi

  log "📋 [$step_name] query_id=$query_id, 开始轮询..."

  # 记录 query_id 到历史文件
  if [ -n "$query_id" ] && [ -n "$_QUERY_ID_HISTORY_FILE" ] && [ -f "$_QUERY_ID_HISTORY_FILE" ]; then
    python3 - "$step_name" "$query_id" "$_QUERY_ID_HISTORY_FILE" <<'PY' 2>/dev/null || true
import json, sys
step, qid, path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path, 'r', encoding='utf-8') as f:
        arr = json.load(f)
    arr.append({"step": step, "queryId": qid})
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(arr, f, ensure_ascii=False)
except Exception:
    pass
PY
  fi

  if ! check_sql_done "$query_id" "$max_wait"; then
    local poll_exit=$?
    if [ $poll_exit -eq 1 ]; then
      log "❌ [$step_name] SQL 执行失败 (query_id=$query_id)"
      return 11
    elif [ $poll_exit -eq 2 ]; then
      log "⏰ [$step_name] SQL 轮询超时 (query_id=$query_id)"
      return 12
    fi
  fi

  log "✅ [$step_name] SQL 执行完成 (query_id=$query_id)"
  return 0
}

# 提取 count 值
extract_count_from_result() {
  local json_data="$1"
  if [ -z "$json_data" ]; then
    echo 0
    return
  fi

  log_debug "提取计数, 原始数据: ${json_data:0:1000}"

  echo "$json_data" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    results = d.get('data', {}).get('results', [])
    if results and len(results) > 0:
        result = results[0]
        rows = result.get('data', [])
        # TABLE格式: [['col'], ['val']]
        if len(rows) > 1 and len(rows[1]) > 0:
            val = rows[1][0]
            print(int(val) if str(val).isdigit() else 0)
        elif len(rows) == 1 and len(rows[0]) > 0:
            val = rows[0][0]
            print(int(val) if str(val).isdigit() else 0)
        else:
            print(0)
    else:
        print(0)
except Exception as e:
    sys.stderr.write('Error in extract_count: ' + str(e) + '\n')
    print(0)
" 2>/dev/null || echo 0
}

# ============================================
# 参数校验函数
# ============================================

# 校验 SAMPLE_SCOPE 是否像合法的 WHERE 子句
# 返回: 0=合法 1=可疑(有警告) 2=明显无效
validate_sample_scope_where() {
  local scope="$1"
  local reason=""

  # 空值由调用方处理
  if [ -z "$scope" ]; then
    echo "EMPTY"
    return 2
  fi

  # 去除首尾空白
  scope=$(echo "$scope" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

  # 长度过短 — 单词级内容大概率是 LLM 输出错误
  if [ ${#scope} -lt 5 ]; then
    echo "TOO_SHORT: 长度=${#scope}, 值='${scope}'"
    return 2
  fi

  # 纯单词黑名单 — LLM 常见错误输出
  local lower_scope
  lower_scope=$(echo "$scope" | tr '[:upper:]' '[:lower:]')
  case "$lower_scope" in
    sql|query|where|select|from|none|null|undefined|na|n/a|unknown|true|false|yes|no)
      echo "BLACKLIST: 值为 LLM 错误输出 '${scope}'"
      return 2
      ;;
  esac

  # 必须包含至少一个比较运算符或逻辑运算符
  if ! echo "$scope" | grep -qiE '(=|<>|!=|>|<|LIKE|IN\s*\(|IS\s+|BETWEEN|AND|OR)'; then
    echo "NO_OPERATOR: 不含比较/逻辑运算符, 值='${scope}'"
    return 2
  fi

  # 完整 SELECT 语句走子查询分支，不算 WHERE 子句
  if echo "$scope" | grep -qi '^\s*SELECT'; then
    echo "FULL_SELECT"
    return 0
  fi

  # 包含 FROM — 可能是不完整的 SELECT，也走子查询分支
  if echo "$scope" | grep -qi 'FROM'; then
    echo "HAS_FROM"
    return 0
  fi

  # 通过所有检查 — 看起来是合法的 WHERE 条件
  echo "VALID_WHERE"
  return 0
}

# 校验构造的 SQL 不含明显的语法错误
# 返回: 0=合法 1=有警告但仍可执行 2=明显错误
validate_constructed_sql() {
  local sql="$1"
  local reason=""

  # 检查 WHERE 后跟分号或无效 token
  if echo "$sql" | grep -qiE 'WHERE\s*;'; then
    echo "EMPTY_WHERE: SQL 包含 'WHERE;' (WHERE 后无条件)"
    return 2
  fi

  # 检查 WHERE 后跟黑名单单词
  if echo "$sql" | grep -qiE 'WHERE\s+(sql|query|where|select|from|none|null|undefined)\s*;'; then
    echo "BLACKLIST_WHERE: WHERE 后跟无效 token"
    return 2
  fi

  # 检查 WHERE 后是否只有空白就到分号
  if echo "$sql" | grep -qiE 'WHERE\s*\)\s*;'; then
    echo "EMPTY_WHERE_PAREN: WHERE 后紧跟 ')' 和 ';'"
    return 2
  fi

  # 必须包含 SELECT 和 FROM
  if ! echo "$sql" | grep -qi 'SELECT'; then
    echo "NO_SELECT: SQL 缺少 SELECT"
    return 2
  fi
  if ! echo "$sql" | grep -qi 'FROM'; then
    echo "NO_FROM: SQL 缺少 FROM"
    return 2
  fi

  echo "OK"
  return 0
}

# ============================================
# Step 1b: 参数合法性校验（机制性防护）
# ============================================
_VALIDATE_WARNINGS=""

log "=========================================="
log "--- Step 1b: SAMPLE_SCOPE 合法性校验 ---"
log "📥 SAMPLE_SCOPE 原始值: '${SAMPLE_SCOPE}'"

if [ -n "$SAMPLE_SCOPE" ]; then
  validate_result=$(validate_sample_scope_where "$SAMPLE_SCOPE")
  validate_exit=$?

  log "📋 校验结果: $validate_result (exit=$validate_exit)"

  case $validate_exit in
    2)
      # 明显无效 — 记录到警告，后续构造 SQL 时尝试从完整 SELECT 回退
      _VALIDATE_WARNINGS="${_VALIDATE_WARNINGS}SAMPLE_SCOPE 校验失败: ${validate_result}; "
      log "⚠️ SAMPLE_SCOPE 校验失败: ${validate_result}"
      log "   将尝试其他路径构造 SQL..."
      # 标记 SAMPLE_SCOPE 不可信
      export _SAMPLE_SCOPE_INVALID="true"
      ;;
    1)
      # 可疑 — 记录警告，但不阻断
      _VALIDATE_WARNINGS="${_VALIDATE_WARNINGS}SAMPLE_SCOPE 校验警告: ${validate_result}; "
      log "⚠️ SAMPLE_SCOPE 校验警告: ${validate_result}"
      ;;
    0)
      log "✅ SAMPLE_SCOPE 校验通过: ${validate_result}"
      ;;
  esac
else
  log "ℹ️ SAMPLE_SCOPE 为空"
fi

# ============================================
# Step 2: 权限检查
# ============================================
PERMISSION_OK="true"
PERMISSION_DETAIL=""

log "=========================================="
log "--- Step 2: 样本表权限检查 ---"

if [ -n "${TABLES:-}" ]; then
  IFS=',' read -ra TABLE_LIST <<< "$TABLES"
  for tbl in "${TABLE_LIST[@]}"; do
    tbl=$(echo "$tbl" | xargs)  # trim
    [ -z "$tbl" ] && continue

    CHECK_SQL="SELECT 1 FROM ${tbl} WHERE dt = '${DATE_START}' LIMIT 1;"
    log "🔍 检查表: $tbl"

    exec_status=0
    exec_sql_and_wait "$CHECK_SQL" 180 "权限检查($tbl)" || exec_status=$?

    if [ $exec_status -ne 0 ]; then
      log "❌ 表 $tbl 无权限或不存在 (exit=$exec_status)"
      PERMISSION_OK="false"
      PERMISSION_DETAIL="${PERMISSION_DETAIL}表 ${tbl} 无权限; "
    else
      log "✅ 表 $tbl 可访问"
    fi
  done
fi

if [ "$PERMISSION_OK" = "false" ]; then
  output_error "权限检查失败: ${PERMISSION_DETAIL}" "请确认表权限"
fi

# ============================================
# Step 3: 建样本表
# ============================================
log "=========================================="
log "--- Step 3: 样本交易提取 ---"

_STEP3_STRATEGY=""

if [ "$_SAMPLE_SCOPE_INVALID" = "true" ]; then
  # SAMPLE_SCOPE 校验失败 — 尝试回退策略（按可靠性排序）
  log "⚠️ SAMPLE_SCOPE 无效，尝试回退策略..."

  # 策略0（最可靠）: 使用 dimaFields.sample_scope.sql_query
  # 这是 intent-recognition 节点专门提取的完整 SQL，比 extractedParams.sampleScope 更可靠
  if [ -n "${DIMA_SAMPLE_SQL_QUERY:-}" ] && echo "$DIMA_SAMPLE_SQL_QUERY" | grep -qi 'SELECT'; then
    log "✅ 回退策略0: 使用 DIMA_SAMPLE_SQL_QUERY (dimaFields.sample_scope.sql_query)"
    log "   SQL: ${DIMA_SAMPLE_SQL_QUERY:0:200}..."
    SAMPLE_SCOPE="$DIMA_SAMPLE_SQL_QUERY"
    unset _SAMPLE_SCOPE_INVALID
    _STEP3_STRATEGY="fallback_from_dima_sql_query"
  # 策略1: 从 USER_INPUT 中正则提取完整 SELECT 语句
  else
    _EXTRACTED_SQL=""
    _EXTRACTED_SQL=$(echo "$USER_INPUT" | python3 -c "
import sys, re
text = sys.stdin.read()
# 尝试匹配常见的 SELECT ... FROM ... WHERE ... 模式
patterns = [
    r'(SELECT\s+.{10,}?\s+FROM\s+[\w.]+(?:\s+WHERE\s+.{5,})?)',
    r'(\`\`\`sql\s*(SELECT\s+.{10,}?\s+FROM\s+[\w.]+.*?)\s*\`\`\`)',
    r'(\`\`\`\s*(SELECT\s+.{10,}?\s+FROM\s+[\w.]+.*?)\s*\`\`\`)',
]
for pat in patterns:
    m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
    if m:
        sql = m.group(1).strip()
        # 清理代码块标记
        sql = sql.replace('\`\`\`sql', '').replace('\`\`\`', '').strip()
        print(sql)
        sys.exit(0)
print('')
" 2>/dev/null || echo "")

    if [ -n "$_EXTRACTED_SQL" ] && echo "$_EXTRACTED_SQL" | grep -qi 'SELECT'; then
      log "✅ 回退策略1: 从 USER_INPUT 中提取到 SELECT 语句"
      log "   提取SQL: ${_EXTRACTED_SQL:0:200}..."
      SAMPLE_SCOPE="$_EXTRACTED_SQL"
      unset _SAMPLE_SCOPE_INVALID
      _STEP3_STRATEGY="extracted_from_user_input"
    # 策略2: 如果有 TABLES，用 TABLES 构造全表扫描（不带 WHERE）
    else
      if [ -n "${TABLES:-}" ]; then
        FIRST_TABLE=$(echo "$TABLES" | cut -d',' -f1 | xargs)
        log "📋 回退策略2: 使用 TABLES 全表扫描 (无 WHERE 条件)"
        _VALIDATE_WARNINGS="${_VALIDATE_WARNINGS}SQL回退: 使用 ${FIRST_TABLE} 全表扫描, 原SAMPLE_SCOPE无效; "
        STEP3_SQL="DROP TABLE IF EXISTS ${SAMPLE_TABLE}; CREATE TABLE IF NOT EXISTS ${SAMPLE_TABLE} AS SELECT trade_no, '${DATE_START}' AS dt FROM ${FIRST_TABLE} WHERE dt = '${DATE_START}';"
        _STEP3_STRATEGY="fallback_full_table_scan"
      else
        # 无任何回退方案 — 报错退出
        output_error "SAMPLE_SCOPE 无效且无回退方案" "SAMPLE_SCOPE='${SAMPLE_SCOPE}' 校验不通过(不是合法WHERE条件)。DIMA_SAMPLE_SQL_QUERY 也为空。USER_INPUT 中也未提取到有效 SELECT。TABLES 也为空。请检查 intent-recognition 节点的 extractedParams.sampleScope 和 dimaFields.sample_scope.sql_query 输出。校验详情: ${_VALIDATE_WARNINGS}"
      fi
    fi
  fi
fi

# 正常路径（SAMPLE_SCOPE 校验通过或回退成功）
if [ -z "$_STEP3_STRATEGY" ] || [ "$_STEP3_STRATEGY" = "extracted_from_user_input" ]; then
  if echo "$SAMPLE_SCOPE" | grep -qi '^\s*SELECT'; then
    STEP3_SQL="DROP TABLE IF EXISTS ${SAMPLE_TABLE}; CREATE TABLE IF NOT EXISTS ${SAMPLE_TABLE} AS SELECT sub.trade_no, '${DATE_START}' AS dt FROM (${SAMPLE_SCOPE}) sub;"
    _STEP3_STRATEGY="subquery_from_select"
  elif echo "$SAMPLE_SCOPE" | grep -qi 'FROM'; then
    # 包含 FROM 但可能不以 SELECT 开头（兜底）
    STEP3_SQL="DROP TABLE IF EXISTS ${SAMPLE_TABLE}; CREATE TABLE IF NOT EXISTS ${SAMPLE_TABLE} AS SELECT sub.trade_no, '${DATE_START}' AS dt FROM (${SAMPLE_SCOPE}) sub;"
    _STEP3_STRATEGY="subquery_from_fragment"
  elif [ -n "${TABLES:-}" ]; then
    # SAMPLE_SCOPE 只是 WHERE 条件，TABLES 提供数据源表
    FIRST_TABLE=$(echo "$TABLES" | cut -d',' -f1 | xargs)
    log "📋 SAMPLE_SCOPE 为 WHERE 条件，使用数据源表: $FIRST_TABLE"
    STEP3_SQL="DROP TABLE IF EXISTS ${SAMPLE_TABLE}; CREATE TABLE IF NOT EXISTS ${SAMPLE_TABLE} AS SELECT trade_no, '${DATE_START}' AS dt FROM ${FIRST_TABLE} WHERE ${SAMPLE_SCOPE};"
    _STEP3_STRATEGY="where_from_scope_and_tables"
  else
    output_error "缺少数据源表名，无法构造样本提取 SQL" "SAMPLE_SCOPE='${SAMPLE_SCOPE}' 不是完整的 SELECT 语句，且 TABLES 参数为空。请在 workflow 中配置 TABLES 环境变量，或确保 upstream intent-recognition 输出完整的 SELECT SQL。"
  fi
fi

# SQL 构造后二次校验
log "📋 Step3 构造策略: ${_STEP3_STRATEGY}"
log_sql "Step3 构造结果" "$STEP3_SQL"

_SQL_VALIDATE_RESULT=$(validate_constructed_sql "$STEP3_SQL")
_SQL_VALIDATE_EXIT=$?
if [ $_SQL_VALIDATE_EXIT -eq 2 ]; then
  log "❌ 构造的 SQL 未通过合法性校验: $_SQL_VALIDATE_RESULT"
  output_error "构造的 SQL 未通过合法性校验" "校验结果: ${_SQL_VALIDATE_RESULT}; SQL内容: ${STEP3_SQL:0:500}; 校验警告: ${_VALIDATE_WARNINGS}"
fi
if [ $_SQL_VALIDATE_EXIT -eq 1 ]; then
  log "⚠️ 构造的 SQL 有警告: $_SQL_VALIDATE_RESULT (仍将尝试执行)"
  _VALIDATE_WARNINGS="${_VALIDATE_WARNINGS}SQL构造警告: ${_SQL_VALIDATE_RESULT}; "
fi

export _STEP3_SQL="$STEP3_SQL"
export _STEP3_STRATEGY="${_STEP3_STRATEGY:-}"
step3_status=0
exec_sql_and_wait "$STEP3_SQL" 900 "样本提取" || step3_status=$?
export _STEP3_STATUS="$step3_status"
export _STEP3_QUERY_ID="$_LAST_QUERY_ID"

if [ $step3_status -ne 0 ]; then
  output_error "Step 3 样本交易提取SQL执行失败" "exit_code=$step3_status"
fi

log "✅ Step 3 完成"

# ============================================
# Step 3b: 统计样本数
# ============================================
SAMPLE_COUNT=0
query_id=""

COUNT_SQL="SELECT COUNT(*) AS cnt FROM ${SAMPLE_TABLE};"
export _COUNT_SQL="$COUNT_SQL"
log "=========================================="
log "--- Step 3b: 统计样本数 ---"
log "📤 计数 SQL: $COUNT_SQL"

query_id=$(run_sql "$COUNT_SQL")
run_exit=$?
export _COUNT_QUERY_ID="$query_id"

if [ $run_exit -ne 0 ] || [ -z "$query_id" ]; then
  log "⚠️ 计数 SQL 提交失败，跳过计数 (exit=$run_exit)"
  SAMPLE_COUNT=0
else
  log "📋 计数 query_id=$query_id"
  check_status=0
  check_sql_done "$query_id" 120 || check_status=$?

  if [ $check_status -eq 0 ]; then
    COUNT_JSON=$(get_sql_result_json "$query_id")
    get_exit=$?
    if [ $get_exit -eq 0 ] && [ -n "$COUNT_JSON" ]; then
      SAMPLE_COUNT=$(extract_count_from_result "$COUNT_JSON")
      log "📊 样本计数结果: $SAMPLE_COUNT"
    else
      log "⚠️ 获取计数结果失败 (get_exit=$get_exit)"
      SAMPLE_COUNT=0
    fi
  else
    log "⚠️ 计数 SQL 轮询失败 (exit=$check_status)"
    SAMPLE_COUNT=0
  fi
fi

log "📊 样本交易数: $SAMPLE_COUNT"

_SAMPLE_EMPTY_WARNING="false"
if [ "$SAMPLE_COUNT" -eq 0 ] 2>/dev/null; then
  log "⚠️ 样本交易提取结果为空(0条记录)，流程继续，但标记异常"
  _SAMPLE_EMPTY_WARNING="true"
fi

# ============================================
# Step 4: 建宽表
# ============================================
log "=========================================="
log "--- Step 4: 关联离线稽核信息 ---"

STEP4_SQL="DROP TABLE IF EXISTS ${ALLIN_TABLE}; CREATE TABLE IF NOT EXISTS ${ALLIN_TABLE} AS SELECT t1.trade_no, t2.trade_buyer_id, t2.trade_seller_id, t2.uni_trade_no, t2.gmt_occur, t2.business_code, t2.busi_prod, t2.trade_total_amt, t2.total_dst_amt, t2.ali_dst_amt, t2.mer_sub_amt, t2.goods_title, t2.partner_id, t2.merchant_id, t2.secondary_merchant_id, t2.pid_smid, t2.merchant_type, t2.abnor_type_list, t1.dt FROM ${SAMPLE_TABLE} t1 LEFT JOIN (SELECT trade_no, trade_buyer_id, trade_seller_id, uni_trade_no, gmt_occur, business_code, busi_prod, trade_total_amt, total_dst_amt, ali_dst_amt, mer_sub_amt, goods_title, partner_id, merchant_id, secondary_merchant_id, pid_smid, merchant_type, abnor_type_list FROM antctu.adm_ctu_app_ekyt_abnor_allin_di WHERE dt BETWEEN '${DATE_START}' AND '${DATE_END}') t2 ON t1.trade_no = t2.trade_no;"

export _STEP4_SQL="$STEP4_SQL"
step4_status=0
exec_sql_and_wait "$STEP4_SQL" 900 "关联稽核" || step4_status=$?
export _STEP4_STATUS="$step4_status"
export _STEP4_QUERY_ID="$_LAST_QUERY_ID"

if [ $step4_status -ne 0 ]; then
  output_error "Step 4 关联稽核信息SQL执行失败" "exit_code=$step4_status"
fi

log "✅ Step 4 完成"

# ============================================
# Step 4b: 统计关联数据
# ============================================
STATS_SQL="SELECT COUNT(*) AS total, SUM(CASE WHEN abnor_type_list IS NOT NULL AND abnor_type_list != '' THEN 1 ELSE 0 END) AS risk_cnt, SUM(CASE WHEN trade_buyer_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_cnt FROM ${ALLIN_TABLE};"

TOTAL_COUNT="$SAMPLE_COUNT"
MATCHED_COUNT=0
RISK_COUNT=0

log "=========================================="
log "--- Step 4b: 统计关联数据 ---"
log "📤 统计 SQL: $STATS_SQL"

query_id=$(run_sql "$STATS_SQL")
run_exit=$?

if [ $run_exit -eq 0 ] && [ -n "$query_id" ]; then
  log "📋 统计 query_id=$query_id"
  check_status=0
  check_sql_done "$query_id" 120 || check_status=$?

  if [ $check_status -eq 0 ]; then
    STATS_JSON=$(get_sql_result_json "$query_id")
    if [ -n "$STATS_JSON" ]; then
      TOTAL_COUNT=$(extract_count_from_result "$STATS_JSON")
      # 需要按列索引提取，先取结果
      local stats_line
      stats_line=$(echo "$STATS_JSON" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    rows = d.get('data', {}).get('results', [{}])[0].get('data', [])
    if len(rows) > 1:
        vals = rows[1]
        total = int(vals[0]) if str(vals[0]).isdigit() else 0
        risk = int(vals[1]) if str(vals[1]).isdigit() else 0
        matched = int(vals[2]) if str(vals[2]).isdigit() else 0
        print(f'{total}|{risk}|{matched}')
    else:
        print('0|0|0')
except Exception as e:
    print(f'0|0|0')
" 2>/dev/null)
      IFS='|' read TOTAL_COUNT RISK_COUNT MATCHED_COUNT <<< "$stats_line" || true
      log "📊 统计结果分解: total=$stats_line"
    fi
  else
    log "⚠️ 统计 SQL 轮询失败 (exit=$check_status)"
  fi
else
  log "⚠️ 统计 SQL 提交失败 (exit=$run_exit)"
fi

log "📊 最终统计: 样本总量=$TOTAL_COUNT, 关联匹配=$MATCHED_COUNT, 有风险标签=$RISK_COUNT"

# ============================================
# 完成输出
# ============================================
log "=========================================="
log "=== 覆盖分析 SQL 执行完成 ==="
log "=========================================="
output_success
