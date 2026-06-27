#!/usr/bin/env bash
# 起/复用一个 CDP 受控 Chrome(独立 profile,端口 9222),供本机自主取信息。
# 首次:跑本脚本 → 在弹出的 Chrome 里登 Outlook(sh1990914@hotmail.com)+ ASC(过 2FA)。
# 之后登录态存 profile,Claude 用 cdp_fetch.py / cdp_readmail.py 全自主抓。
set -uo pipefail
PROFILE="$HOME/.autoapp-cdp-profile"; PORT=9222
if curl -s "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
  echo "[cdp] 已在运行(:$PORT)"; exit 0
fi
mkdir -p "$PROFILE"
nohup "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  "--remote-debugging-port=$PORT" "--user-data-dir=$PROFILE" \
  "--remote-allow-origins=*" "--no-first-run" "--no-default-browser-check" \
  "about:blank" >/tmp/cdp_chrome.log 2>&1 &
for i in $(seq 1 10); do curl -s "http://localhost:$PORT/json/version" >/dev/null 2>&1 && break; perl -e 'select(undef,undef,undef,1)'; done
curl -s "http://localhost:$PORT/json/version" >/dev/null 2>&1 && echo "[cdp] 已启动 :$PORT (profile=$PROFILE)" || echo "[cdp] 启动失败,看 /tmp/cdp_chrome.log"
