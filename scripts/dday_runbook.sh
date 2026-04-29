#!/usr/bin/env bash
# dday_runbook.sh
# 上线日单 App 用。给定 slug 和 App Store URL，把 reports/launch-marketing-*.md 模板里
# 该 App 的所有平台稿件 placeholder 替换为真实链接，输出一份 reports/launch-day-<slug>-<date>.md
# 让用户复制粘贴即可发各平台。
#
# Usage:
#   bash orchestrator/dday_runbook.sh autochoice https://apps.apple.com/app/id1234567890
#   bash orchestrator/dday_runbook.sh altitudenow https://apps.apple.com/app/id... [--also-zh]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

SLUG="${1:-}"
STORE_URL="${2:-}"

if [ -z "$SLUG" ] || [ -z "$STORE_URL" ]; then
    cat <<EOF
Usage: bash $0 <slug> <App Store URL> [--also-zh]

slug: autochoice | altitudenow | daysuntil | promptvault
EOF
    exit 1
fi

# Map slug → display info
case "$SLUG" in
    autochoice)
        DISPLAY_NAME="AutoChoice"
        REPO="autoapp-hello"
        APP_ID_GUESS=$(echo "$STORE_URL" | sed -n 's|.*/id\([0-9]\+\).*|\1|p')
        ;;
    altitudenow)
        DISPLAY_NAME="AltitudeNow"
        REPO="autoapp-altitude-now"
        APP_ID_GUESS=$(echo "$STORE_URL" | sed -n 's|.*/id\([0-9]\+\).*|\1|p')
        ;;
    daysuntil)
        DISPLAY_NAME="DaysUntil"
        REPO="autoapp-days-until"
        APP_ID_GUESS=$(echo "$STORE_URL" | sed -n 's|.*/id\([0-9]\+\).*|\1|p')
        ;;
    promptvault)
        DISPLAY_NAME="PromptVault"
        REPO="autoapp-prompt-vault"
        APP_ID_GUESS=$(echo "$STORE_URL" | sed -n 's|.*/id\([0-9]\+\).*|\1|p')
        ;;
    *)
        echo "Unknown slug: $SLUG" >&2
        exit 1
        ;;
esac

DATE=$(date -u +%Y-%m-%d)
OUT="$REPO_ROOT/reports/launch-day-${SLUG}-${DATE}.md"

mkdir -p "$REPO_ROOT/reports"

cat > "$OUT" <<MD
# $DISPLAY_NAME 上线日 — $DATE

**App Store**: $STORE_URL
**Source**: https://github.com/jiejuefuyou/$REPO

---

## 发布顺序与时间（建议）

按时区压时段最大化第一波曝光：

| 时间（北京时间） | 动作 | 平台 | 备注 |
|---|---|---|---|
| 上午 9:00 | 推 r/iOSProgramming "Show & Tell" | Reddit | 美东 21:00 — 美区夜间高峰 |
| 上午 10:00 | 推 Hacker News "Show HN" | HN | 美东 22:00 — 主榜上排候选时段 |
| 中午 12:00 | 上 Product Hunt（提交，标 "Launch today"） | PH | 美西 21:00 当日截止前 |
| 下午 14:00 | 即刻发首发动态 | 即刻 | 国内午后高峰 |
| 下午 16:00 | 小红书发图文笔记 | 小红书 | 国内通勤前高峰 |
| 晚上 20:00 | Twitter/X 长串 thread | Twitter | 跨时区两端都活跃 |
| 晚上 22:00 | 公众号文章 | 微信 | 国内晚间收尾 |

每个平台**间隔至少 1 小时**避免被识别成机器人。**Reddit 和 HN 不要交叉提同一个链接**（HN 不喜欢同时多平台 self-promotion）。

---

## ⬇️ 复制即用稿件 ⬇️

> 下面每段都是替换好链接的最终版，直接 copy-paste。

MD

# Append per-platform sections from the templates, with placeholders filled.
EN_TEMPLATE="$REPO_ROOT/reports/launch-marketing-drafts.md"
ZH_TEMPLATE="$REPO_ROOT/reports/launch-marketing-zh.md"

if [ -f "$EN_TEMPLATE" ]; then
    cat >> "$OUT" <<MD

## 英文版

> 引用自 \`reports/launch-marketing-drafts.md\` 该 App 段落，已替换链接

MD
    # 提取 displayed name 段并替换 [link] [ID]
    awk -v app="$DISPLAY_NAME" '
        BEGIN { in_section = 0 }
        /^## / {
            if ($0 ~ app) { in_section = 1; print; next }
            else if (in_section) { exit }
        }
        in_section { print }
    ' "$EN_TEMPLATE" \
    | sed "s|\\[link\\]|$STORE_URL|g; s|\\[ID\\]|$APP_ID_GUESS|g" \
    >> "$OUT"
fi

if [ -f "$ZH_TEMPLATE" ]; then
    cat >> "$OUT" <<MD

---

## 中文版

> 引用自 \`reports/launch-marketing-zh.md\` 该 App 段落，已替换链接

MD
    awk -v app="$DISPLAY_NAME" '
        BEGIN { in_section = 0 }
        /^## / {
            if ($0 ~ app) { in_section = 1; print; next }
            else if (in_section) { exit }
        }
        in_section { print }
    ' "$ZH_TEMPLATE" \
    | sed "s|\\[link\\]|$STORE_URL|g; s|\\[ID\\]|$APP_ID_GUESS|g" \
    >> "$OUT"
fi

cat >> "$OUT" <<MD

---

## 上线后 24 小时检查清单（CC 自跑）

- [ ] App Store 实际可搜索（按 keywords 搜，看排位）
- [ ] HN 帖未被 flag（front page 仍可见）
- [ ] Reddit 帖未被自动删（通常 sub 反 self-promotion 严，用 archive.org 备份链接以防）
- [ ] PH "today" 列表里能看到（首页 daily ranking）
- [ ] 收第一条 review（鼓励 TestFlight 测试者去 App Store 留 5 星）
- [ ] 跑 \`orchestrator/asc_reviews_check.sh\`（baseline reviews）
- [ ] 跑 \`orchestrator/asc_sales_report.sh\`（24h 后才有完整数据，48h 后再跑）
- [ ] 截图保存：HN ranking、PH ranking、App Store rank — 用作后续帖子素材

## 7 天复盘节点

- [ ] T+24h: 拉 ASC sales 数据；如转化率 < 1%，写 review 复盘文章发 reddit
- [ ] T+3d: HN 帖如已沉，准备一篇 deep-dive 技术博客（"how I shipped X" 角度）发 dev.to + 知乎
- [ ] T+7d: 第一周收入 + 用户数 + 反馈汇总，决定下一款 App 节奏（按 \`reports/aso-baseline-2026-04-29.md\` 30 天判断标准）

---

_自动生成: \`orchestrator/dday_runbook.sh $SLUG $STORE_URL\` · ${DATE}_
MD

echo "✅ Written: $OUT"
echo ""
echo "下一步："
echo "  1. cat $OUT  # 浏览检查"
echo "  2. 按时间表分时段贴各平台"
echo "  3. 24h 后跑 'orchestrator/asc_sales_report.sh'（需 ASC API key 已配）"
