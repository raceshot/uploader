# Raceshot 圖片上傳工具 (Python)

此工具可將指定資料夾中的所有相片，逐張呼叫 API 上傳到 Raceshot，並輸出每張相片的成功/失敗記錄。

- API 端點：`POST https://api.raceshot.app/api/photographer/upload`
- 欄位：
  - `images`: 多個圖片檔案 (此工具採逐張上傳)
  - `eventId`: 活動ID (必填)
  - `bibNumber`: 號碼布號碼 (可選)
  - `location`: 拍攝地點 (必填)
  - `price`: 價格 (必填，預設 30)
  - `longitude`: 經度 (可選)
  - `latitude`: 緯度 (可選)

## 安裝步驟

建議使用虛擬環境：

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows 使用 .venv\\Scripts\\activate
pip install -r requirements.txt
```

## 使用方式

可用環境變數或參數提供 API Token：
- 環境變數：`RACESHOT_API_TOKEN`（可透過 `.env` 檔載入）
- 或使用 `--token` 參數

指令參數說明：

```bash
python3 uploader.py --dir <圖片資料夾> \
  --event-id <活動ID> \
  --location <拍攝地點> \
  [--price <價格，預設30>] \
  [--bib-number <號碼布>] \
  [--longitude <經度>] \
  [--latitude <緯度>] \
  [--token <API Token，否則讀取環境變數>] \
  [--max-retries <重試次數，預設3>] \
  [--retry-backoff <重試退避係數，預設1.5>] \
  [--timeout <逾時秒數，預設30>] \
  [--dry-run] \
  [--env-file <自訂 .env 檔路徑>]
```

範例：

```bash
# 以環境變數提供 Token，將 ~/photos/ 內所有圖片上傳至 event-123
export RACESHOT_API_TOKEN="YOUR_TOKEN"
python3 uploader.py --dir ~/photos \
  --event-id event-123 \
  --location "Finish Line" \
  --price 30
```

```bash
# 直接帶入 token 並指定號碼布
python3 uploader.py --dir ./images \
  --event-id evt_2025_09_10 \
  --location "CP2" \
  --bib-number 168 \
  --token "YOUR_TOKEN"
```

```bash
# 先預覽將上傳的清單（不真的呼叫 API）
python3 uploader.py --dir ./images \
  --event-id evt_2025_09_10 \
  --location "CP2" \
  --dry-run
```

```bash
# 上傳時帶入經緯度資訊
python3 uploader.py --dir ./images \
  --event-id evt_2025_09_10 \
  --location "Finish Line" \
  --latitude 25.033611 \
  --longitude 121.565000 \
  --token "YOUR_TOKEN"
```

### 使用 .env 設定變數

支援自動載入專案根目錄的 `.env`，或使用 `--env-file` 指定路徑。最常見的是在 `.env` 放入 Token：

```dotenv
# .env 範例
RACESHOT_API_TOKEN=YOUR_API_TOKEN
RACESHOT_LONGITUDE=121.565000
RACESHOT_LATITUDE=25.033611
```

執行時會自動讀取當前目錄的 `.env`；如需指定其他路徑：

```bash
python3 uploader.py --dir ./images \
  --event-id evt_2025_09_10 \
  --location "CP2" \
  --env-file ./configs/prod.env
```

注意：
- `.env` 可用於提供 `RACESHOT_API_TOKEN`、`RACESHOT_LONGITUDE` 和 `RACESHOT_LATITUDE`。
- 指令列參數若有提供，會優先於環境變數。

## 輸出檔案

程式執行後會在 `output/` 產生：
- `upload_results.csv`：每張檔案的詳細結果（檔名、成功/失敗、訊息、photo_id、錯誤、HTTP 狀態碼）
- `success_list.txt`：成功上傳之檔名清單
- `failure_list.txt`：失敗之檔名清單
- `upload.log`：完整日誌

## 支援的圖片格式

- `.jpg`, `.jpeg`, `.png`

## GUI 介面功能

### 地圖選擇功能
- 點擊「🗺️ 在地圖上選擇」按鈕打開互動式地圖
- 在地圖上點擊以選擇拍攝地點座標
- 支持直接輸入經度和緯度數值
- 座標會自動保存到配置檔案

### 配置保存
- 所有設定（包括經緯度）會自動保存
- 下次啟動時自動載入上次的設定

## 注意事項與最佳實務

- 遇到網路/伺服器錯誤（5xx/429 或連線逾時）會自動重試，次數與退避可調整。
- 若後端回傳「已重複上傳」等錯誤，會記錄於 `failure_list.txt`，以便後續檢查。
- 建議先使用 `--dry-run` 檢查目錄掃描是否正確，再正式上傳。
- 地圖功能需要網際網路連線以載入 OpenStreetMap 瓦片和 Leaflet 庫。

## 疑難排解

- 若出現 `ModuleNotFoundError: No module named 'requests'`，請先執行：

```bash
pip install -r requirements.txt
```

- 若 API 權限錯誤，請確認 Token 是否正確或未過期。
