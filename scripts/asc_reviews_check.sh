#!/usr/bin/env bash
# asc_reviews_check.sh
# 拉每个 App 的 customer reviews（最新 30 条），对比上次快照标识新评论。
# 需要 ASC API Key + 每个 App 的 ASC App ID（数字）。
#
# Usage:
#   export ASC_KEY_ID=...
#   export ASC_ISSUER_ID=...
#   export ASC_KEY_FILE=$HOME/AuthKey.p8
#   bash orchestrator/asc_reviews_check.sh
#
# App ID 从 ASC > 你的 App > General > App Information > Apple ID 看。
# 第一次跑时把 App ID 填到 ASC_APP_IDS 数组里。

set -euo pipefail

# ── 每个 App 的 ASC Apple ID（占位，第一次审核通过后回填）──
declare -A ASC_APP_IDS=(
  [autochoice]=""
  [altitudenow]=""
  [daysuntil]=""
)

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SNAPSHOT_DIR="$REPO_ROOT/orchestrator/.review-snapshots"
mkdir -p "$SNAPSHOT_DIR"

# Validate
for var in ASC_KEY_ID ASC_ISSUER_ID ASC_KEY_FILE; do
  if [ -z "${!var:-}" ]; then
    echo "ERROR: $var 未设置" >&2
    exit 1
  fi
done

# ── JWT (同 asc_sales_report.sh) ──
NOW=$(date +%s)
EXP=$((NOW + 1200))
HEADER=$(printf '{"alg":"ES256","kid":"%s","typ":"JWT"}' "$ASC_KEY_ID" | openssl base64 -A | tr -d '=' | tr '/+' '_-')
PAYLOAD=$(printf '{"iss":"%s","exp":%d,"aud":"appstoreconnect-v1"}' "$ASC_ISSUER_ID" "$EXP" | openssl base64 -A | tr -d '=' | tr '/+' '_-')
SIGNATURE=$(printf '%s.%s' "$HEADER" "$PAYLOAD" | openssl dgst -sha256 -sign "$ASC_KEY_FILE" -binary | openssl base64 -A | tr -d '=' | tr '/+' '_-')
JWT="$HEADER.$PAYLOAD.$SIGNATURE"

# ── Per-App ──
for app in "${!ASC_APP_IDS[@]}"; do
  ID="${ASC_APP_IDS[$app]}"
  if [ -z "$ID" ]; then
    echo "[skip] $app: ASC App ID 未填，跳过"
    continue
  fi

  echo ""
  echo "═══ $app (Apple ID $ID) ═══"

  URL="https://api.appstoreconnect.apple.com/v1/apps/$ID/customerReviews?limit=30&sort=-createdDate"
  RAW=$(curl -s -H "Authorization: Bearer $JWT" -H "Accept: application/json" "$URL")

  # 错误检测
  if echo "$RAW" | jq -e '.errors' >/dev/null 2>&1; then
    echo "ERROR: $(echo "$RAW" | jq -c '.errors[0]')"
    continue
  fi

  # 提取 review IDs
  IDS=$(echo "$RAW" | jq -r '.data[].id' | sort)
  SNAPSHOT="$SNAPSHOT_DIR/$app.txt"

  if [ -f "$SNAPSHOT" ]; then
    NEW_IDS=$(comm -13 "$SNAPSHOT" <(echo "$IDS"))
  else
    NEW_IDS="$IDS"
    echo "[初次运行] 全量当前评论作为 baseline"
  fi

  # 输出新评论
  if [ -n "$NEW_IDS" ]; then
    NEW_COUNT=$(echo "$NEW_IDS" | wc -l)
    echo "🔔 $NEW_COUNT 条新评论："
    for rid in $NEW_IDS; do
      echo "$RAW" | jq -r --arg rid "$rid" '
        .data[] | select(.id == $rid) |
        "─ ★\(.attributes.rating) [\(.attributes.territory)] \(.attributes.createdDate[:10]) by \(.attributes.reviewerNickname)\n  \(.attributes.title)\n  \(.attributes.body)"
      '
    done
  else
    echo "（无新评论）"
  fi

  # 更新快照
  echo "$IDS" > "$SNAPSHOT"
done

echo ""
echo "下次运行只显示新评论。重置 baseline: rm $SNAPSHOT_DIR/<app>.txt"
