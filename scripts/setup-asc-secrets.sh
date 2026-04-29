#!/usr/bin/env bash
# setup-asc-secrets.sh
# 当 Apple Developer email 到来 + ASC API Key 到手后，CC 跑这个脚本一次性把 3 个 App
# 仓的 GitHub Secrets 全配齐。所有 secrets 在 testflight environment 下。
#
# Usage:
#   1. 把 .p8 内容存到本地文件，比如 ~/AuthKey_ABCD123456.p8
#   2. 编辑下面的 INPUTS 段填入 Key ID / Issuer ID / Team ID / ITC Team ID / .p8 路径 / GitHub PAT
#   3. bash orchestrator/setup-asc-secrets.sh
#
# 此脚本是 idempotent 的：重复运行只会覆盖现有 secrets，不会失败。

set -euo pipefail

# ───────── INPUTS（运行前必须填）─────────
ASC_KEY_ID="${ASC_KEY_ID:-}"                # Apple ASC API Key ID（10 字符）
ASC_ISSUER_ID="${ASC_ISSUER_ID:-}"          # ASC API Issuer ID（页顶 UUID）
ASC_KEY_FILE="${ASC_KEY_FILE:-$HOME/AuthKey.p8}"  # .p8 文件本地路径
TEAM_ID="${TEAM_ID:-}"                      # ASC Membership 页 Team ID
ITC_TEAM_ID="${ITC_TEAM_ID:-}"              # 数字，第一次跑 fastlane 会打印；可先空，bootstrap 后回填
GH_PAT="${GH_PAT:-}"                        # 用户 GitHub PAT，scope: repo（用于 match 推 autoapp-certs）
FASTLANE_USER="${FASTLANE_USER:-sh1990914@hotmail.com}"
# ───────────────────────────────────────

REPOS=(autoapp-hello autoapp-altitude-now autoapp-days-until)
OWNER="jiejuefuyou"

# ── Validate ──
for var in ASC_KEY_ID ASC_ISSUER_ID TEAM_ID GH_PAT; do
  if [ -z "${!var}" ]; then
    echo "ERROR: $var 未设置。编辑脚本顶部 INPUTS 段或 export 环境变量。" >&2
    exit 1
  fi
done

if [ ! -f "$ASC_KEY_FILE" ]; then
  echo "ERROR: ASC_KEY_FILE 文件不存在: $ASC_KEY_FILE" >&2
  exit 1
fi

# ── Generate / encode shared values ──
ASC_KEY_BASE64=$(base64 -w 0 "$ASC_KEY_FILE" 2>/dev/null || base64 < "$ASC_KEY_FILE" | tr -d '\n')

# MATCH_PASSWORD：32 字节随机；只在第一次跑时生成。后续从某 repo 已存在的 secret 读不出，
# 所以脚本要么写：第一次生成 + 写本地文件，后续从本地文件读。
MATCH_PWD_FILE="$HOME/.autoapp-match-password"
if [ ! -f "$MATCH_PWD_FILE" ]; then
  openssl rand -base64 32 > "$MATCH_PWD_FILE"
  chmod 600 "$MATCH_PWD_FILE"
  echo "[gen] MATCH_PASSWORD 已生成并保存到 $MATCH_PWD_FILE （chmod 600）"
fi
MATCH_PASSWORD=$(cat "$MATCH_PWD_FILE")

# MATCH_GIT_BASIC_AUTHORIZATION = base64("user:PAT")，HTTPS auth header 格式
MATCH_GIT_AUTH=$(printf '%s:%s' "$OWNER" "$GH_PAT" | base64 | tr -d '\n')

# ── Helper ──
set_secret() {
  local repo=$1 name=$2 value=$3
  printf '%s' "$value" | gh secret set "$name" -R "$OWNER/$repo" --env testflight --body -
}

# ── Per-repo loop ──
for repo in "${REPOS[@]}"; do
  echo ""
  echo "═══ $repo ═══"

  # Ensure testflight environment exists
  gh api -X PUT "repos/$OWNER/$repo/environments/testflight" >/dev/null
  echo "[env] testflight ensured"

  # Set secrets
  set_secret "$repo" ASC_KEY_ID                    "$ASC_KEY_ID"
  set_secret "$repo" ASC_ISSUER_ID                 "$ASC_ISSUER_ID"
  set_secret "$repo" ASC_KEY_CONTENT               "$ASC_KEY_BASE64"
  set_secret "$repo" MATCH_PASSWORD                "$MATCH_PASSWORD"
  set_secret "$repo" MATCH_GIT_BASIC_AUTHORIZATION "$MATCH_GIT_AUTH"
  set_secret "$repo" FASTLANE_USER                 "$FASTLANE_USER"
  set_secret "$repo" TEAM_ID                       "$TEAM_ID"
  if [ -n "$ITC_TEAM_ID" ]; then
    set_secret "$repo" ITC_TEAM_ID "$ITC_TEAM_ID"
  fi

  echo "[ok] $repo testflight secrets 已配齐"
done

echo ""
echo "═══ 完成 ═══"
echo "下一步："
echo "  1. 触发 init_signing.yml 在每个仓（一次性 bootstrap）："
for r in "${REPOS[@]}"; do
  echo "     gh workflow run init_signing.yml -R $OWNER/$r -f type=appstore"
done
echo "  2. 等约 10 分钟，确认 autoapp-certs 仓里出现加密的 cert + profile。"
echo "  3. 推 v0.1.0 tag 到 autoapp-hello 触发 TestFlight build："
echo "     cd repos/autoapp-hello && git tag v0.1.0 && git push origin v0.1.0"
echo ""
echo "ITC_TEAM_ID 如未填：第一次 init_signing 跑完后查 fastlane log，回头 export 后重跑此脚本。"
