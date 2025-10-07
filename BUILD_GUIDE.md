# 運動拍檔 Raceshot 上傳工具 - 打包指南

本文件說明如何將程式打包成三大作業系統的獨立執行檔。

## ⚠️ 重要提醒

**PyInstaller 無法跨平台打包**，必須在目標作業系統上執行打包：

- **macOS 執行檔** → 必須在 macOS 上打包
- **Windows 執行檔** → 必須在 Windows 上打包
- **Linux 執行檔** → 必須在 Linux 上打包

---

## 📦 macOS 打包

### 1. 執行打包腳本

```bash
chmod +x build_macos.sh
./build_macos.sh
```

### 2. 產出檔案

- **位置**：`dist/運動拍檔上傳工具.app`
- **類型**：macOS 應用程式包
- **大小**：約 80-120 MB

### 3. 測試執行

```bash
open dist/運動拍檔上傳工具.app
```

### 4. 發佈

**方法 1：壓縮成 ZIP**
```bash
cd dist
zip -r 運動拍檔上傳工具-macOS.zip 運動拍檔上傳工具.app
```

**方法 2：建立 DMG 安裝檔**（推薦）
```bash
# 安裝 create-dmg
brew install create-dmg

# 建立 DMG
create-dmg \
  --volname "運動拍檔上傳工具" \
  --window-pos 200 120 \
  --window-size 800 400 \
  --icon-size 100 \
  --app-drop-link 600 185 \
  運動拍檔上傳工具-macOS.dmg \
  dist/運動拍檔上傳工具.app
```

### 5. 程式碼簽章（可選）

避免「無法驗證開發者」警告：

```bash
# 簽章應用程式
codesign --force --deep --sign - dist/運動拍檔上傳工具.app

# 移除隔離屬性
xattr -cr dist/運動拍檔上傳工具.app
```

---

## 🪟 Windows 打包

### 1. 執行打包腳本

```cmd
build_windows.bat
```

### 2. 產出檔案

- **位置**：`dist\運動拍檔上傳工具.exe`
- **類型**：Windows 執行檔
- **大小**：約 60-100 MB

### 3. 測試執行

```cmd
dist\運動拍檔上傳工具.exe
```

### 4. 發佈

**方法 1：壓縮成 ZIP**
```cmd
cd dist
powershell Compress-Archive -Path * -DestinationPath 運動拍檔上傳工具-Windows.zip
```

**方法 2：建立安裝程式**（推薦）

使用 [Inno Setup](https://jrsoftware.org/isinfo.php) 建立安裝程式：

1. 下載並安裝 Inno Setup
2. 建立 `installer.iss` 配置檔
3. 編譯成 `.exe` 安裝程式

---

## 🐧 Linux 打包

### 1. 執行打包腳本

```bash
chmod +x build_linux.sh
./build_linux.sh
```

### 2. 產出檔案

- **位置**：`dist/運動拍檔上傳工具`
- **類型**：Linux 執行檔
- **大小**：約 80-120 MB

### 3. 測試執行

```bash
./dist/運動拍檔上傳工具
```

### 4. 發佈

**方法 1：壓縮成 tar.gz**
```bash
cd dist
tar -czf 運動拍檔上傳工具-Linux.tar.gz 運動拍檔上傳工具
```

**方法 2：建立 AppImage**（推薦）

使用 [appimagetool](https://appimage.github.io/appimagetool/) 建立 AppImage。

---

## 🔧 手動打包（進階）

如果自動腳本無法使用：

```bash
# 1. 建立虛擬環境
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2. 安裝依賴
pip install -r requirements.txt

# 3. 執行打包
pyinstaller raceshot_uploader.spec --clean
```

---

## 📊 檔案大小優化

### 減少執行檔大小

1. **排除不需要的模組**

編輯 `raceshot_uploader.spec`：

```python
excludes=['tkinter', 'matplotlib', 'numpy', 'pandas'],
```

2. **關閉 UPX 壓縮**（如果失敗）

```python
upx=False,
```

3. **使用 --onefile 模式**（啟動較慢）

```bash
pyinstaller --onefile gui_pyqt.py
```

---

## 🚀 CI/CD 自動化打包

使用 GitHub Actions 在三個平台自動打包：

```yaml
# .github/workflows/build.yml
name: Build Executables

on:
  push:
    tags:
      - 'v*'

jobs:
  build:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
    runs-on: ${{ matrix.os }}
    
    steps:
      - uses: actions/checkout@v3
      
      - uses: actions/setup-python@v4
        with:
          python-version: '3.9'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
      
      - name: Build (macOS/Linux)
        if: runner.os != 'Windows'
        run: |
          chmod +x build_${{ runner.os }}.sh
          ./build_${{ runner.os }}.sh
      
      - name: Build (Windows)
        if: runner.os == 'Windows'
        run: build_windows.bat
      
      - name: Upload artifact
        uses: actions/upload-artifact@v3
        with:
          name: 運動拍檔上傳工具-${{ matrix.os }}
          path: dist/
```

---

## 📝 發佈檢查清單

打包完成後，請確認：

- [ ] 執行檔可以正常啟動
- [ ] 圖標正確顯示
- [ ] 所有功能正常運作
- [ ] 上傳功能測試通過
- [ ] 檔案大小合理（< 150MB）
- [ ] 包含版本號和更新日期
- [ ] 準備 README 使用說明

---

## 🐛 疑難排解

### 問題 1：執行檔無法啟動

**解決方法**：
```bash
# 檢查依賴
pyinstaller raceshot_uploader.spec --clean --debug all
```

### 問題 2：找不到模組

**解決方法**：在 `raceshot_uploader.spec` 加入 `hiddenimports`：
```python
hiddenimports=['requests', 'PyQt6', 'dotenv'],
```

### 問題 3：macOS 提示「已損毀」

**解決方法**：
```bash
xattr -cr dist/運動拍檔上傳工具.app
```

### 問題 4：Windows Defender 誤報

**原因**：PyInstaller 打包的執行檔可能被誤判為病毒

**解決方法**：
1. 申請程式碼簽章憑證
2. 或在 README 說明如何加入白名單

---

## 📚 參考資源

- [PyInstaller 官方文件](https://pyinstaller.org/)
- [PyQt6 打包指南](https://www.pythonguis.com/tutorials/packaging-pyqt6-applications-pyinstaller/)
- [macOS 程式碼簽章](https://developer.apple.com/documentation/security/notarizing_macos_software_before_distribution)
