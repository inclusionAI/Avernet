#!/bin/bash
# 停滞哨兵：裁判脚本「节点静默死亡」的检测器（全 Bot 桌无人值守时用）。
#
# 判活 = max(driver 日志, 玩家日志, **模型服务日志**) 三方 mtime 取最新。
# 模型服务日志是全桌心跳：6 个玩家（我们 + 5 个 OpenClaw）全走同一个单实例
# 模型服务，链路活着它就一直在处理请求。发言链是串行的、后期提示词 15k+
# token、服务一次一个请求——driver/玩家日志静默二三十分钟很正常，但三方
# 同时安静 = run 真的死了（driver 自带 60 分钟 WATCHDOG 会自动 SX recover，
# 但那要干等一小时；本哨兵的价值是 20 分钟就报警、早动手）。
#
# 三个真实的坑（都踩过，别改回去）：
# 1. 确认键只用 phase 不用秒数——silence 每轮都在涨，拿 "silence:phase" 做
#    确认键永远不相等，「连续两次确认」永远不会触发（哨兵永不响）；
# 2. 判活阈值不能只按节点预算（420s/节点）推导——实测一条健康的串行发言链
#    静默 44 分钟仍在出牌（慢 LLM 转弯互相排队）。要么把模型服务日志纳入
#    判活（本脚本的做法，20 分钟即可），要么把阈值提到小时级；
# 3. 假报警不是无害的——对健康轮误发「卡住了」会作废正常进度、白烧每轮
#    2 次的 sx_recover 重开预算。报警后动手前先看模型服务是否在处理请求。
#
# 用法（三个日志路径必须用环境变量传，无默认值）：
#   REF_DRIVER_LOG=... REF_PLAYER_LOG=... REF_SRV_LOG=... bash ops-stall-sentinel.sh
# 可选：REF_SESSION=<session_id> 固定会话（默认每轮从 driver 日志 grep 最新）；
#       REF_UC=<undercover.py 路径>；REF_THRESH=<静默阈值秒，默认 1200>。
#
# 报警后的处置（见 INTEGRATION.md 常见坑 #7/#9）——先看现场，再决定动作：
#   a. 模型服务还在处理请求 → 链子活着，什么都不做（继续等）；
#   b. 三方同静（服务也闲）→ **保持玩家在线**，直接以裁判身份手动重开本轮：
#        env PATH=<bcs-cli 目录>:$PATH BCN_BOT_TOKEN=$(cat ~/.mlx-referee.token) \
#          python undercover.py open-vote --session <sid> --retry   # 发言链卡用 open-round
#      不要走「停玩家 → task.message 卡住了 → 重启玩家」老流程：重开提交时
#      状态机会把 vote_start 节点投递给本 bot，bot 离线则提交直接 400
#      （Bot 'bot_xxx' is not connected）。2026-11-12 实测踩坑。
#   b2. 若是投票链在计票前挂掉（sm run 死于 420s 节点超时、票面其实都收齐了）：
#      别再 re-vote（结构性必死），直接把计票节点以 task.message 人工回灌给
#      driver（[State Machine Task] + node_id: tally + [Upstream Outputs] 的
#      [vote_N] 票面原文 + --round N --stage regular --attempt M），driver 会走
#      设计内的 s4_tally→reveal→finish 全链。votes-set 硬校验全员票齐，迟到的
#      票面也能从 BCS 日志里捞回来。参考 /tmp/nudge_tally.py。
#   c. 重开后几分钟内又挂、或同一个问题反复出现 → 停止重试，中断处理/治本。
#      已知根因：BCS 状态机节点预算 420s < 共享单卡服务拥堵时的最坏排队
#      （票节点并行扇出、提示词 15k+，串行预填必超时）——重开只是续命，
#      治本靠压缩上下文/降低并发负载（INTEGRATION.md 坑 #9）。
LOG="${REF_DRIVER_LOG:?need REF_DRIVER_LOG}"
PLOG="${REF_PLAYER_LOG:?need REF_PLAYER_LOG}"
SLOG="${REF_SRV_LOG:?need REF_SRV_LOG}"
UC="${REF_UC:-/Users/lipeilin/代码库/python/Avernet/singlebox/agents/6bots_undercover_game_profile/referee/skills/undercover-game-referee/scripts/undercover.py}"
SID_FIXED="${REF_SESSION:-}"
THRESH="${REF_THRESH:-1200}"

stall_prev=""
while true; do
  sleep 240
  SID="$SID_FIXED"
  if [ -z "$SID" ]; then
    SID=$(grep -o '"session": "[^"]*"' "$LOG" | tail -1 | cut -d'"' -f4)
    [ -z "$SID" ] && continue
  fi
  now=$(date +%s)
  newest=0
  for f in "$LOG" "$PLOG" "$SLOG"; do
    [ -f "$f" ] || continue
    m=$(($(date -r "$f" +%s)))
    [ "$m" -gt "$newest" ] && newest=$m
  done
  [ "$newest" -eq 0 ] && continue
  silence=$((now - newest))
  [ "$silence" -lt "$THRESH" ] && { stall_prev=""; continue; }
  phase=$(python3 "$UC" status --session "$SID" 2>/dev/null | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("phase","?"))
except Exception: print("?")')
  case "$phase" in
    *_RUNNING) ;;
    *) stall_prev=""; continue ;;
  esac
  if [ "$stall_prev" = "$phase" ]; then
    echo "STALL CONFIRMED phase=$phase silence=${silence}s session=$SID — 先确认模型服务空闲；保持玩家在线，直接手动重开本轮（open-round/open-vote --retry，见文件头 b）；投票 run 死于计票前用计票回灌（b2）；同问题反复出现则停止重试（c）"
    stall_prev=""  # 报警后清零：持续停滞每两轮再报一次，不刷屏
  else
    stall_prev="$phase"
  fi
done
