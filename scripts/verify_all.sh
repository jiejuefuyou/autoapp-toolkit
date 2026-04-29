#!/usr/bin/env bash
# verify_all.sh
# 跨 4 仓的 pre-launch 自动巡检。每条 ASC 硬要求都 check，输出彩色 OK/FAIL 表。
# 在用户拿到 Apple email 之前，CC 自己定期跑这个确保没坏的项；
# 在 ASC API key 配好后，跑这个一次确保「能跑 testflight」。
#
# Usage: bash orchestrator/verify_all.sh

set -uo pipefail   # 注意 -e 没开：单条 fail 不退出，全部跑完才汇总

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPOS=(autoapp-hello autoapp-altitude-now autoapp-days-until autoapp-prompt-vault)

GREEN="\033[32m"
RED="\033[31m"
YELLOW="\033[33m"
BLUE="\033[34m"
NC="\033[0m"

declare -A LOCAL_DIR=(
    [autoapp-hello]="AutoChoice"
    [autoapp-altitude-now]="AltitudeNow"
    [autoapp-days-until]="DaysUntil"
    [autoapp-prompt-vault]="PromptVault"
)
declare -A BUNDLE_ID=(
    [autoapp-hello]="com.jiejuefuyou.autochoice"
    [autoapp-altitude-now]="com.jiejuefuyou.altitudenow"
    [autoapp-days-until]="com.jiejuefuyou.daysuntil"
    [autoapp-prompt-vault]="com.jiejuefuyou.promptvault"
)

total_pass=0
total_fail=0
total_warn=0

check() {
    local repo=$1 desc=$2 cmd=$3
    if eval "$cmd" >/dev/null 2>&1; then
        printf "  ${GREEN}✓${NC} %s\n" "$desc"
        total_pass=$((total_pass + 1))
    else
        printf "  ${RED}✗${NC} %s\n" "$desc"
        total_fail=$((total_fail + 1))
    fi
}

warn_check() {
    local repo=$1 desc=$2 cmd=$3
    if eval "$cmd" >/dev/null 2>&1; then
        printf "  ${GREEN}✓${NC} %s\n" "$desc"
        total_pass=$((total_pass + 1))
    else
        printf "  ${YELLOW}!${NC} %s\n" "$desc"
        total_warn=$((total_warn + 1))
    fi
}

for repo in "${REPOS[@]}"; do
    dir="$REPO_ROOT/repos/$repo"
    app_dir="${LOCAL_DIR[$repo]}"
    bid="${BUNDLE_ID[$repo]}"
    printf "\n${BLUE}═══ %s (%s) ═══${NC}\n" "$repo" "$bid"

    if [ ! -d "$dir" ]; then
        printf "  ${RED}✗${NC} repo dir missing: %s\n" "$dir"
        total_fail=$((total_fail + 1))
        continue
    fi

    # ── Code health ──
    check "$repo" "project.yml exists"                            "[ -f '$dir/project.yml' ]"
    check "$repo" "ITSAppUsesNonExemptEncryption=NO declared"     "grep -q 'INFOPLIST_KEY_ITSAppUsesNonExemptEncryption.*NO' '$dir/project.yml'"
    check "$repo" "Bundle ID matches expected"                    "grep -q 'PRODUCT_BUNDLE_IDENTIFIER: $bid' '$dir/project.yml'"
    check "$repo" "PrivacyInfo.xcprivacy exists"                  "[ -f '$dir/$app_dir/Resources/PrivacyInfo.xcprivacy' ]"
    check "$repo" "App icon PNG exists"                           "[ -f '$dir/$app_dir/Resources/Assets.xcassets/AppIcon.appiconset/icon.png' ]"
    check "$repo" "StoreKit config exists"                        "[ -f '$dir/$app_dir/Resources/StoreKitConfiguration.storekit' ]"

    # ── Fastlane configs ──
    check "$repo" "fastlane Fastfile exists"                      "[ -f '$dir/fastlane/Fastfile' ]"
    check "$repo" "fastlane Appfile bundle id correct"            "grep -q '$bid' '$dir/fastlane/Appfile'"
    check "$repo" "fastlane Matchfile bundle id correct"          "grep -q '$bid' '$dir/fastlane/Matchfile'"

    # ── Metadata ──
    check "$repo" "en-US name.txt non-empty"                      "[ -s '$dir/fastlane/metadata/en-US/name.txt' ]"
    check "$repo" "en-US subtitle.txt non-empty"                  "[ -s '$dir/fastlane/metadata/en-US/subtitle.txt' ]"
    check "$repo" "en-US description.txt non-empty"               "[ -s '$dir/fastlane/metadata/en-US/description.txt' ]"
    check "$repo" "en-US keywords.txt within 100 chars"           "[ \$(printf '%s' \"\$(cat '$dir/fastlane/metadata/en-US/keywords.txt')\" | wc -c) -le 100 ]"
    check "$repo" "en-US release_notes.txt non-empty"             "[ -s '$dir/fastlane/metadata/en-US/release_notes.txt' ]"
    check "$repo" "en-US privacy_url.txt non-empty"               "[ -s '$dir/fastlane/metadata/en-US/privacy_url.txt' ]"
    check "$repo" "en-US support_url.txt non-empty"               "[ -s '$dir/fastlane/metadata/en-US/support_url.txt' ]"
    check "$repo" "primary_category.txt non-empty"                "[ -s '$dir/fastlane/metadata/primary_category.txt' ]"
    check "$repo" "copyright.txt non-empty"                       "[ -s '$dir/fastlane/metadata/copyright.txt' ]"

    # ── zh-Hans (warn-level: nice-to-have not required) ──
    warn_check "$repo" "zh-Hans description.txt present"          "[ -s '$dir/fastlane/metadata/zh-Hans/description.txt' ]"
    warn_check "$repo" "zh-Hans name.txt present"                 "[ -s '$dir/fastlane/metadata/zh-Hans/name.txt' ]"

    # ── CI workflows ──
    check "$repo" "CI workflow exists"                            "[ -f '$dir/.github/workflows/ci.yml' ]"
    check "$repo" "TestFlight workflow exists"                    "[ -f '$dir/.github/workflows/testflight.yml' ]"
    check "$repo" "init_signing workflow exists"                  "[ -f '$dir/.github/workflows/init_signing.yml' ]"
    check "$repo" "release workflow exists"                       "[ -f '$dir/.github/workflows/release.yml' ]"
    check "$repo" "screenshots workflow exists"                   "[ -f '$dir/.github/workflows/screenshots.yml' ]"
    check "$repo" "monitor workflow exists"                       "[ -f '$dir/.github/workflows/monitor.yml' ]"

    # ── Lint scripts ──
    check "$repo" "lint-metadata.sh exists"                       "[ -f '$dir/scripts/lint-metadata.sh' ]"
    check "$repo" "lint-metadata.sh passes"                       "(cd '$dir' && bash scripts/lint-metadata.sh)"

    # ── Screenshots (warn — auto-regenerated) ──
    warn_check "$repo" "en-US screenshots present"                "ls '$dir/fastlane/screenshots/en-US/'*.png 2>/dev/null | head -1 > /dev/null"

    # ── Latest CI status ──
    if command -v gh >/dev/null 2>&1; then
        latest=$(gh run list -R "jiejuefuyou/$repo" --limit 1 --json conclusion,status --jq '.[0] | "\(.status)/\(.conclusion // "running")"' 2>/dev/null || echo "?")
        if [[ "$latest" == "completed/success" ]]; then
            printf "  ${GREEN}✓${NC} latest CI: %s\n" "$latest"
            total_pass=$((total_pass + 1))
        elif [[ "$latest" == *"in_progress"* ]] || [[ "$latest" == *"queued"* ]] || [[ "$latest" == *"pending"* ]] || [[ "$latest" == *"running"* ]]; then
            printf "  ${YELLOW}!${NC} latest CI: %s\n" "$latest"
            total_warn=$((total_warn + 1))
        elif [[ "$latest" == "completed/failure" ]]; then
            printf "  ${RED}✗${NC} latest CI: %s\n" "$latest"
            total_fail=$((total_fail + 1))
        else
            # cancelled, skipped, etc. — soft warn
            printf "  ${YELLOW}!${NC} latest CI: %s\n" "$latest"
            total_warn=$((total_warn + 1))
        fi
    fi
done

# ── Cross-cutting concerns ──
printf "\n${BLUE}═══ Cross-cutting ═══${NC}\n"
check "" "setup-asc-secrets.sh present"                          "[ -f '$REPO_ROOT/orchestrator/setup-asc-secrets.sh' ]"
check "" "asc_sales_report.sh present"                           "[ -f '$REPO_ROOT/orchestrator/asc_sales_report.sh' ]"
check "" "asc_reviews_check.sh present"                          "[ -f '$REPO_ROOT/orchestrator/asc_reviews_check.sh' ]"
check "" "next-actions.md present"                               "[ -f '$REPO_ROOT/reports/next-actions.md' ]"
check "" "pre-launch-checklist.md present"                       "[ -f '$REPO_ROOT/reports/pre-launch-checklist.md' ]"
check "" "rejection-response-templates.md present"               "[ -f '$REPO_ROOT/reports/rejection-response-templates.md' ]"
check "" "launch-marketing-drafts.md present"                    "[ -f '$REPO_ROOT/reports/launch-marketing-drafts.md' ]"
check "" "launch-marketing-zh.md present"                        "[ -f '$REPO_ROOT/reports/launch-marketing-zh.md' ]"

# ── Summary ──
printf "\n${BLUE}═══ Summary ═══${NC}\n"
printf "  ${GREEN}PASS${NC} %d   ${YELLOW}WARN${NC} %d   ${RED}FAIL${NC} %d\n" \
    "$total_pass" "$total_warn" "$total_fail"

if [ "$total_fail" -gt 0 ]; then
    printf "${RED}❌ %d hard checks failed — fix before tagging v0.1.0.${NC}\n" "$total_fail"
    exit 1
fi
if [ "$total_warn" -gt 0 ]; then
    printf "${YELLOW}⚠️  %d soft warnings — review before submission.${NC}\n" "$total_warn"
fi
printf "${GREEN}✅ All hard requirements met.${NC}\n"
exit 0
