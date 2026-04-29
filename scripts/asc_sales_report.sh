#!/usr/bin/env bash
# asc_sales_report.sh
# 拉 App Store Connect 每日销售/下载汇总，附在每日报告里。
# 需要 ASC API Key 到位（见 setup-asc-secrets.sh）。
#
# Usage:
#   export ASC_KEY_ID=...
#   export ASC_ISSUER_ID=...
#   export ASC_KEY_FILE=$HOME/AuthKey.p8
#   export VENDOR_NUMBER=...   # ASC > Payments and Financial Reports > Vendor Number
#   bash orchestrator/asc_sales_report.sh [YYYY-MM-DD]
#
# 输出：reports/sales-YYYY-MM-DD.tsv（Apple 原始 TSV）
# 备注：销售数据有 24-48 小时延迟。今天的报告通常要到第 2 天才能拉到完整数据。

set -euo pipefail

REPORT_DATE="${1:-$(date -u -d 'yesterday' +%Y-%m-%d 2>/dev/null || date -u -v-1d +%Y-%m-%d)}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$REPO_ROOT/reports"
OUT_TSV="$OUT_DIR/sales-$REPORT_DATE.tsv"

# Validate inputs
for var in ASC_KEY_ID ASC_ISSUER_ID ASC_KEY_FILE VENDOR_NUMBER; do
  if [ -z "${!var:-}" ]; then
    echo "ERROR: $var 未设置。先 export 这 4 个变量。" >&2
    exit 1
  fi
done

if [ ! -f "$ASC_KEY_FILE" ]; then
  echo "ERROR: $ASC_KEY_FILE 不存在" >&2
  exit 1
fi

# ── Generate JWT (ASC API 用 ES256 签名 JWT，有效期 ≤ 20 min）──
NOW=$(date +%s)
EXP=$((NOW + 1200))

HEADER=$(printf '{"alg":"ES256","kid":"%s","typ":"JWT"}' "$ASC_KEY_ID" \
  | openssl base64 -A | tr -d '=' | tr '/+' '_-')
PAYLOAD=$(printf '{"iss":"%s","exp":%d,"aud":"appstoreconnect-v1"}' "$ASC_ISSUER_ID" "$EXP" \
  | openssl base64 -A | tr -d '=' | tr '/+' '_-')
SIGNATURE=$(printf '%s.%s' "$HEADER" "$PAYLOAD" \
  | openssl dgst -sha256 -sign "$ASC_KEY_FILE" -binary \
  | openssl base64 -A | tr -d '=' | tr '/+' '_-')
JWT="$HEADER.$PAYLOAD.$SIGNATURE"

# ── Fetch the Daily Sales Report ──
mkdir -p "$OUT_DIR"

URL="https://api.appstoreconnect.apple.com/v1/salesReports?\
filter[frequency]=DAILY&\
filter[reportDate]=$REPORT_DATE&\
filter[reportSubType]=SUMMARY&\
filter[reportType]=SALES&\
filter[vendorNumber]=$VENDOR_NUMBER&\
filter[version]=1_0"

HTTP_CODE=$(curl -s -o "$OUT_TSV.gz" -w "%{http_code}" \
  -H "Authorization: Bearer $JWT" \
  -H "Accept: application/a-gzip" \
  "$URL")

if [ "$HTTP_CODE" != "200" ]; then
  echo "ERROR: ASC API 返回 $HTTP_CODE"
  cat "$OUT_TSV.gz" 2>/dev/null
  rm -f "$OUT_TSV.gz"
  exit 1
fi

gunzip -f "$OUT_TSV.gz"
echo "[ok] 写入 $OUT_TSV"

# ── 解析关键指标 ──
echo ""
echo "═══ $REPORT_DATE 摘要 ═══"
# Apple 的 TSV 字段顺序见 https://help.apple.com/app-store-connect/#/dev3a16f24fa
# 列：Provider | Provider Country | SKU | Developer | Title | Version | Product Type Identifier
#    | Units | Developer Proceeds | Begin Date | End Date | Customer Currency | Country Code
#    | Currency of Proceeds | Apple Identifier | Customer Price | Promo Code | Parent Identifier
#    | Subscription | Period | Category | CMB | Device | Supported Platforms | Proceeds Reason
#    | Preserved Pricing | Client | Order Type
awk -F'\t' 'NR>1 {
  units[$5][$7] += $8
  proceeds[$5][$7] += $9
}
END {
  printf "%-20s %-4s %6s %12s\n", "App", "PTI", "Units", "Proceeds(USD)"
  for (app in units) {
    for (pti in units[app]) {
      printf "%-20s %-4s %6d %12.2f\n", app, pti, units[app][pti], proceeds[app][pti]
    }
  }
}' "$OUT_TSV"

# Product Type Identifier (PTI) 速记：
#   1   - iPhone/iPad app, free download
#   1F  - iPhone app, paid
#   IA1 - In-app purchase (one-time, non-consumable) ← 我们的 IAP
#   IAY - Auto-renewable subscription (我们不用)
echo ""
echo "PTI 速记：1=免费下载  IA1=我们的一次性 IAP"
