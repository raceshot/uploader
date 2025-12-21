#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Raceshot 圖片上傳工具

功能：
- 由指定資料夾遞迴蒐集圖片檔
- 逐張呼叫 API 上傳，方便精準記錄每張檔名之成功/失敗
- 允許從命令列或環境變數 RACESHOT_API_TOKEN 取得 API Token
- 具備重試與逾時處理、錯誤日誌
- 產出結果檔案：
  - output/upload_results.csv
  - output/success_list.txt
  - output/failure_list.txt
  - output/upload.log (完整日誌)

使用方式請見 README.md。
"""
from __future__ import annotations

import argparse
import csv
import logging
import mimetypes
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import hashlib

API_ENDPOINT = "https://api.raceshot.app/api/v1/photographer/upload"
DEFAULT_PRICE = 30

# 輸出目錄：使用者家目錄（避免打包後的唯讀問題）
OUTPUT_DIR = Path.home() / ".raceshot_uploader" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

RESULT_CSV = OUTPUT_DIR / "upload_results.csv"
SUCCESS_LIST = OUTPUT_DIR / "success_list.txt"
FAILURE_LIST = OUTPUT_DIR / "failure_list.txt"
LOG_FILE = OUTPUT_DIR / "upload.log"
HISTORY_CSV = OUTPUT_DIR / "upload_history_v2.csv"
WRITE_LOCK = threading.Lock()

# 允許的圖片副檔名（小寫）
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@dataclass
class UploadResult:
    file_name: str
    success: bool
    message: str
    photo_id: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    file_path: Optional[str] = None  # 絕對路徑，供歷史紀錄使用
    signature: Optional[str] = None  # 檔案特徵值


def setupLogging() -> None:
    """設定 console 與 file 的 logging。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch_formatter = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    ch.setFormatter(ch_formatter)

    # File handler
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh_formatter = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fh_formatter)

    logger.handlers.clear()
    logger.addHandler(ch)
    logger.addHandler(fh)


def parseArgs(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Raceshot 圖片上傳工具")
    parser.add_argument("--dir", dest="directory", required=False, default=None, help="要遞迴上傳的圖片資料夾（可用環境變數 RACESHOT_DIR）")
    parser.add_argument("--event-id", dest="event_id", required=False, default=None, help="活動 ID（可用環境變數 RACESHOT_EVENT_ID）")
    parser.add_argument("--location", dest="location", required=False, default=None, help="拍攝地點（可用環境變數 RACESHOT_LOCATION）")
    parser.add_argument("--price", dest="price", type=int, default=None, help=f"價格（可用環境變數 RACESHOT_PRICE；未提供則預設 {DEFAULT_PRICE}）")
    parser.add_argument("--bib-number", dest="bib_number", default=None, help="號碼布號碼 (可選)")
    parser.add_argument("--token", dest="token", default=None, help="API Token；可用環境變數 RACESHOT_API_TOKEN")
    parser.add_argument("--max-retries", dest="max_retries", type=int, default=None, help="最大重試次數（可用環境變數 RACESHOT_MAX_RETRIES；未提供則預設 3）")
    parser.add_argument("--retry-backoff", dest="retry_backoff", type=float, default=None, help="重試退避係數秒數（可用環境變數 RACESHOT_RETRY_BACKOFF；未提供則預設 1.5）")
    parser.add_argument("--timeout", dest="timeout", type=float, default=None, help="單次請求逾時秒數（可用環境變數 RACESHOT_TIMEOUT；未提供則預設 30s）")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="僅列出將要上傳的檔案，不實際呼叫 API")
    parser.add_argument("--env-file", dest="env_file", default=None, help="指定 .env 檔路徑（預設會自動讀取當前目錄的 .env）")
    parser.add_argument("--concurrency", dest="concurrency", type=int, default=None, help="同時上傳的工作執行緒數（可用環境變數 RACESHOT_CONCURRENCY；預設 1）")
    parser.add_argument("--batch-size", dest="batch_size", type=int, default=None, help="單次請求上傳的圖片數量（可用環境變數 RACESHOT_BATCH_SIZE；預設 1）")
    parser.add_argument("--reupload-failures", dest="reupload_failures", action="store_true", help="讀取 output/failure_list.txt 並僅重新上傳這些失敗的檔案")
    parser.add_argument("--longitude", dest="longitude", type=float, default=None, help="經度（可用環境變數 RACESHOT_LONGITUDE）")
    parser.add_argument("--latitude", dest="latitude", type=float, default=None, help="緯度（可用環境變數 RACESHOT_LATITUDE）")
    return parser.parse_args(argv)


def getApiToken(cli_token: Optional[str]) -> str:
    token = cli_token or os.getenv("RACESHOT_API_TOKEN")
    if not token:
        logging.error("找不到 API Token，請使用 --token 或設定環境變數 RACESHOT_API_TOKEN")
        sys.exit(1)
    return token


def parseBoolEnv(val: Optional[str]) -> Optional[bool]:
    """將環境變數字串轉為布林。
    可接受：1/true/yes/y/on 與 0/false/no/n/off（不分大小寫）。
    無法解析則回傳 None。
    """
    if val is None:
        return None
    s = val.strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return None


def collectImageFiles(root_dir: Path) -> List[Path]:
    if not root_dir.exists() or not root_dir.is_dir():
        logging.error(f"指定路徑不存在或不是資料夾：{root_dir}")
        sys.exit(1)

    files: List[Path] = []
    for path in root_dir.rglob("*"):
        if path.is_file():
            ext = path.suffix.lower()
            if ext in ALLOWED_EXTENSIONS:
                files.append(path)
    files.sort()
    if not files:
        logging.warning("找不到任何符合的圖片檔案。允許的副檔名：" + ", ".join(sorted(ALLOWED_EXTENSIONS)))
    logging.info(f"共找到 {len(files)} 張圖片待上傳")
    return files


def guessMimeType(file_path: Path) -> Optional[str]:
    mime, _ = mimetypes.guess_type(str(file_path))
    return mime


def getFileSignature(path: Path) -> str:
    """計算檔案特徵值：Hash(Size + Mtime + First 4KB)"""
    try:
        stat = path.stat()
        file_size = stat.st_size
        mtime = stat.st_mtime
        
        with open(path, "rb") as f:
            head = f.read(4096)
        
        h = hashlib.md5()
        h.update(str(file_size).encode())
        h.update(str(mtime).encode())
        h.update(head)
        return h.hexdigest()
    except Exception as e:
        logging.warning(f"無法計算檔案特徵值 {path}: {e}")
        return ""


def buildMultipart(file_path: Path) -> Tuple[str, Tuple[str, bytes, Optional[str]]]:
    """建立 multipart form-data 的單一 'images' 欄位資料。
    回傳 (field_name, file_tuple)
    file_tuple 為 (filename, file_bytes, content_type)
    """
    content_type = guessMimeType(file_path) or "application/octet-stream"
    with open(file_path, "rb") as f:
        data = f.read()
    return (
        "images",
        (file_path.name, data, content_type),
    )


def chunked(items: List[Path], size: int) -> List[List[Path]]:
    """將清單切成固定大小區塊。"""
    if size <= 0:
        size = 1
    return [items[i : i + size] for i in range(0, len(items), size)]


def shouldRetry(status_code: Optional[int], exc: Optional[BaseException]) -> bool:
    if exc is not None:
        # 連線錯誤、逾時等，建議重試
        return True
    if status_code is None:
        return False
    # 5xx 與 429 重試
    if status_code >= 500 or status_code == 429:
        return True
    return False


def isDuplicateFailure(error_msg: Optional[str], failure_item: Optional[dict] = None) -> Tuple[bool, Optional[str]]:
    """判斷是否為『已上傳』的重複情況，並嘗試取出 photoId。
    規則：
    - error 含有 'already upload'（不分大小寫）、'已上傳'、'已存在' 等字樣
    - 或 failure_item 本身含有 photoId（多數情況代表已存在）
    回傳 (is_duplicate, photo_id)
    """
    photo_id = None
    if isinstance(failure_item, dict):
        pid = failure_item.get("photoId") or failure_item.get("photoID")
        if pid:
            photo_id = str(pid)
    if error_msg:
        s = str(error_msg).strip().lower()
        dup_keywords = ["already upload", "already uploaded", "duplicate", "已上傳", "已存在"]
        if any(k in s for k in dup_keywords):
            return True, photo_id
    # 沒有明確訊息，但有 photo_id 也視為已存在
    if photo_id:
        return True, photo_id
    return False, None


def uploadSingleImage(
    session: requests.Session,
    token: str,
    file_path: Path,
    event_id: str,
    location: str,
    price: int,
    bib_number: Optional[str],
    timeout: float,
    max_retries: int,
    retry_backoff: float,
    longitude: Optional[float] = None,
    latitude: Optional[float] = None,
) -> UploadResult:
    headers = {"Authorization": f"Bearer {token}"}

    form_data = {
        "eventId": str(event_id),
        "location": str(location),
        "price": str(price),
    }
    if bib_number:
        form_data["bibNumber"] = str(bib_number)
    if longitude is not None:
        form_data["longitude"] = str(longitude)
    if latitude is not None:
        form_data["latitude"] = str(latitude)

    images_field = [buildMultipart(file_path)]

    attempt = 0
    last_error: Optional[str] = None
    last_status: Optional[int] = None
    while True:
        attempt += 1
        exc: Optional[BaseException] = None
        try:
            resp = session.post(
                API_ENDPOINT,
                headers=headers,
                data=form_data,
                files=images_field,
                timeout=timeout,
            )
            last_status = resp.status_code
            # 嘗試解析 JSON
            payload = None
            try:
                payload = resp.json()
            except Exception:
                payload = None

            if resp.ok and payload is not None:
                # 依照題目回傳格式處理
                success = bool(payload.get("success"))
                message = str(payload.get("message", ""))
                photo_ids = payload.get("photoIds") or []
                failure_items = payload.get("failures") or []

                if success:
                    photo_id = photo_ids[0] if isinstance(photo_ids, list) and photo_ids else None
                    logging.info(f"✅ 成功上傳：{file_path.name} (photoId={photo_id})")
                    return UploadResult(
                        file_name=file_path.name,
                        success=True,
                        message=message or "上傳成功",
                        photo_id=photo_id,
                        status_code=resp.status_code,
                        file_path=str(file_path.resolve()),
                        signature=getFileSignature(file_path),
                    )
                else:
                    # 失敗情境：回傳中可能含有 failures 陣列
                    err_msg = None
                    if isinstance(failure_items, list) and failure_items:
                        f0 = failure_items[0]
                        # 後端可能傳 error 與 fileName 或 photoId
                        err_msg = f0.get("error") or message or "上傳失敗"
                        is_dup, dup_pid = isDuplicateFailure(err_msg, f0)
                        if is_dup:
                            logging.info(f"☑️ 已上傳（視為成功）：{file_path.name}")
                            return UploadResult(
                                file_name=file_path.name,
                                success=True,
                                message="已上傳（視為成功）",
                                photo_id=dup_pid,
                                status_code=resp.status_code,
                                file_path=str(file_path.resolve()),
                                signature=getFileSignature(file_path),
                            )
                    else:
                        err_msg = message or f"HTTP {resp.status_code}"
                    logging.warning(f"⚠️ 上傳失敗：{file_path.name} - {err_msg}")
                    # 4xx 不重試；但 429 會在 shouldRetry 中允許
                    if not shouldRetry(resp.status_code, None):
                        return UploadResult(
                            file_name=file_path.name,
                            success=False,
                            message=message or "上傳失敗",
                            error=err_msg,
                            status_code=resp.status_code,
                            file_path=str(file_path.resolve()),
                            signature=getFileSignature(file_path),
                        )
                    last_error = err_msg
            else:
                # 非 ok 或非 JSON 回傳
                text = None
                try:
                    text = resp.text
                except Exception:
                    text = None
                last_error = f"HTTP {resp.status_code}: {text[:300] if text else 'No body'}"
                logging.warning(f"⚠️ 伺服器回應錯誤：{file_path.name} - {last_error}")
        except (requests.ConnectionError, requests.Timeout) as e:
            exc = e
            last_error = f"連線/逾時錯誤：{e}"
            logging.warning(f"⚠️ 請求失敗（{file_path.name}）：{e}")
        except Exception as e:
            exc = e
            last_error = f"其他錯誤：{e}"
            logging.exception(f"❌ 未預期錯誤（{file_path.name}）：{e}")

        # 判斷是否重試
        if attempt <= max_retries and shouldRetry(last_status, exc):
            sleep_seconds = retry_backoff ** (attempt - 1)
            logging.info(f"重試第 {attempt}/{max_retries} 次前等待 {sleep_seconds:.1f}s：{file_path.name}")
            time.sleep(sleep_seconds)
            continue

        # 放棄重試
        return UploadResult(
            file_name=file_path.name,
            success=False,
            message="上傳失敗",
            error=last_error,
            status_code=last_status,
            file_path=str(file_path.resolve()),
            signature=getFileSignature(file_path),
        )


def uploadImagesBatch(
    token: str,
    file_paths: List[Path],
    event_id: str,
    location: str,
    price: int,
    bib_number: Optional[str],
    timeout: float,
    max_retries: int,
    retry_backoff: float,
    longitude: Optional[float] = None,
    latitude: Optional[float] = None,
) -> List[UploadResult]:
    """一次上傳多張圖片（同一請求），回傳逐檔結果清單。"""
    headers = {"Authorization": f"Bearer {token}"}
    form_data = {
        "eventId": str(event_id),
        "location": str(location),
        "price": str(price),
    }
    if bib_number:
        form_data["bibNumber"] = str(bib_number)
    if longitude is not None:
        form_data["longitude"] = str(longitude)
    if latitude is not None:
        form_data["latitude"] = str(latitude)

    files_field: List[Tuple[str, Tuple[str, bytes, Optional[str]]]] = []
    for p in file_paths:
        files_field.append(buildMultipart(p))

    attempt = 0
    last_error: Optional[str] = None
    last_status: Optional[int] = None
    session = requests.Session()
    while True:
        attempt += 1
        exc: Optional[BaseException] = None
        try:
            resp = session.post(
                API_ENDPOINT,
                headers=headers,
                data=form_data,
                files=files_field,
                timeout=timeout,
            )
            last_status = resp.status_code
            payload = None
            try:
                payload = resp.json()
            except Exception:
                payload = None

            # 預設全部標為失敗，若成功或判定為重複則覆寫
            results: List[UploadResult] = [
                UploadResult(
                    file_name=p.name,
                    success=False,
                    message="上傳失敗",
                    file_path=str(p.resolve()),
                    signature=getFileSignature(p)
                )
                for p in file_paths
            ]

            if resp.ok and payload is not None:
                success = bool(payload.get("success"))
                message = str(payload.get("message", ""))
                photo_ids = payload.get("photoIds") or []
                failure_items = payload.get("failures") or []

                # 將失敗項目以 fileName 建立查表
                failed_by_name: dict[str, str] = {}
                dup_by_name: dict[str, Optional[str]] = {}
                unknown_failure_count = 0
                for f in failure_items if isinstance(failure_items, list) else []:
                    fn = f.get("fileName") or f.get("filename")
                    err = f.get("error") or message or "上傳失敗"
                    is_dup, dup_pid = isDuplicateFailure(err, f)
                    if fn:
                        if is_dup:
                            dup_by_name[fn] = dup_pid
                        else:
                            failed_by_name[fn] = err
                    else:
                        # 無法對應到檔名者
                        if not is_dup:
                            unknown_failure_count += 1

                # 先標記『已上傳』為成功
                for r in results:
                    if r.file_name in dup_by_name:
                        r.success = True
                        r.message = "已上傳（視為成功）"
                        r.photo_id = dup_by_name[r.file_name]

                # 再標記真正的失敗
                for r in results:
                    if r.file_name in failed_by_name:
                        r.success = False
                        r.error = failed_by_name[r.file_name]
                        r.message = message or r.error or "上傳失敗"

                # 其餘標記為成功（暫無法從回傳對應 file_name 與 photo_id 的關係）
                for i, r in enumerate(results):
                    if r.file_name not in failed_by_name and r.file_name not in dup_by_name:
                        r.success = True
                        r.message = message or "上傳成功"
                        r.photo_id = None

                # 若回傳有未知的失敗數（沒有 fileName），將尚未標記的成功項目回填為失敗以符合計數
                if unknown_failure_count > 0:
                    patched = 0
                    for r in results:
                        if patched >= unknown_failure_count:
                            break
                        if r.success and r.file_name not in dup_by_name:
                            r.success = False
                            r.error = "回應未提供 fileName，無法對應之失敗項目"
                            r.message = message or r.error
                            patched += 1

                # 嘗試填入第一個 photo_id 於第一個成功項（僅作為參考）
                if isinstance(photo_ids, list) and photo_ids:
                    for r in results:
                        if r.success:
                            r.photo_id = photo_ids[0]
                            break

                # 記錄日誌
                ok_count = sum(1 for r in results if r.success)
                fail_count = len(results) - ok_count
                logging.info(
                    f"📦 批次上傳完成：成功 {ok_count}、失敗 {fail_count}（批次大小 {len(file_paths)}）"
                )
                return results
            else:
                text = None
                try:
                    text = resp.text
                except Exception:
                    text = None
                last_error = f"HTTP {resp.status_code}: {text[:300] if text else 'No body'}"
                logging.warning(f"⚠️ 伺服器回應錯誤（批次）：{last_error}")
        except (requests.ConnectionError, requests.Timeout) as e:
            exc = e
            last_error = f"連線/逾時錯誤：{e}"
            logging.warning(f"⚠️ 請求失敗（批次）：{e}")
        except Exception as e:
            exc = e
            last_error = f"其他錯誤：{e}"
            logging.exception(f"❌ 未預期錯誤（批次）：{e}")

        if attempt <= max_retries and shouldRetry(last_status, exc):
            sleep_seconds = retry_backoff ** (attempt - 1)
            logging.info(f"批次重試第 {attempt}/{max_retries} 次前等待 {sleep_seconds:.1f}s")
            time.sleep(sleep_seconds)
            continue

        # 放棄重試，全部標失敗並回傳
        results: List[UploadResult] = [
            UploadResult(
                file_name=p.name,
                success=False,
                message="上傳失敗",
                error=last_error,
                status_code=last_status,
                file_path=str(p.resolve()),
                signature=getFileSignature(p),
            )
            for p in file_paths
        ]
        return results

def init_results_files() -> None:
    """初始化結果輸出檔案：若不存在，建立並寫入必要的表頭。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not RESULT_CSV.exists():
        with open(RESULT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["file_path", "file_name", "success", "message", "photo_id", "error", "status_code"])
        logging.info(f"建立結果 CSV：{RESULT_CSV}")

    if not SUCCESS_LIST.exists():
        SUCCESS_LIST.touch()
        logging.info(f"建立成功清單：{SUCCESS_LIST}")

    if not FAILURE_LIST.exists():
        FAILURE_LIST.touch()
        logging.info(f"建立失敗清單：{FAILURE_LIST}")

    if not HISTORY_CSV.exists():
        with open(HISTORY_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["signature", "event_id", "photo_id", "file_path", "uploaded_at"])
        logging.info(f"建立歷史紀錄 CSV：{HISTORY_CSV}")


def append_results(results: List[UploadResult], event_id: str, location: str) -> None:
    """將一批結果追加到輸出檔案（CSV、成功/失敗清單、歷史紀錄）。"""
    with WRITE_LOCK:
        # CSV 逐列追加
        with open(RESULT_CSV, "a", newline="", encoding="utf-8") as fcsv:
            writer = csv.writer(fcsv)
            for r in results:
                writer.writerow([
                    r.file_path or "",
                    r.file_name,
                    r.success,
                    r.message,
                    r.photo_id or "",
                    r.error or "",
                    r.status_code or "",
                ])

        # 成功/失敗逐列追加（修改：記錄絕對路徑以便精準重試）
        if results:
            with open(SUCCESS_LIST, "a", encoding="utf-8") as fsucc, open(FAILURE_LIST, "a", encoding="utf-8") as ffail:
                for r in results:
                    path_to_write = r.file_path or r.file_name
                    if r.success:
                        fsucc.write(path_to_write + "\n")
                    else:
                        ffail.write(path_to_write + "\n")

        # 歷史紀錄：僅針對成功
        with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            for r in results:
                if r.success and r.signature:
                    writer.writerow([
                        r.signature,
                        event_id,
                        r.photo_id or "",
                        r.file_path or "",
                        timestamp
                    ])


def collect_failures_to_reupload(root_dir: Path) -> List[Path]:
    """讀取 failure_list.txt 並判斷是路徑還是檔名，回傳待上傳清單。"""
    if not FAILURE_LIST.exists():
        logging.error(f"找不到失敗清單檔案：{FAILURE_LIST}")
        sys.exit(1)

    with open(FAILURE_LIST, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        logging.info("失敗清單是空的，沒有需要重新上傳的檔案。")
        return []

    files_to_reupload: List[Path] = []
    
    # 策略：
    # 1. 如果這一行看起來像絕對路徑且檔案存在，直接使用
    # 2. 如果只是檔名，則掃描 root_dir 尋找對應（可能會有多個同名檔案，建議全部加入嘗試上傳，或是僅第一個）
    #    由於舊版邏輯有缺陷（同名覆蓋），這裡改為：若為檔名，則收集所有同名檔案

    # 先掃描 root_dir 建立檔名索引（僅在有需要時才做，優化效能）
    name_map: Optional[dict[str, List[Path]]] = None

    def get_name_map() -> dict[str, List[Path]]:
        logging.info(f"正在掃描 {root_dir} 以匹配失敗的檔名…")
        m = {}
        for p in collectImageFiles(root_dir):
            m.setdefault(p.name, []).append(p)
        return m

    count_by_path = 0
    count_by_name = 0

    for line in lines:
        p = Path(line)
        if p.is_absolute() and p.exists():
            files_to_reupload.append(p)
            count_by_path += 1
        else:
            # 視為檔名（舊格式或相對路徑）
            if name_map is None:
                name_map = get_name_map()
            
            # 使用檔名匹配
            fname = p.name
            if fname in name_map:
                # 將所有同名檔案都加入重試，由後續的 Signature 機制去過濾重複
                for match_p in name_map[fname]:
                    files_to_reupload.append(match_p)
                count_by_name += 1
            else:
                logging.warning(f"找不到檔案（既非絕對路徑，也無同名檔案）：{line}")

    # 去重
    unique_files = list(set(files_to_reupload))
    logging.info(f"從失敗清單解析出 {len(unique_files)} 個待重試檔案（路徑匹配: {count_by_path}, 檔名匹配: {count_by_name}）")
    return unique_files


def read_history_keys(event_id: str) -> set:
    """讀取歷史紀錄，回傳已成功上傳過的 (signature, event_id) 集合。"""
    keys = set()
    if not HISTORY_CSV.exists():
        return keys
    try:
        with open(HISTORY_CSV, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            # csv header: signature, event_id, photo_id, file_path, uploaded_at
            for row in reader:
                if len(row) >= 2:
                    sig = row[0]
                    evt = row[1]
                    if evt == str(event_id):
                        keys.add((sig, evt))
    except Exception as e:
        logging.warning(f"讀取歷史紀錄失敗：{e}")
    return keys


def main(argv: Optional[List[str]] = None) -> None:
    args = parseArgs(argv)
    setupLogging()

    # 讀取 .env（若指定路徑則使用該檔案，否則嘗試載入預設 .env）
    if args.env_file:
        dotenv_path = Path(args.env_file).expanduser().resolve()
        if dotenv_path.exists():
            loaded = load_dotenv(dotenv_path=dotenv_path, override=False)
            if loaded:
                logging.info(f"已載入環境檔：{dotenv_path}")
            else:
                logging.warning(f"未能載入環境檔：{dotenv_path}")
        else:
            logging.warning(f"指定的 .env 檔案不存在：{dotenv_path}")
    else:
        loaded = load_dotenv()
        if loaded:
            logging.info("已載入 .env 環境變數（預設路徑）")

    # 由命令列參數與環境變數合併最終設定（CLI > ENV）
    env_dir = os.getenv("RACESHOT_DIR")
    env_event_id = os.getenv("RACESHOT_EVENT_ID")
    env_location = os.getenv("RACESHOT_LOCATION")
    env_price = os.getenv("RACESHOT_PRICE")
    env_bib = os.getenv("RACESHOT_BIB_NUMBER")
    env_max_retries = os.getenv("RACESHOT_MAX_RETRIES")
    env_retry_backoff = os.getenv("RACESHOT_RETRY_BACKOFF")
    env_timeout = os.getenv("RACESHOT_TIMEOUT")
    env_dry_run = os.getenv("RACESHOT_DRY_RUN")
    env_concurrency = os.getenv("RACESHOT_CONCURRENCY")
    env_batch_size = os.getenv("RACESHOT_BATCH_SIZE")
    env_longitude = os.getenv("RACESHOT_LONGITUDE")
    env_latitude = os.getenv("RACESHOT_LATITUDE")

    directory = args.directory or env_dir
    event_id = args.event_id or env_event_id
    location = args.location or env_location

    # 數值型參數解析（帶有防呆與預設值）
    price_eff: int
    if args.price is not None:
        price_eff = int(args.price)
    else:
        try:
            price_eff = int(env_price) if env_price is not None else DEFAULT_PRICE
        except Exception:
            logging.warning(f"RACESHOT_PRICE 無法解析為整數：{env_price!r}，改用預設 {DEFAULT_PRICE}")
            price_eff = DEFAULT_PRICE

    max_retries_eff: int
    if args.max_retries is not None:
        max_retries_eff = int(args.max_retries)
    else:
        try:
            max_retries_eff = int(env_max_retries) if env_max_retries is not None else 3
        except Exception:
            logging.warning(f"RACESHOT_MAX_RETRIES 無法解析為整數：{env_max_retries!r}，改用預設 3")
            max_retries_eff = 3

    retry_backoff_eff: float
    if args.retry_backoff is not None:
        retry_backoff_eff = float(args.retry_backoff)
    else:
        try:
            retry_backoff_eff = float(env_retry_backoff) if env_retry_backoff is not None else 1.5
        except Exception:
            logging.warning(f"RACESHOT_RETRY_BACKOFF 無法解析為浮點數：{env_retry_backoff!r}，改用預設 1.5")
            retry_backoff_eff = 1.5

    timeout_eff: float
    if args.timeout is not None:
        timeout_eff = float(args.timeout)
    else:
        try:
            timeout_eff = float(env_timeout) if env_timeout is not None else 30.0
        except Exception:
            logging.warning(f"RACESHOT_TIMEOUT 無法解析為浮點數：{env_timeout!r}，改用預設 30.0")
            timeout_eff = 30.0

    dry_run_eff = args.dry_run if args.dry_run else (parseBoolEnv(env_dry_run) or False)
    bib_number = args.bib_number or env_bib
    
    # 解析經緯度
    longitude_eff: Optional[float] = None
    if args.longitude is not None:
        longitude_eff = float(args.longitude)
    elif env_longitude is not None:
        try:
            longitude_eff = float(env_longitude)
        except Exception:
            logging.warning(f"RACESHOT_LONGITUDE 無法解析為浮點數：{env_longitude!r}")
    
    latitude_eff: Optional[float] = None
    if args.latitude is not None:
        latitude_eff = float(args.latitude)
    elif env_latitude is not None:
        try:
            latitude_eff = float(env_latitude)
        except Exception:
            logging.warning(f"RACESHOT_LATITUDE 無法解析為浮點數：{env_latitude!r}")
    # 解析併發與批次
    if args.concurrency is not None:
        concurrency_eff = max(1, int(args.concurrency))
    else:
        try:
            concurrency_eff = max(1, int(env_concurrency)) if env_concurrency is not None else 1
        except Exception:
            logging.warning(f"RACESHOT_CONCURRENCY 無法解析為整數：{env_concurrency!r}，改用 1")
            concurrency_eff = 1

    if args.batch_size is not None:
        batch_size_eff = max(1, int(args.batch_size))
    else:
        try:
            batch_size_eff = max(1, int(env_batch_size)) if env_batch_size is not None else 1
        except Exception:
            logging.warning(f"RACESHOT_BATCH_SIZE 無法解析為整數：{env_batch_size!r}，改用 1")
            batch_size_eff = 1

    # 檢查必填（允許從 .env 或 CLI 任一提供）
    missing: List[str] = []
    if not directory:
        missing.append("RACESHOT_DIR 或 --dir")
    if not event_id:
        missing.append("RACESHOT_EVENT_ID 或 --event-id")
    if not location:
        missing.append("RACESHOT_LOCATION 或 --location")
    if missing:
        logging.error("缺少必填參數：" + ", ".join(missing))
        sys.exit(1)

    token = getApiToken(args.token)
    root_dir = Path(directory).expanduser().resolve()

    files: List[Path]
    if args.reupload_failures:
        logging.info(f"--reupload-failures 啟用，將從 {FAILURE_LIST} 重新上傳失敗檔案")
        files = collect_failures_to_reupload(root_dir)
        if not files:
            logging.info("失敗清單中沒有需要重新上傳的檔案。")
            return
        # 清空舊的失敗清單，以便記錄本次執行的失敗
        with WRITE_LOCK:
            if FAILURE_LIST.exists():
                logging.info(f"清空舊的失敗清單：{FAILURE_LIST}")
                FAILURE_LIST.unlink()
            FAILURE_LIST.touch()
    else:
        files = collectImageFiles(root_dir)

    # 讀取歷史紀錄並過濾重複（同一 event_id 下同一檔案絕對路徑視為已上傳）
    # 讀取歷史紀錄並過濾重複（改用 Signature 檢查）
    init_results_files()
    history_keys = read_history_keys(event_id)
    
    # 計算並過濾
    final_files: List[Path] = []
    skipped_count = 0
    
    if not history_keys:
        final_files = files
    else:
        logging.info("正在檢查檔案特徵值以過濾重複…")
        for p in files:
            sig = getFileSignature(p)
            if (sig, str(event_id)) in history_keys:
                skipped_count += 1
            else:
                final_files.append(p)
    
    if skipped_count > 0:
        logging.info(f"跳過 {skipped_count} 張具相同特徵值的已上傳檔案（event_id={event_id}）")
    
    files = final_files

    if dry_run_eff:
        for p in files:
            logging.info(f"DRY-RUN 將上傳：{p} (Sig={getFileSignature(p)})")
        logging.info("Dry run 結束，未呼叫 API。")
        return

    results: List[UploadResult] = []
    if concurrency_eff == 1 and batch_size_eff == 1:
        # 舊行為：單執行緒、逐張
        session = requests.Session()
        for idx, file_path in enumerate(files, start=1):
            logging.info(f"({idx}/{len(files)}) 上傳 {file_path.name} 中…")
            result = uploadSingleImage(
                session=session,
                token=token,
                file_path=file_path,
                event_id=event_id,
                location=location,
                price=int(price_eff),
                bib_number=bib_number,
                timeout=float(timeout_eff),
                max_retries=int(max_retries_eff),
                retry_backoff=float(retry_backoff_eff),
                longitude=longitude_eff,
                latitude=latitude_eff,
            )
            results.append(result)
            # 逐檔即時寫入
            append_results([result], event_id=event_id, location=location)
    else:
        # 併發 + 批次（每個工作處理一個批次）
        batches = chunked(files, batch_size_eff)
        total_batches = len(batches)
        logging.info(f"啟用併發與/或批次上傳：concurrency={concurrency_eff}, batch_size={batch_size_eff}, 批次數={total_batches}")

        def process_batch(idx_batch: int, batch_paths: List[Path]) -> List[UploadResult]:
            logging.info(f"[批次 {idx_batch}/{total_batches}] 上傳 {len(batch_paths)} 張…")
            return uploadImagesBatch(
                token=token,
                file_paths=batch_paths,
                event_id=event_id,
                location=location,
                price=int(price_eff),
                bib_number=bib_number,
                timeout=float(timeout_eff),
                max_retries=int(max_retries_eff),
                retry_backoff=float(retry_backoff_eff),
                longitude=longitude_eff,
                latitude=latitude_eff,
            )

        with ThreadPoolExecutor(max_workers=concurrency_eff) as executor:
            future_to_idx = {
                executor.submit(process_batch, i + 1, batch): i for i, batch in enumerate(batches)
            }
            for fut in as_completed(future_to_idx):
                try:
                    batch_results = fut.result()
                    results.extend(batch_results)
                    # 逐批即時寫入
                    append_results(batch_results, event_id=event_id, location=location)
                except Exception as e:
                    logging.exception(f"批次處理發生未預期錯誤：{e}")

    total = len(results)
    ok = sum(1 for r in results if r.success)
    fail = total - ok
    logging.info(f"完成：成功 {ok}、失敗 {fail}、總計 {total}")


if __name__ == "__main__":
    main()
