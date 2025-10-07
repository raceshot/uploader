# 應用程式圖標設定說明

## 如何更換圖標

### 1. 準備圖標檔案

將你的圖標檔案命名為 `app_icon.png`，並放在專案根目錄（與 `gui_pyqt.py` 同一層）。

**建議規格：**
- 格式：PNG（支援透明背景）
- 尺寸：512x512 或 1024x1024（會自動縮放）
- 檔案大小：< 1MB

### 2. 支援的圖標格式

程式會自動尋找以下檔案（優先順序由高到低）：
- `app_icon.png`
- `app_icon.ico`（Windows 專用）
- `app_icon.icns`（macOS 專用）

### 3. 測試圖標

放置圖標檔案後，重新啟動程式：

```bash
source .venv/bin/activate && python3 gui_pyqt.py
```

圖標會顯示在：
- **視窗標題列**（左上角）
- **Dock/工作列**（macOS/Windows）

### 4. 打包執行檔時的圖標

如果要打包成執行檔，需要在 PyInstaller 設定中指定圖標：

```bash
pyinstaller --onefile --windowed --icon=app_icon.png --name="運動拍檔上傳工具" gui_pyqt.py
```

## 圖標設計建議

1. **簡潔明瞭**：圖標應該在小尺寸下也能清楚辨識
2. **品牌一致**：使用品牌顏色（例如運動拍檔的紅色 #B22529）
3. **透明背景**：PNG 格式支援透明背景，適應不同主題
4. **高解析度**：準備 2x 或 3x 尺寸以支援 Retina 顯示器

## 線上圖標產生工具

如果需要產生多種格式的圖標：

- **IconKitchen**: https://icon.kitchen/
- **App Icon Generator**: https://appicon.co/
- **Favicon Generator**: https://realfavicongenerator.net/

## 範例

假設你有一張 `logo.png`：

```bash
# 1. 重新命名或複製為 app_icon.png
cp logo.png app_icon.png

# 2. 確認檔案存在
ls -lh app_icon.png

# 3. 重新啟動程式
source .venv/bin/activate && python3 gui_pyqt.py
```

## 疑難排解

### 圖標沒有顯示？

1. **確認檔案名稱**：必須是 `app_icon.png`（小寫）
2. **確認檔案位置**：與 `gui_pyqt.py` 同一目錄
3. **確認檔案格式**：使用 `file app_icon.png` 檢查
4. **重新啟動程式**：圖標只在啟動時載入

### macOS Dock 圖標沒有更新？

macOS 會快取圖標，可能需要：

```bash
# 清除圖標快取
sudo rm -rf /Library/Caches/com.apple.iconservices.store
killall Dock
```
