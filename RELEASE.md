# 發佈指南 - GitHub Actions 自動建置

本專案使用 GitHub Actions 自動建置三大平台的執行檔。

## 🚀 自動發佈流程

### 方法 1：建立 Git Tag（推薦）

```bash
# 1. 確保所有變更已提交
git add .
git commit -m "準備發佈 v1.0.0"

# 2. 建立並推送標籤
git tag v1.0.0
git push origin v1.0.0
```

這會自動觸發 GitHub Actions：
- ✅ 在 Linux、Windows、macOS 上同時建置
- ✅ 自動建立 GitHub Release
- ✅ 上傳所有平台的執行檔

### 方法 2：手動觸發

1. 前往 GitHub 專案頁面
2. 點擊 **Actions** 標籤
3. 選擇 **Build Multi-Platform Executables**
4. 點擊 **Run workflow**
5. 選擇分支並執行

## 📦 建置產物

建置完成後會產生：

### macOS
- `運動拍檔上傳工具-macOS.zip`
- 包含 `.app` 應用程式包
- 大小：約 25-30 MB

### Windows
- `運動拍檔上傳工具-Windows.zip`
- 包含 `.exe` 執行檔
- 大小：約 20-25 MB

### Linux
- `運動拍檔上傳工具-Linux.tar.gz`
- 包含可執行檔
- 大小：約 25-30 MB

## 📋 版本號規範

使用語義化版本（Semantic Versioning）：

- **v1.0.0** - 主要版本（重大變更）
- **v1.1.0** - 次要版本（新功能）
- **v1.0.1** - 修訂版本（錯誤修正）

## 🔍 檢查建置狀態

### 查看建置進度

1. 前往 GitHub 專案頁面
2. 點擊 **Actions** 標籤
3. 查看最新的工作流程執行狀態

### 建置時間

- macOS：約 5-8 分鐘
- Windows：約 5-8 分鐘
- Linux：約 5-8 分鐘
- **總計**：約 5-8 分鐘（平行執行）

## 📥 下載執行檔

### 從 GitHub Release 下載

1. 前往專案的 **Releases** 頁面
2. 選擇最新版本
3. 下載對應平台的壓縮檔

### 從 Actions Artifacts 下載

如果是手動觸發（沒有建立 Release）：

1. 前往 **Actions** 標籤
2. 點擊完成的工作流程
3. 在 **Artifacts** 區域下載

## 🛠️ 本地建置（備用）

如果 GitHub Actions 無法使用：

### macOS
```bash
./build_macos.sh
```

### Windows
```cmd
build_windows.bat
```

### Linux
```bash
./build_linux.sh
```

## 🐛 疑難排解

### 問題 1：建置失敗

**檢查**：
- Python 版本是否正確（3.9）
- requirements.txt 是否完整
- 打包腳本是否有執行權限

**解決**：查看 Actions 日誌找出錯誤原因

### 問題 2：執行檔無法啟動

**macOS**：
```bash
# 移除隔離屬性
xattr -cr 運動拍檔上傳工具.app
```

**Windows**：
- 檢查 Windows Defender 是否誤報
- 加入白名單或申請程式碼簽章

### 問題 3：檔案太大

**優化方法**：
1. 在 `raceshot_uploader.spec` 中排除不需要的模組
2. 關閉 UPX 壓縮（如果失敗）
3. 使用 `--onefile` 模式

## 📝 發佈檢查清單

發佈前請確認：

- [ ] 所有功能測試通過
- [ ] 更新 README.md
- [ ] 更新版本號
- [ ] 建立 CHANGELOG.md
- [ ] 測試本地建置
- [ ] 提交所有變更
- [ ] 建立並推送標籤
- [ ] 等待 GitHub Actions 完成
- [ ] 檢查 Release 頁面
- [ ] 下載並測試執行檔

## 🔐 程式碼簽章（可選）

### macOS

```bash
# 簽章應用程式
codesign --force --deep --sign "Developer ID Application: Your Name" dist/運動拍檔上傳工具.app

# 公證（需要 Apple Developer 帳號）
xcrun notarytool submit dist/運動拍檔上傳工具.app --apple-id your@email.com --password app-specific-password --team-id TEAM_ID
```

### Windows

需要購買程式碼簽章憑證（Code Signing Certificate）

## 📚 相關資源

- [GitHub Actions 文件](https://docs.github.com/en/actions)
- [PyInstaller 文件](https://pyinstaller.org/)
- [語義化版本](https://semver.org/lang/zh-TW/)
- [Apple 公證指南](https://developer.apple.com/documentation/security/notarizing_macos_software_before_distribution)

## 💡 最佳實踐

1. **定期發佈**：每個重要功能或錯誤修正後發佈新版本
2. **版本號一致**：確保程式內的版本號與 Git Tag 一致
3. **撰寫更新日誌**：在 CHANGELOG.md 記錄每個版本的變更
4. **測試再發佈**：在本地測試通過後再推送標籤
5. **保留舊版本**：不要刪除舊的 Release，方便使用者降級

## 🎯 快速開始

```bash
# 1. 完成開發並測試
git add .
git commit -m "feat: 新增批次上傳功能"

# 2. 建立標籤
git tag v1.1.0

# 3. 推送（自動觸發建置）
git push origin main
git push origin v1.1.0

# 4. 等待 5-8 分鐘

# 5. 前往 GitHub Release 頁面下載
```

就這麼簡單！🎉
