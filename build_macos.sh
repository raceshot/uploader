#!/bin/bash
# macOS 打包腳本

set -e

echo "=========================================="
echo "  運動拍檔 Raceshot 上傳工具 - macOS 打包"
echo "=========================================="
echo ""

# 檢查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 找不到 python3"
    exit 1
fi

# 檢查虛擬環境
if [ ! -d ".venv" ]; then
    echo "📦 建立虛擬環境..."
    python3 -m venv .venv
fi

echo "🔧 啟用虛擬環境..."
source .venv/bin/activate

# 安裝依賴
echo "📥 安裝依賴套件..."
pip install --upgrade pip
pip install -r requirements.txt

# 清理舊檔案
echo "🧹 清理舊的打包檔案..."
rm -rf build dist

# 執行打包
echo "🔨 開始打包..."
pyinstaller raceshot_uploader.spec --clean

# 檢查結果
if [ -d "dist/運動拍檔上傳工具.app" ]; then
    echo ""
    echo "✅ 打包成功！"
    echo "📁 應用程式位置：dist/運動拍檔上傳工具.app"
    echo ""
    echo "📦 檔案大小："
    du -sh "dist/運動拍檔上傳工具.app"
    echo ""
    echo "🧪 測試執行："
    echo "  open dist/運動拍檔上傳工具.app"
    echo ""
    echo "📤 發佈建議："
    echo "  1. 壓縮成 zip："
    echo "     cd dist && zip -r 運動拍檔上傳工具-macOS.zip 運動拍檔上傳工具.app"
    echo "  2. 或建立 DMG 安裝檔（需要額外工具）"
else
    echo "❌ 打包失敗"
    exit 1
fi
