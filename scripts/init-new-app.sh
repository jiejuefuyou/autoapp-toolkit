#!/usr/bin/env bash
# init-new-app.sh
# Bootstrap a new iOS app repo from one of your existing apps as a template.
# Replaces all DisplayName / displayname / display-name occurrences with the new
# app's name; renames directories; clones the .github/workflows + fastlane setup.
#
# Usage:
#   bash scripts/init-new-app.sh <template-app-dir> <new-app-slug> <new-display-name> <new-bundle-id>
#
# Example:
#   bash scripts/init-new-app.sh ../repos/autoapp-hello prompt-vault PromptVault com.jiejuefuyou.promptvault

set -euo pipefail

TEMPLATE_DIR="${1:?usage: $0 <template-dir> <slug> <DisplayName> <bundle-id>}"
NEW_SLUG="${2:?need slug (e.g. prompt-vault)}"
NEW_NAME="${3:?need DisplayName (e.g. PromptVault)}"
NEW_BUNDLE="${4:?need bundle id (e.g. com.example.promptvault)}"

if [ ! -d "$TEMPLATE_DIR" ]; then
    echo "Template dir doesn't exist: $TEMPLATE_DIR" >&2
    exit 1
fi

# Discover template's display name + bundle (look in project.yml)
PROJECT_YML="$TEMPLATE_DIR/project.yml"
OLD_NAME=$(grep -E '^name:' "$PROJECT_YML" | head -1 | sed 's/name: *//')
OLD_BUNDLE=$(grep -E 'PRODUCT_BUNDLE_IDENTIFIER:' "$PROJECT_YML" | head -1 | sed 's/.*PRODUCT_BUNDLE_IDENTIFIER: *//' | tr -d ' ')
OLD_LOWER=$(echo "$OLD_NAME" | tr '[:upper:]' '[:lower:]')
OLD_KEBAB=$(echo "$OLD_NAME" | sed 's/\([a-z0-9]\)\([A-Z]\)/\1-\2/g' | tr '[:upper:]' '[:lower:]')

NEW_LOWER=$(echo "$NEW_NAME" | tr '[:upper:]' '[:lower:]')
NEW_KEBAB="$NEW_SLUG"

echo "Template: $TEMPLATE_DIR"
echo "  $OLD_NAME → $NEW_NAME"
echo "  $OLD_LOWER → $NEW_LOWER"
echo "  $OLD_KEBAB → $NEW_KEBAB"
echo "  $OLD_BUNDLE → $NEW_BUNDLE"

DEST_DIR="$(dirname "$TEMPLATE_DIR")/autoapp-$NEW_SLUG"
if [ -e "$DEST_DIR" ]; then
    echo "Destination already exists: $DEST_DIR" >&2
    exit 1
fi

# 1. Copy
cp -r "$TEMPLATE_DIR" "$DEST_DIR"
rm -rf "$DEST_DIR/.git"
rm -rf "$DEST_DIR/fastlane/screenshots"/* 2>/dev/null || true

# 2. Rename directories
for sub in "$DEST_DIR/$OLD_NAME" "$DEST_DIR/${OLD_NAME}Tests" "$DEST_DIR/${OLD_NAME}UITests"; do
    if [ -d "$sub" ]; then
        new_sub=$(echo "$sub" | sed "s/$OLD_NAME/$NEW_NAME/g")
        mv "$sub" "$new_sub"
    fi
done

# 3. Replace text inside files
find "$DEST_DIR" -type f \( -name "*.swift" -o -name "*.yml" -o -name "*.yaml" -o -name "*.rb" -o -name "*.md" -o -name "*.txt" -o -name "*.json" -o -name "*.sh" -o -name "*.plist" -o -name "*.storekit" -o -name "Fastfile" -o -name "Appfile" -o -name "Matchfile" -o -name "Snapfile" \) -print0 \
| while IFS= read -r -d '' f; do
    if grep -qE "$OLD_NAME|$OLD_LOWER|$OLD_KEBAB|$OLD_BUNDLE" "$f" 2>/dev/null; then
        # macOS sed needs '' after -i; Linux sed doesn't
        if [[ "$(uname)" == "Darwin" ]]; then
            sed -i '' "s|$OLD_BUNDLE|$NEW_BUNDLE|g; s|$OLD_NAME|$NEW_NAME|g; s|$OLD_LOWER|$NEW_LOWER|g; s|$OLD_KEBAB|$NEW_KEBAB|g" "$f"
        else
            sed -i "s|$OLD_BUNDLE|$NEW_BUNDLE|g; s|$OLD_NAME|$NEW_NAME|g; s|$OLD_LOWER|$NEW_LOWER|g; s|$OLD_KEBAB|$NEW_KEBAB|g" "$f"
        fi
    fi
done

# 4. Rename files that have the old name in their filename
find "$DEST_DIR" -type f -name "*$OLD_NAME*" | while read f; do
    nf=$(echo "$f" | sed "s/$OLD_NAME/$NEW_NAME/g")
    mv "$f" "$nf"
done

# 5. Init git
cd "$DEST_DIR"
git init -b main >/dev/null
git add . >/dev/null

echo ""
echo "✅ Bootstrapped at $DEST_DIR"
echo ""
echo "Next steps:"
echo "  1. cd $DEST_DIR"
echo "  2. Edit fastlane/metadata/en-US/{description,subtitle,keywords,name}.txt for the new product"
echo "  3. Edit PromptVault/Models/Models.swift (or equivalent) — replace template domain logic"
echo "  4. git commit -m 'feat: scaffold from autoapp-toolkit'"
echo "  5. gh repo create your-username/autoapp-$NEW_SLUG --public --source=. --push"
