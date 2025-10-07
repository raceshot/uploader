# 運動拍檔上傳工具 使用說明

當您成功下載並開啟運動拍檔上傳工具後，請參考以下使用說明。

> 若您在開啟過程發生問題，請先參考以下連結
> Mac OS 安裝指南：https://github.com/raceshot/uploader/blob/main/installation_instructions/mac_install_guide.md
> Windows 安裝指南：https://github.com/raceshot/uploader/blob/main/installation_instructions/windows_install_guide.md

## 使用說明

### 基本設定

請參考下圖對照說明

![image](https://github.com/raceshot/uploader/how_to_use/image/01.jpg)

1. API Token：這是作為驗證您的帳號使用，為必填選項。
 * 請至攝影師後台->[API Token 管理](https://raceshot.app/photographer/api-token)
 * 選取有效期天數，最長 365 天，注意天數若過期將無法使用
 * 點選「生成 API Token」按鈕
 * 可於下方查看生成的 Token，請點選複製按鈕複製 Token 並回到上傳工具的 API Token 欄位貼入
2. 相片資料夾：請選取您要上傳相片的所在資料夾，由於會上傳資料夾內所有相片，建議您可事先篩選後再選取。
3. 活動 ID：請輸入您要上傳的賽事活動 ID，可至攝影師後台->[API Token 管理](https://raceshot.app/photographer/api-token)查看
4. 拍攝地點：請輸入您在該賽事拍攝的地點，不同地點請分開上傳。
5. 價格：輸入您要販售的價格，預設為 169，不可低於 60。
6. 號碼布：預留欄位可留空。

## 進階設定

請參考下圖對照說明

![image](https://github.com/raceshot/uploader/how_to_use/image/02.jpg)

1. 併發數：一次上傳的並行數量，預設為 20，若您的電腦或網速較不理想，請降低該數值。
2. 批次大小：單次上傳的相片樟樹，建議總量不要超過 10mb 避免出錯，若不確定請維持 1。
3. 逾時秒數：預設 30，若無需要請維持不變。

## 準備就緒開始上傳

完成上述設定後即可點擊下方開始上傳按鈕進行上傳，您可以透過上傳日誌以及下方進度條了解目前上傳情況，也可以按下停止按鈕隨時終止上傳程序。

本應用程式預設將會紀錄您前一次使用設定，方便您不必每次都要從頭開始填寫。

若有任何問題請至[官網](https://raceshot.app)洽詢客服。