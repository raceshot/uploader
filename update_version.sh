#!/bin/bash

# 版本更新腳本
# 使用方式: ./update_version.sh patch|minor|major

if [ -z "$1" ]; then
    echo "使用方式: $0 patch|minor|major"
    echo "例如: $0 patch  # 1.0.0 -> 1.0.1"
    echo "例如: $0 minor  # 1.0.0 -> 1.1.0"
    echo "例如: $0 major  # 1.0.0 -> 2.0.0"
    exit 1
fi

# 讀取當前版本
if [ ! -f VERSION ]; then
    echo "ERROR: VERSION file not found"
    exit 1
fi

CURRENT_VERSION=$(cat VERSION | tr -d '\n')
echo "當前版本: $CURRENT_VERSION"

# 解析版本號
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"

# 更新版本號
case "$1" in
    patch)
        PATCH=$((PATCH + 1))
        ;;
    minor)
        MINOR=$((MINOR + 1))
        PATCH=0
        ;;
    major)
        MAJOR=$((MAJOR + 1))
        MINOR=0
        PATCH=0
        ;;
    *)
        echo "ERROR: 無效的版本類型。使用 patch|minor|major"
        exit 1
        ;;
esac

NEW_VERSION="$MAJOR.$MINOR.$PATCH"
echo "新版本: $NEW_VERSION"

# 更新 VERSION 檔案
echo "$NEW_VERSION" > VERSION

# 提交更改
git add VERSION
git commit -m "chore: bump version to $NEW_VERSION"

# 建立 tag
git tag -a "v$NEW_VERSION" -m "Release version $NEW_VERSION"

echo "✅ 版本已更新到 $NEW_VERSION"
echo "📌 Tag 已建立: v$NEW_VERSION"
echo "🚀 執行 'git push origin main --tags' 以推送到 GitHub"
