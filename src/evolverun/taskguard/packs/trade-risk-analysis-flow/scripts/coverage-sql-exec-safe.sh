#!/usr/bin/bash
# coverage-sql-exec.sh — 覆盖分析 SQL 执行脚本（安全版本）
# 执行 Step 1~4: 参数校验 → 权限检查 → 建样本表 → 关联稽核宽表
# 输出 JSON 给下游 coverage-analysis 节点消费
# ⚠️ 重要：stdout 必须是有效 JSON，所有日志写到 stderr

# ============================================
# 全局输出函数（任何路径最终都要调用 success 或 error）
# ============================================
output_success() {
  cat <<EOF
{
  "success": true,
  "permissionChecked": ${PERMISSION_OK:-true},
  "sampleTable": "${SAMPLE_TABLE}",
  "allinTable": "${ALLIN_TABLE}",
  "sampleCount": ${TOTAL_COUNT:-0},
  "matchedCount": ${MATCHED_COUNT:-0},
  "riskCount": ${RISK_COUNT:-0},
  "dateStart": "${DATE_START}",
  "dateEnd": "${DATE_END}",
  "sceneAbbr": "${SCENE_ABBR}",
  "extractedParams": {
    "uid": "${ALIPAY_UID:-}",
    "tradeNo": "${TRADE_NO:-}",
    "digitalPoiId": "${DIGITAL_POI_ID:-}",
    "sampleScope": "${SAMPLE_SCOPE:-}",
    "dateRange": "${DATE_RANGE:-}",
    "tables": "${TABLES:-}"
  }
}
EOF
  exit 0
}

output_error() {
  local msg="$1"
  local detail="${2:-}"
  cat <<EOF
{
  "success": false,
  "error": "${msg}",
  "errorDetail": "${detail}",
  "permissionChecked": ${PERMISSION_OK:-false},
  "sampleTable": "${SAMPLE_TABLE:-}",
  "allinTable": "${ALLIN_TABLE:-}",
  "sampleCount": ${SAMPLE_COUNT:-0},
  "matchedCount": ${MATCHED_COUNT:-0},
  "riskCount": ${RISK_COUNT:-0},
  "extractedParams": {
    "uid": "${ALIPAY_UID:-}",
    "tradeNo": "${TRADE_NO:-}",
    "digitalPoiId": "${DIGITAL_POI_ID:-}",
    "sampleScope": "${SAMPLE_SCOPE:-}",
    "dateRange": "${DATE_RANGE:-}",
    "tables": "${TABLES:-}"
  }
}
EOF
  exit 0
}

# 所有日志写 stderr
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2
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

# ⚠️ 关键: SCENE_ABBR 安全化 — 确保表名不包含中文等非 ASCII 字符
if echo "$SCENE_ABBR" | grep -qP '[^\x00-\x7F]'; then
  log "⚠️ SCENE_ABBR 含非 ASCII 字符: '$SCENE_ABBR', 执行安全化转换..."
  _RAW_SCENE_ABBR="$SCENE_ABBR"
  SCENE_ABBR=$(sanitize_scene_abbr "$SCENE_ABBR")
  log "✅ SCENE_ABBR 安全化: '$_RAW_SCENE_ABBR' → '$SCENE_ABBR'"
fi

SAMPLE_TABLE="alipaybipub_dev.sc_fzb_scene_trd_${SCENE_ABBR}_${DATE_START}"
ALLIN_TABLE="alipaybipub_dev.sc_fzb_scene_trd_allin_${SCENE_ABBR}_${DATE_START}"

log "=== 覆盖分析 SQL 执行开始 ==="
log "场景: $SCENE_ABBR | 日期: $DATE_START ~ $DATE_END"
log "样本表: $SAMPLE_TABLE"
log "宽表: $ALLIN_TABLE"

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
  local args_json
  args_json=$(python3 -c "
import json, sys, os
project_id = os.environ.get('PROJECT_ID', '410894')
print(json.dumps({'project_id': project_id, 'sql_query': sys.argv[1]}))
" "$sql" 2>/dev/null)

  if [ -z "$args_json" ]; then
    log "❌ 构造 SQL 参数失败"
    echo ""
    return 1
  fi

  log "📤 mcporter 提交 SQL..."
  local result
  result=$(mcporter call mcp.ant.rpc.dpagent.dataprocess.run_sql_query \
    --args "$args_json" \
    --output json 2>&1)
  local mc_exit=$?

  if [ $mc_exit -ne 0 ]; then
    log "❌ mcporter 调用失败: exit=$mc_exit, output=${result:0:500}"
    echo ""
    return 1
  fi

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
    log "❌ mcporter 响应中无 query_id: ${result:0:500}"
    echo ""
    return 1
  fi

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

  echo "$result"
  return 0
}

# 轮询 SQL 直到完成/失败/超时
# 返回: 0=成功 1=失败 2=超时
check_sql_done() {
  local query_id="$1"
  local max_wait="${2:-600}"
  local elapsed=0
  local wait_sec=30

  log "⏳ 开始轮询 SQL 状态 (query_id=$query_id, max_wait=${max_wait}s)..."

  while [ $elapsed -lt $max_wait ]; do
    local status
    status=$(get_sql_status "$query_id")
    local get_status_exit=${PIPESTATUS[0]:-0}

    case "$status" in
      SUCCESS)
        log "✅ SQL 执行成功 (query_id=$query_id)"
        return 0
        ;;
      FAILED)
        log "❌ SQL 执行失败 (query_id=$query_id)"
        return 1
        ;;
      RUNNING|SUBMITTED|PENDING)
        log "⏳ SQL 状态: $status, 已等待 ${elapsed}s, 下次等待 ${wait_sec}s..."
        sleep $wait_sec
        elapsed=$((elapsed + wait_sec))
        # 加速轮询
        if [ $elapsed -gt 180 ]; then
          wait_sec=10
        fi
        ;;
      QUERY_FAILED)
        log "⚠️ 查询状态失败, 继续轮询"
        sleep 10
        elapsed=$((elapsed + 10))
        ;;
      *)
        log "❓ 未知状态: $status, 继续轮询"
        sleep 10
        elapsed=$((elapsed + 10))
        ;;
    esac
  done

  log "⏰ SQL 轮询超时 (已等待 ${elapsed}s)"
  return 2
}

# 一步执行：提交+等待
exec_sql_and_wait() {
  local sql="$1"
  local max_wait="${2:-600}"
  local step_name="${3:-SQL}"

  log "📤 $step_name: 提交 SQL..."
  local query_id
  query_id=$(run_sql "$sql")
  local submit_exit=$?

  if [ $submit_exit -ne 0 ] || [ -z "$query_id" ]; then
    log "❌ $step_name: SQL 提交失败"
    return 10
  fi

  log "📋 $step_name: query_id=$query_id"

  if ! check_sql_done "$query_id" "$max_wait"; then
    local poll_exit=$?
    if [ $poll_exit -eq 1 ]; then
      log "❌ $step_name: SQL 执行失败"
      return 11
    elif [ $poll_exit -eq 2 ]; then
      log "⏰ $step_name: SQL 轮询超时"
      return 12
    fi
  fi

  return 0
}

# 提取 count 值
extract_count_from_result() {
  local json_data="$1"
  if [ -z "$json_data" ]; then
    echo 0
    return
  fi

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
# Step 2: 权限检查
# ============================================
PERMISSION_OK="true"
PERMISSION_DETAIL=""

log "--- Step 2: 样本表权限检查 ---"

if [ -n "${TABLES:-}" ]; then
  IFS=',' read -ra TABLE_LIST <<< "$TABLES"
  for tbl in "${TABLE_LIST[@]}"; do
    tbl=$(echo "$tbl" | xargs)  # trim
    [ -z "$tbl" ] && continue

    CHECK_SQL="SELECT 1 FROM ${tbl} WHERE dt = '${DATE_START}' LIMIT 1;"
    log "🔍 检查表: $tbl"

    exec_status=0
    exec_sql_and_wait "$CHECK_SQL" 120 "权限检查($tbl)" || exec_status=$?

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
log "--- Step 3: 样本交易提取 ---"

if echo "$SAMPLE_SCOPE" | grep -qi '^\s*SELECT'; then
  STEP3_SQL="DROP TABLE IF EXISTS ${SAMPLE_TABLE}; CREATE TABLE IF NOT EXISTS ${SAMPLE_TABLE} AS SELECT sub.trade_no, '${DATE_START}' AS dt FROM (${SAMPLE_SCOPE}) sub;"
else
  STEP3_SQL="DROP TABLE IF EXISTS ${SAMPLE_TABLE}; CREATE TABLE IF NOT EXISTS ${SAMPLE_TABLE} AS SELECT trade_no, dt FROM ${SAMPLE_SCOPE};"
fi

log "📋 Step3 SQL: ${STEP3_SQL:0:200}..."

step3_status=0
exec_sql_and_wait "$STEP3_SQL" 600 "样本提取" || step3_status=$?

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
log "📤 提交计数 SQL: $COUNT_SQL"

query_id=$(run_sql "$COUNT_SQL")
run_exit=$?

if [ $run_exit -ne 0 ] || [ -z "$query_id" ]; then
  log "⚠️ 计数 SQL 提交失败，跳过计数"
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
      log "⚠️ 获取计数结果失败"
      SAMPLE_COUNT=0
    fi
  else
    log "⚠️ 计数 SQL 轮询失败 (exit=$check_status)"
    SAMPLE_COUNT=0
  fi
fi

log "📊 样本交易数: $SAMPLE_COUNT"

if [ "$SAMPLE_COUNT" -eq 0 ] 2>/dev/null; then
  output_error "样本交易提取结果为空(0条记录)" "query_id=$query_id, sample_table=$SAMPLE_TABLE"
fi

# ============================================
# Step 4: 建宽表
# ============================================
log "--- Step 4: 关联离线稽核信息 ---"

STEP4_SQL="DROP TABLE IF EXISTS ${ALLIN_TABLE}; CREATE TABLE IF NOT EXISTS ${ALLIN_TABLE} AS SELECT t1.trade_no, t2.trade_buyer_id, t2.trade_seller_id, t2.uni_trade_no, t2.gmt_occur, t2.business_code, t2.busi_prod, t2.trade_total_amt, t2.total_dst_amt, t2.ali_dst_amt, t2.mer_sub_amt, t2.goods_title, t2.partner_id, t2.merchant_id, t2.secondary_merchant_id, t2.pid_smid, t2.merchant_type, t2.abnor_type_list, t1.dt FROM ${SAMPLE_TABLE} t1 LEFT JOIN (SELECT trade_no, trade_buyer_id, trade_seller_id, uni_trade_no, gmt_occur, business_code, busi_prod, trade_total_amt, total_dst_amt, ali_dst_amt, mer_sub_amt, goods_title, partner_id, merchant_id, secondary_merchant_id, pid_smid, merchant_type, abnor_type_list FROM antctu.adm_ctu_app_ekyt_abnor_allin_di WHERE dt BETWEEN '${DATE_START}' AND '${DATE_END}') t2 ON t1.trade_no = t2.trade_no;"

step4_status=0
exec_sql_and_wait "$STEP4_SQL" 600 "关联稽核" || step4_status=$?

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

query_id=$(run_sql "$STATS_SQL")
run_exit=$?

if [ $run_exit -eq 0 ] && [ -n "$query_id" ]; then
  check_status=0
  check_sql_done "$query_id" 120 || check_status=$?

  if [ $check_status -eq 0 ]; then
    STATS_JSON=$(get_sql_result_json "$query_id")
    if [ -n "$STATS_JSON" ]; then
      TOTAL_COUNT=$(extract_count_from_result "$STATS_JSON")
      # 需要按列索引提取，先取结果
      echo "$STATS_JSON" | python3 -c "
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
except:
    print('0|0|0')
" 2>/dev/null | IFS='|' read TOTAL_COUNT RISK_COUNT MATCHED_COUNT || true
    fi
  fi
fi

log "📊 样本总量: $TOTAL_COUNT, 关联匹配: $MATCHED_COUNT, 有风险标签: $RISK_COUNT"

# ============================================
# 完成输出
# ============================================
log "=== 覆盖分析 SQL 执行完成 ==="
output_success
