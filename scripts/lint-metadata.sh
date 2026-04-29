#!/usr/bin/env bash
# lint-metadata.sh
# 校验 fastlane/metadata 各文件长度不超 ASC 限制。
# 上线前 fastlane deliver 会做这事，但 CI 提早拦住可省一次失败的 release run。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
META_ROOT="$REPO_ROOT/fastlane/metadata"

# Apple ASC 限制（字符数）
declare -A LIMITS=(
  [name.txt]=30
  [subtitle.txt]=30
  [keywords.txt]=100
  [promotional_text.txt]=170
  [description.txt]=4000
  [release_notes.txt]=4000
  [privacy_url.txt]=255
  [support_url.txt]=255
  [marketing_url.txt]=255
)

FAILED=0
for locale_dir in "$META_ROOT"/*/; do
  [ -d "$locale_dir" ] || continue
  locale=$(basename "$locale_dir")
  for file in name subtitle keywords promotional_text description release_notes privacy_url support_url marketing_url; do
    fpath="$locale_dir$file.txt"
    [ -f "$fpath" ] || continue
    limit="${LIMITS[$file.txt]}"
    # trim trailing newline + count chars
    actual=$(printf '%s' "$(cat "$fpath")" | wc -m | tr -d ' ')
    if [ "$actual" -gt "$limit" ]; then
      echo "❌ $locale/$file.txt: $actual chars > $limit limit"
      FAILED=1
    fi
  done
done

if [ "$FAILED" -eq 0 ]; then
  echo "✅ All ASC metadata within limits."
fi

exit "$FAILED"
