#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Raceshot 圖片上傳工具 - PyQt6 GUI 介面
"""
import sys
import threading
import json
import os
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar, QFileDialog,
    QMessageBox, QGroupBox, QSpinBox, QDialog, QDoubleSpinBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWebEngineWidgets import QWebEngineView
import tempfile

# 匯入核心上傳功能
from uploader import (
    collectImageFiles,
    collect_failures_to_reupload,
    uploadSingleImage,
    uploadImagesBatch,
    init_results_files,
    append_results,
    read_history_keys,
    chunked,
    UploadResult,
    OUTPUT_DIR,
    getFileSignature,
    FAILURE_LIST,
)
import requests


class MapPickerDialog(QDialog):
    """地圖選擇對話框 - 允許使用者在地圖上點擊選擇經緯度"""
    def __init__(self, parent=None, initial_lat=25.0, initial_lon=121.0):
        super().__init__(parent)
        self.setWindowTitle("選擇拍攝地點")
        self.setGeometry(100, 100, 1000, 750)
        self.latitude = initial_lat
        self.longitude = initial_lon
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # 說明文字
        info_text = QLabel("💡 提示：在地圖上點擊以選擇拍攝地點，或直接輸入座標")
        info_text.setStyleSheet("color: #666; font-size: 12px; margin-bottom: 5px;")
        layout.addWidget(info_text)
        
        # 建立地圖
        self.map_view = QWebEngineView()
        self.map_view.urlChanged.connect(self.on_url_changed)
        layout.addWidget(self.map_view)
        
        # 座標輸入區
        coord_layout = QHBoxLayout()
        coord_layout.addWidget(QLabel("緯度："))
        self.lat_input = QDoubleSpinBox()
        self.lat_input.setRange(-90, 90)
        self.lat_input.setValue(self.latitude)
        self.lat_input.setDecimals(6)
        self.lat_input.valueChanged.connect(self.on_coord_changed)
        coord_layout.addWidget(self.lat_input)
        
        coord_layout.addWidget(QLabel("經度："))
        self.lon_input = QDoubleSpinBox()
        self.lon_input.setRange(-180, 180)
        self.lon_input.setValue(self.longitude)
        self.lon_input.setDecimals(6)
        self.lon_input.valueChanged.connect(self.on_coord_changed)
        coord_layout.addWidget(self.lon_input)
        
        layout.addLayout(coord_layout)
        
        # 顯示選定的座標
        self.info_label = QLabel(f"選定座標：緯度 {self.latitude:.6f}, 經度 {self.longitude:.6f}")
        self.info_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        layout.addWidget(self.info_label)
        
        # 確認按鈕
        button_layout = QHBoxLayout()
        confirm_btn = QPushButton("✅ 確認")
        confirm_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("❌ 取消")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(confirm_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
        
        self.load_map()
        
    def on_coord_changed(self):
        """座標輸入框改變時更新"""
        self.latitude = self.lat_input.value()
        self.longitude = self.lon_input.value()
        self.info_label.setText(f"選定座標：緯度 {self.latitude:.6f}, 經度 {self.longitude:.6f}")
        self.load_map()
        
    def load_map(self):
        """載入互動式地圖"""
        # 建立 HTML 內容，使用 Leaflet 庫
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css" />
            <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
            <style>
                body {{ margin: 0; padding: 0; }}
                #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}
            </style>
        </head>
        <body>
            <div id="map"></div>
            <script>
                setTimeout(function() {{
                    var map = L.map('map').setView([{self.latitude}, {self.longitude}], 13);
                    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                        attribution: '© OpenStreetMap contributors',
                        maxZoom: 19
                    }}).addTo(map);
                    
                    var marker = L.marker([{self.latitude}, {self.longitude}]).addTo(map);
                    marker.bindPopup('拍攝地點<br>緯度: {self.latitude:.6f}<br>經度: {self.longitude:.6f}');
                    
                    // 點擊地圖更新座標
                    map.on('click', function(e) {{
                        var lat = parseFloat(e.latlng.lat.toFixed(6));
                        var lng = parseFloat(e.latlng.lng.toFixed(6));
                        marker.setLatLng([lat, lng]);
                        marker.setPopupContent('拍攝地點<br>緯度: ' + lat + '<br>經度: ' + lng);
                        marker.openPopup();
                        
                        // 通過 window.location.hash 將座標傳回 Python
                        window.location.hash = 'lat=' + lat + '&lng=' + lng;
                    }});
                }}, 500);
            </script>
        </body>
        </html>
        """
        
        # 直接使用 setHtml 方法設定內容
        self.map_view.setHtml(html_content)
    
    def on_url_changed(self, url):
        """監聽 URL 變化以獲取地圖點擊的座標"""
        url_str = url.toString()
        if 'lat=' in url_str and 'lng=' in url_str:
            try:
                # 解析 URL 中的座標
                hash_part = url_str.split('#')[1] if '#' in url_str else ''
                if hash_part:
                    params = dict(param.split('=') for param in hash_part.split('&'))
                    lat = float(params.get('lat', self.latitude))
                    lng = float(params.get('lng', self.longitude))
                    
                    # 更新座標
                    self.latitude = lat
                    self.longitude = lng
                    self.lat_input.blockSignals(True)
                    self.lon_input.blockSignals(True)
                    self.lat_input.setValue(lat)
                    self.lon_input.setValue(lng)
                    self.lat_input.blockSignals(False)
                    self.lon_input.blockSignals(False)
                    self.info_label.setText(f"選定座標：緯度 {self.latitude:.6f}, 經度 {self.longitude:.6f}")
            except Exception as e:
                pass
        
    def get_coordinates(self):
        """取得選定的座標"""
        return self.latitude, self.longitude


class UploadWorker(QThread):
    """上傳工作執行緒"""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(int, int)
    
    def __init__(self, params):
        super().__init__()
        self.params = params
        self.is_running = True
        
    def stop(self):
        self.is_running = False
        
    def run(self):
        try:
            token = self.params['token']
            folder = Path(self.params['folder'])
            event_id = self.params['event_id']
            location = self.params['location']
            price = self.params['price']
            bib_number = self.params.get('bib_number')
            longitude = self.params.get('longitude')
            latitude = self.params.get('latitude')
            concurrency = self.params.get('concurrency', 1)
            batch_size = self.params.get('batch_size', 1)
            timeout = self.params.get('timeout', 30.0)
            reupload_mode = self.params.get('reupload_mode', False)
            
            try:
                if reupload_mode:
                    self.log_signal.emit(f"📂 正在讀取失敗清單並搜尋檔案...")
                    files = collect_failures_to_reupload(folder)
                else:
                    self.log_signal.emit(f"📂 掃描資料夾：{folder}")
                    files = collectImageFiles(folder)
            
                if not files:
                    self.log_signal.emit("⚠️ 找不到任何圖片檔案，或掃描過程發生錯誤。")
                    self.finished_signal.emit(0, 0)
                    return
            except Exception as e:
                self.log_signal.emit(f"❌ 掃描資料夾時發生嚴重錯誤：{e}")
                self.finished_signal.emit(0, 0)
                return
                
            self.log_signal.emit(f"✅ 找到 {len(files)} 張圖片")
            
            # 初始化輸出檔案
            init_results_files()
            
            # 讀取歷史紀錄並過濾 (使用 Signature)
            history_keys = read_history_keys(event_id)
            
            final_files = []
            skipped = 0
            if not history_keys:
                final_files = files
            else:
                self.log_signal.emit("🔍 比對檔案特徵值中...")
                for p in files:
                    # 若是重試模式，通常我們希望即使歷史有紀錄(可能上次標記失敗但實際成功?)也要小心
                    # 但原則上：只要歷史有紀錄且成功，就跳過
                    if (getFileSignature(p), str(event_id)) in history_keys:
                        skipped += 1
                    else:
                        final_files.append(p)
            
            if skipped > 0:
                self.log_signal.emit(f"⏭️ 跳過 {skipped} 張已上傳的檔案 (重複特徵值)")
            
            files = final_files
                
            if not files:
                self.log_signal.emit("✅ 所有檔案都已上傳過")
                self.finished_signal.emit(0, 0)
                return
                
            self.log_signal.emit(f"🚀 開始上傳 {len(files)} 張圖片...")
            self.log_signal.emit(f"⚙️ 設定：併發={concurrency}, 批次={batch_size}, 逾時={timeout}s")
            
            results = []
            total = len(files)
            
            if concurrency == 1 and batch_size == 1:
                # 單執行緒逐張上傳
                session = requests.Session()
                for idx, file_path in enumerate(files, start=1):
                    if not self.is_running:
                        self.log_signal.emit("⏸️ 上傳已停止")
                        break
                        
                    self.log_signal.emit(f"({idx}/{total}) 上傳 {file_path.name}...")
                    progress = int(idx / total * 100)
                    self.progress_signal.emit(progress, f"上傳中：{idx}/{total}")
                    
                    result = uploadSingleImage(
                        session=session,
                        token=token,
                        file_path=file_path,
                        event_id=event_id,
                        location=location,
                        price=price,
                        bib_number=bib_number,
                        timeout=timeout,
                        max_retries=3,
                        retry_backoff=1.5,
                        longitude=longitude,
                        latitude=latitude,
                    )
                    results.append(result)
                    
                    if result.success:
                        self.log_signal.emit(f"  ✅ 成功：{file_path.name}")
                    else:
                        self.log_signal.emit(f"  ❌ 失敗：{file_path.name} - {result.error}")
                        
                    # 即時寫入結果
                    append_results([result], event_id=event_id, location=location)
            else:
                # 批次上傳
                batches = chunked(files, batch_size)
                total_batches = len(batches)
                self.log_signal.emit(f"📦 分成 {total_batches} 個批次")
                
                from concurrent.futures import ThreadPoolExecutor, as_completed
                
                def process_batch(idx_batch, batch_paths):
                    if not self.is_running:
                        return []
                    return uploadImagesBatch(
                        token=token,
                        file_paths=batch_paths,
                        event_id=event_id,
                        location=location,
                        price=price,
                        bib_number=bib_number,
                        timeout=timeout,
                        max_retries=3,
                        retry_backoff=1.5,
                        longitude=longitude,
                        latitude=latitude,
                    )
                
                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    future_to_idx = {
                        executor.submit(process_batch, i + 1, batch): i
                        for i, batch in enumerate(batches)
                    }
                    
                    completed = 0
                    for fut in as_completed(future_to_idx):
                        if not self.is_running:
                            break
                            
                        batch_results = fut.result()
                        results.extend(batch_results)
                        completed += 1
                        
                        progress = int(completed / total_batches * 100)
                        self.progress_signal.emit(progress, f"批次進度：{completed}/{total_batches}")
                        
                        ok = sum(1 for r in batch_results if r.success)
                        fail = len(batch_results) - ok
                        self.log_signal.emit(f"📦 批次 {completed}/{total_batches} 完成：成功 {ok}，失敗 {fail}")
                        
                        # 即時寫入結果
                        append_results(batch_results, event_id=event_id, location=location)
            
            # 統計結果
            total_uploaded = len(results)
            success_count = sum(1 for r in results if r.success)
            fail_count = total_uploaded - success_count
            
            self.log_signal.emit("=" * 50)
            self.log_signal.emit(f"✅ 上傳完成！")
            self.log_signal.emit(f"📊 成功：{success_count} 張")
            self.log_signal.emit(f"❌ 失敗：{fail_count} 張")
            self.log_signal.emit(f"📁 結果檔案：{OUTPUT_DIR}")
            self.log_signal.emit("=" * 50)
            
            self.progress_signal.emit(100, f"完成：成功 {success_count}，失敗 {fail_count}")
            self.finished_signal.emit(success_count, fail_count)
            
        except Exception as e:
            self.log_signal.emit(f"❌ 錯誤：{e}")
            self.finished_signal.emit(0, 0)


class RaceshotUploaderGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.upload_worker = None
        
        # 設定檔案路徑（使用者家目錄，避免打包後的唯讀問題）
        self.app_dir = Path.home() / ".raceshot_uploader"
        self.app_dir.mkdir(exist_ok=True)
        self.config_file = self.app_dir / "gui_config.json"
        
        self.init_ui()
        self.load_config()
        
    def init_ui(self):
        self.setWindowTitle("運動拍檔 Raceshot 圖片上傳工具")
        self.setGeometry(100, 100, 900, 750)
        
        # 設定應用程式圖標（支援多種格式）
        icon_formats = ["app_icon.png", "app_icon.jpg", "app_icon.ico", "app_icon.icns"]
        icon_loaded = False
        for icon_name in icon_formats:
            icon_path = Path(icon_name)
            if icon_path.exists():
                icon = QIcon(str(icon_path))
                if not icon.isNull():
                    self.setWindowIcon(icon)
                    icon_loaded = True
                    print(f"✅ 圖標已載入：{icon_name}")
                    break
                else:
                    print(f"⚠️ 圖標檔案無效：{icon_name}")
        
        if not icon_loaded:
            print("⚠️ 未找到有效的圖標檔案")
        
        # 主容器
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # 標題
        title = QLabel("運動拍檔 Raceshot 圖片上傳工具")
        title.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        title.setStyleSheet("color: #B22529; margin-bottom: 10px;")
        main_layout.addWidget(title)
        
        # 參數設定區
        params_group = QGroupBox("上傳參數")
        params_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        params_layout = QGridLayout()
        params_layout.setSpacing(10)
        
        row = 0
        
        # API Token
        params_layout.addWidget(QLabel("API Token"), row, 0)
        self.token_entry = QLineEdit()
        self.token_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_entry.setPlaceholderText("請輸入 API Token")
        self.token_entry.setStyleSheet("padding: 8px; border: 1px solid #ccc; border-radius: 4px;")
        params_layout.addWidget(self.token_entry, row, 1, 1, 2)
        row += 1
        
        # 資料夾選擇
        params_layout.addWidget(QLabel("相片資料夾"), row, 0)
        self.folder_entry = QLineEdit()
        self.folder_entry.setPlaceholderText("選擇包含相片的資料夾")
        self.folder_entry.setStyleSheet("padding: 8px; border: 1px solid #ccc; border-radius: 4px;")
        params_layout.addWidget(self.folder_entry, row, 1)
        
        browse_btn = QPushButton("瀏覽")
        browse_btn.clicked.connect(self.browse_folder)
        browse_btn.setStyleSheet("padding: 8px 20px; background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 4px;")
        params_layout.addWidget(browse_btn, row, 2)
        row += 1
        
        # 活動 ID
        params_layout.addWidget(QLabel("活動 ID"), row, 0)
        self.event_id_entry = QLineEdit()
        self.event_id_entry.setPlaceholderText("例如：12345")
        self.event_id_entry.setStyleSheet("padding: 8px; border: 1px solid #ccc; border-radius: 4px;")
        params_layout.addWidget(self.event_id_entry, row, 1, 1, 2)
        row += 1
        
        # 拍攝地點
        params_layout.addWidget(QLabel("拍攝地點:"), row, 0)
        self.location_entry = QLineEdit()
        self.location_entry.setPlaceholderText("例如：終點線")
        self.location_entry.setStyleSheet("padding: 8px; border: 1px solid #ccc; border-radius: 4px;")
        params_layout.addWidget(self.location_entry, row, 1, 1, 2)
        row += 1
        
        # 經緯度選擇
        params_layout.addWidget(QLabel("經度"), row, 0)
        self.longitude_entry = QDoubleSpinBox()
        self.longitude_entry.setRange(-180, 180)
        self.longitude_entry.setValue(121.0)
        self.longitude_entry.setDecimals(6)
        self.longitude_entry.setStyleSheet("padding: 8px; border: 1px solid #ccc; border-radius: 4px;")
        params_layout.addWidget(self.longitude_entry, row, 1)
        
        params_layout.addWidget(QLabel("緯度"), row, 2)
        self.latitude_entry = QDoubleSpinBox()
        self.latitude_entry.setRange(-90, 90)
        self.latitude_entry.setValue(25.0)
        self.latitude_entry.setDecimals(6)
        self.latitude_entry.setStyleSheet("padding: 8px; border: 1px solid #ccc; border-radius: 4px;")
        params_layout.addWidget(self.latitude_entry, row, 3)
        row += 1
        
        # 地圖選擇按鈕
        params_layout.addWidget(QLabel(""), row, 0)
        map_btn = QPushButton("🗺️ 在地圖上選擇")
        map_btn.clicked.connect(self.open_map_picker)
        map_btn.setStyleSheet("padding: 8px 20px; background-color: #4CAF50; color: white; border: 1px solid #45a049; border-radius: 4px;")
        params_layout.addWidget(map_btn, row, 1, 1, 2)
        row += 1
        
        # 價格與號碼布
        params_layout.addWidget(QLabel("價格"), row, 0)
        self.price_entry = QSpinBox()
        self.price_entry.setRange(60, 10000)
        self.price_entry.setValue(169)
        self.price_entry.setStyleSheet("padding: 8px; border: 1px solid #ccc; border-radius: 4px;")
        params_layout.addWidget(self.price_entry, row, 1)
        
        params_layout.addWidget(QLabel("號碼布"), row, 2)
        self.bib_entry = QLineEdit()
        self.bib_entry.setPlaceholderText("可選")
        self.bib_entry.setStyleSheet("padding: 8px; border: 1px solid #ccc; border-radius: 4px;")
        params_layout.addWidget(self.bib_entry, row, 3)
        row += 1
        
        # 進階設定
        advanced_label = QLabel("進階設定")
        advanced_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        params_layout.addWidget(advanced_label, row, 0, 1, 4)
        row += 1
        
        params_layout.addWidget(QLabel("併發數"), row, 0)
        self.concurrency_entry = QSpinBox()
        self.concurrency_entry.setRange(1, 20)
        self.concurrency_entry.setValue(20)
        self.concurrency_entry.setStyleSheet("padding: 5px; border: 1px solid #ccc; border-radius: 4px;")
        params_layout.addWidget(self.concurrency_entry, row, 1)
        
        params_layout.addWidget(QLabel("批次大小"), row, 2)
        self.batch_size_entry = QSpinBox()
        self.batch_size_entry.setRange(1, 50)
        self.batch_size_entry.setValue(1)
        self.batch_size_entry.setStyleSheet("padding: 5px; border: 1px solid #ccc; border-radius: 4px;")
        params_layout.addWidget(self.batch_size_entry, row, 3)
        row += 1
        
        params_layout.addWidget(QLabel("逾時秒數"), row, 0)
        self.timeout_entry = QSpinBox()
        self.timeout_entry.setRange(10, 300)
        self.timeout_entry.setValue(30)
        self.timeout_entry.setStyleSheet("padding: 5px; border: 1px solid #ccc; border-radius: 4px;")
        params_layout.addWidget(self.timeout_entry, row, 1)
        
        params_group.setLayout(params_layout)
        main_layout.addWidget(params_group)
        
        # 日誌顯示區
        log_group = QGroupBox("上傳日誌")
        log_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        log_layout = QVBoxLayout()
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("padding: 10px; border: 1px solid #ccc; border-radius: 4px; background-color: #f9f9f9;")
        log_layout.addWidget(self.log_text)
        
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)
        
        # 進度條
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet("QProgressBar { border: 1px solid #ccc; border-radius: 4px; text-align: center; } QProgressBar::chunk { background-color: #3B8ED0; }")
        main_layout.addWidget(self.progress_bar)
        
        self.progress_label = QLabel("準備就緒")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.progress_label)
        
        # 控制按鈕
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        self.start_btn = QPushButton("🚀 開始上傳")
        self.start_btn.clicked.connect(self.start_upload)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #3B8ED0;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 12px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #1F6AA5;
            }
        """)
        button_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("⏸️ 停止")
        self.stop_btn.clicked.connect(self.stop_upload)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: gray;
                color: white;
                font-size: 14px;
                padding: 12px;
                border-radius: 5px;
            }
            QPushButton:enabled {
                background-color: #f44336;
            }
            QPushButton:enabled:hover {
                background-color: #d32f2f;
            }
        """)
        button_layout.addWidget(self.stop_btn)
        
        clear_log_btn = QPushButton("🗑️ 清除日誌")
        clear_log_btn.clicked.connect(self.clear_log)
        clear_log_btn.setStyleSheet("""
            QPushButton {
                background-color: gray;
                color: white;
                font-size: 14px;
                padding: 12px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #616161;
            }
        """)
        button_layout.addWidget(clear_log_btn)
        
        # 新增一行：重試失敗按鈕
        retry_layout = QHBoxLayout()
        self.retry_btn = QPushButton("🔄 一鍵重新上傳失敗檔案")
        self.retry_btn.clicked.connect(self.retry_failures)
        self.retry_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 10px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
        """)
        retry_layout.addWidget(self.retry_btn)
        main_layout.addLayout(retry_layout)
        
        main_layout.addLayout(button_layout)
        
    def load_config(self):
        """載入上次的設定"""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                # 載入設定到介面
                if 'token' in config:
                    self.token_entry.setText(config['token'])
                if 'folder' in config:
                    self.folder_entry.setText(config['folder'])
                if 'event_id' in config:
                    self.event_id_entry.setText(config['event_id'])
                if 'location' in config:
                    self.location_entry.setText(config['location'])
                if 'price' in config:
                    self.price_entry.setValue(config['price'])
                if 'bib_number' in config:
                    self.bib_entry.setText(config['bib_number'])
                if 'longitude' in config:
                    self.longitude_entry.setValue(config['longitude'])
                if 'latitude' in config:
                    self.latitude_entry.setValue(config['latitude'])
                if 'concurrency' in config:
                    self.concurrency_entry.setValue(config['concurrency'])
                if 'batch_size' in config:
                    self.batch_size_entry.setValue(config['batch_size'])
                if 'timeout' in config:
                    self.timeout_entry.setValue(config['timeout'])
                    
                # 不要在這裡輸出日誌，避免污染 Token
        except Exception as e:
            # 載入失敗也不輸出，避免污染 Token
            pass
    
    def save_config(self):
        """儲存目前的設定"""
        try:
            config = {
                'token': self.token_entry.text().strip(),
                'folder': self.folder_entry.text().strip(),
                'event_id': self.event_id_entry.text().strip(),
                'location': self.location_entry.text().strip(),
                'price': self.price_entry.value(),
                'bib_number': self.bib_entry.text().strip(),
                'longitude': self.longitude_entry.value(),
                'latitude': self.latitude_entry.value(),
                'concurrency': self.concurrency_entry.value(),
                'batch_size': self.batch_size_entry.value(),
                'timeout': self.timeout_entry.value(),
            }
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                
            # 儲存成功不輸出日誌
        except Exception as e:
            # 儲存失敗也不輸出，避免干擾使用者
            pass
    
    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "選擇圖片資料夾")
        if folder:
            self.folder_entry.setText(folder)
    
    def open_map_picker(self):
        """打開地圖選擇對話框"""
        dialog = MapPickerDialog(
            self,
            initial_lat=self.latitude_entry.value(),
            initial_lon=self.longitude_entry.value()
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            lat, lon = dialog.get_coordinates()
            self.latitude_entry.setValue(lat)
            self.longitude_entry.setValue(lon)
            self.log(f"✅ 已選擇座標：緯度 {lat:.6f}, 經度 {lon:.6f}")
            
    def log(self, message):
        self.log_text.append(message)
        
    def clear_log(self):
        self.log_text.clear()
        
    def validate_inputs(self):
        if not self.token_entry.text().strip():
            QMessageBox.critical(self, "錯誤", "請輸入 API Token")
            return False
            
        if not self.folder_entry.text().strip():
            QMessageBox.critical(self, "錯誤", "請選擇圖片資料夾")
            return False
            
        if not Path(self.folder_entry.text()).exists():
            QMessageBox.critical(self, "錯誤", "選擇的資料夾不存在")
            return False
            
        if not self.event_id_entry.text().strip():
            QMessageBox.critical(self, "錯誤", "請輸入活動 ID")
            return False
            
        if not self.location_entry.text().strip():
            QMessageBox.critical(self, "錯誤", "請輸入拍攝地點")
            return False
            
        return True
        
    def start_upload(self):
        self._run_upload(reupload_mode=False)

    def retry_failures(self):
        if not (self.app_dir / "output" / "failure_list.txt").exists():
            QMessageBox.warning(self, "提示", "找不到失敗清單檔案，無法執行重試。")
            return
        self._run_upload(reupload_mode=True)

    def _run_upload(self, reupload_mode=False):
        if not self.validate_inputs():
            return
        
        # 儲存設定
        self.save_config()
            
        # 準備參數
        params = {
            'token': self.token_entry.text().strip(),
            'folder': self.folder_entry.text().strip(),
            'event_id': self.event_id_entry.text().strip(),
            'location': self.location_entry.text().strip(),
            'price': self.price_entry.value(),
            'bib_number': self.bib_entry.text().strip() or None,
            'longitude': self.longitude_entry.value() if self.longitude_entry.value() != 0 else None,
            'latitude': self.latitude_entry.value() if self.latitude_entry.value() != 0 else None,
            'concurrency': self.concurrency_entry.value(),
            'batch_size': self.batch_size_entry.value(),
            'timeout': float(self.timeout_entry.value()),
            'reupload_mode': reupload_mode,
        }
        
        # 更新 UI 狀態
        self.start_btn.setEnabled(False)
        self.retry_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        mode_text = "重試失敗檔案" if reupload_mode else "上傳"
        self.progress_label.setText(f"準備{mode_text}...")
        
        # 啟動工作執行緒
        self.upload_worker = UploadWorker(params)
        self.upload_worker.log_signal.connect(self.log)
        self.upload_worker.progress_signal.connect(self.update_progress)
        self.upload_worker.finished_signal.connect(self.upload_finished)
        self.upload_worker.start()
        
    def stop_upload(self):
        if self.upload_worker:
            self.upload_worker.stop()
            self.log("⏸️ 使用者請求停止上傳...")
            
    def update_progress(self, value, text):
        self.progress_bar.setValue(value)
        self.progress_label.setText(text)
        
    def upload_finished(self, success_count, fail_count):
        self.start_btn.setEnabled(True)
        self.retry_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        QMessageBox.information(
            self,
            "上傳完成",
            f"成功：{success_count} 張\n失敗：{fail_count} 張\n\n結果已儲存至 {OUTPUT_DIR}"
        )


def main():
    app = QApplication(sys.argv)
    
    # 設定應用程式層級的圖標（macOS 需要）
    icon_formats = ["app_icon.png", "app_icon.jpg", "app_icon.ico"]
    for icon_name in icon_formats:
        icon_path = Path(icon_name)
        if icon_path.exists():
            app_icon = QIcon(str(icon_path))
            if not app_icon.isNull():
                app.setWindowIcon(app_icon)
                print(f"✅ 應用程式圖標已設定：{icon_name}")
                break
    
    window = RaceshotUploaderGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
