@echo off
REM Windows 打包腳本
chcp 65001 >nul

echo ==========================================
echo   運動拍檔 Raceshot 上傳工具 - Windows 打包
echo ==========================================
echo.

REM 檢查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 找不到 python
    exit /b 1
)

REM 檢查虛擬環境
if not exist ".venv" (
    echo 📦 建立虛擬環境...
    python -m venv .venv
)

echo 🔧 啟用虛擬環境...
call .venv\Scripts\activate.bat

REM 安裝依賴
echo 📥 安裝依賴套件...
python -m pip install --upgrade pip
pip install -r requirements.txt

REM 清理舊檔案
echo 🧹 清理舊的打包檔案...
if exist "build" rmdir /s /q build
if exist "dist" rmdir /s /q dist

REM 執行打包
echo 🔨 開始打包...
pyinstaller raceshot_uploader.spec --clean --noconfirm

REM 檢查結果
if exist "dist\運動拍檔上傳工具.exe" (
    echo.
    echo ✅ 打包成功！
    echo 📁 執行檔位置：dist\運動拍檔上傳工具.exe
    echo.
    echo 🧪 測試執行：
    echo   dist\運動拍檔上傳工具.exe
    echo.
    echo 📤 發佈建議：
    echo   1. 將 dist 資料夾內的所有檔案打包成 zip
    echo   2. 或使用 Inno Setup 建立安裝程式
) else (
    echo ❌ 打包失敗
    exit /b 1
)

pause
