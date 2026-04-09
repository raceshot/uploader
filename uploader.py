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
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from PIL import Image, ExifTags

import requests
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import hashlib

API_BASE = os.getenv("RACESHOT_API_BASE", "https://api.raceshot.app")
API_ENDPOINT = f"{API_BASE}/api/v1/photographer/upload"
HOST_API_ENDPOINT = f"{API_BASE}/api/v1/host/albums/upload/photo"
VERIFY_TOKEN_ENDPOINT = f"{API_BASE}/api/v1/photographer/api/verify"
LIST_EVENTS_ENDPOINT = f"{API_BASE}/api/v1/photographer/api/events"
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


@dataclass
class GpxTrackPoint:
    timestamp_ms: int
    latitude: float
    longitude: float


@dataclass
class PhotoMatchInfo:
    file_path: Path
    capture_timestamp_ms: Optional[int]
    latitude: Optional[float]
    longitude: Optional[float]
    source: str
    message: str


@dataclass
class GpxProcessingContext:
    track_points: List[GpxTrackPoint]
    time_offset_seconds: int = 0
    fallback_latitude: Optional[float] = None
    fallback_longitude: Optional[float] = None
    fallback_mode: str = "manual"
    max_gap_seconds: int = 300


EXIF_TAGS_BY_NAME = {name: tag for tag, name in ExifTags.TAGS.items()}
DEFAULT_CAPTURE_TZ = timezone(timedelta(hours=8))


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

def verifyToken(token: str, timeout: float = 10.0) -> Tuple[bool, Optional[dict], str]:
    """呼叫後端驗證 API Token 是否有效"""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(VERIFY_TOKEN_ENDPOINT, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            payload = resp.json()
            if payload.get("valid"):
                return True, payload.get("user"), "Token 有效"
            return False, None, payload.get("error", "驗證失敗")
        else:
            payload = resp.json() if resp.text else {}
            return False, None, payload.get("error", f"HTTP {resp.status_code}")
    except Exception as e:
        return False, None, f"連線錯誤: {e}"

def listEvents(token: str, timeout: float = 10.0) -> Tuple[bool, List[dict], str]:
    """呼叫後端獲取可用的活動列表"""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(LIST_EVENTS_ENDPOINT, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            payload = resp.json()
            return True, payload.get("events", []), "Success"
        else:
            payload = resp.json() if resp.text else {}
            return False, [], payload.get("error", f"HTTP {resp.status_code}")
    except Exception as e:
        return False, [], f"連線錯誤: {e}"


def parseArgs(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Raceshot 圖片上傳工具")
    parser.add_argument("--dir", dest="directory", required=False, default=None, help="要遞迴上傳的圖片資料夾（可用環境變數 RACESHOT_DIR）")
    parser.add_argument("--event-id", dest="event_id", required=False, default=None, help="活動 ID（可用環境變數 RACESHOT_EVENT_ID）")
    parser.add_argument("--location", dest="location", required=False, default=None, help="拍攝地點（可用環境變數 RACESHOT_LOCATION）")
    parser.add_argument("--price", dest="price", type=int, default=None, help=argparse.SUPPRESS)
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
    parser.add_argument("--gpx-file", dest="gpx_file", default=None, help="GPX 軌跡檔路徑，會依照片 EXIF 時間自動匹配座標")
    parser.add_argument("--gpx-time-offset", dest="gpx_time_offset", type=int, default=None, help="照片時間校正秒數，可為負值")
    parser.add_argument(
        "--gpx-fallback-mode",
        dest="gpx_fallback_mode",
        choices=["manual", "empty"],
        default=None,
        help="GPX 無法匹配時的座標策略：manual=改用手動座標，empty=留空",
    )
    parser.add_argument(
        "--gpx-max-gap",
        dest="gpx_max_gap",
        type=int,
        default=None,
        help="相鄰 GPX 點允許的最大時間差（秒），超過則不自動匹配",
    )
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


def parse_exif_datetime(
    date_str: Optional[str],
    offset_str: Optional[str],
    subsec_str: Optional[str],
) -> Optional[int]:
    """將 EXIF 日期時間轉成 UTC milliseconds。無時區時預設採用 UTC+8。"""
    if not date_str:
        return None

    try:
        dt = datetime.strptime(str(date_str).strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None

    microseconds = 0
    if subsec_str:
        digits = "".join(ch for ch in str(subsec_str).strip() if ch.isdigit())
        if digits:
            microseconds = int((digits + "000000")[:6])
            dt = dt.replace(microsecond=microseconds)

    tzinfo = DEFAULT_CAPTURE_TZ
    if offset_str:
        offset_text = str(offset_str).strip()
        sign = 1
        if offset_text.startswith("-"):
            sign = -1
        cleaned = offset_text[1:] if offset_text[:1] in {"+", "-"} else offset_text
        parts = cleaned.split(":")
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            hours = int(parts[0])
            minutes = int(parts[1])
            tzinfo = timezone(sign * timedelta(hours=hours, minutes=minutes))

    aware_dt = dt.replace(tzinfo=tzinfo)
    return int(aware_dt.astimezone(timezone.utc).timestamp() * 1000)


def extract_capture_timestamp(file_path: Path) -> Optional[int]:
    """從照片 EXIF 讀取拍攝時間，優先使用 DateTimeOriginal。"""
    try:
        with Image.open(file_path) as img:
            exif = img.getexif()
    except Exception as e:
        logging.warning(f"無法讀取 EXIF：{file_path} - {e}")
        return None

    if not exif:
        return None

    date_str = exif.get(EXIF_TAGS_BY_NAME.get("DateTimeOriginal"))
    if not date_str:
        date_str = exif.get(EXIF_TAGS_BY_NAME.get("DateTimeDigitized"))
    if not date_str:
        date_str = exif.get(EXIF_TAGS_BY_NAME.get("DateTime"))

    offset_str = exif.get(EXIF_TAGS_BY_NAME.get("OffsetTimeOriginal"))
    if not offset_str:
        offset_str = exif.get(EXIF_TAGS_BY_NAME.get("OffsetTimeDigitized"))
    if not offset_str:
        offset_str = exif.get(EXIF_TAGS_BY_NAME.get("OffsetTime"))

    subsec_str = exif.get(EXIF_TAGS_BY_NAME.get("SubsecTimeOriginal"))
    if not subsec_str:
        subsec_str = exif.get(EXIF_TAGS_BY_NAME.get("SubsecTimeDigitized"))
    if not subsec_str:
        subsec_str = exif.get(EXIF_TAGS_BY_NAME.get("SubsecTime"))

    capture_timestamp = parse_exif_datetime(date_str, offset_str, subsec_str)
    if capture_timestamp is None:
        logging.warning(f"EXIF 時間格式無法解析：{file_path}")
    return capture_timestamp


def parse_gpx_track(gpx_path: Path) -> List[GpxTrackPoint]:
    """解析含 time 的 GPX 軌跡，回傳依時間排序的點。"""
    try:
        tree = ET.parse(gpx_path)
    except Exception as e:
        raise ValueError(f"GPX 解析失敗：{e}") from e

    root = tree.getroot()
    track_points: List[GpxTrackPoint] = []

    for element in root.iter():
        if not element.tag.endswith("trkpt"):
            continue

        lat_raw = element.attrib.get("lat")
        lon_raw = element.attrib.get("lon")
        if lat_raw is None or lon_raw is None:
            continue

        time_text = None
        for child in element:
            if child.tag.endswith("time") and child.text:
                time_text = child.text.strip()
                break
        if not time_text:
            continue

        try:
            timestamp_ms = int(datetime.fromisoformat(time_text.replace("Z", "+00:00")).timestamp() * 1000)
            track_points.append(
                GpxTrackPoint(
                    timestamp_ms=timestamp_ms,
                    latitude=float(lat_raw),
                    longitude=float(lon_raw),
                )
            )
        except Exception:
            continue

    track_points.sort(key=lambda point: point.timestamp_ms)
    return track_points


def interpolate_track_point(
    track_points: List[GpxTrackPoint],
    target_timestamp_ms: int,
    max_gap_seconds: int = 300,
) -> Tuple[Optional[float], Optional[float], str]:
    """依照片時間在線性插值出 GPX 座標。"""
    if not track_points:
        return None, None, "GPX 沒有可用的時間點"

    if target_timestamp_ms < track_points[0].timestamp_ms or target_timestamp_ms > track_points[-1].timestamp_ms:
        return None, None, "照片時間超出 GPX 時間範圍"

    for index, current in enumerate(track_points):
        if current.timestamp_ms == target_timestamp_ms:
            return current.latitude, current.longitude, "精確匹配 GPX 時間點"

        if current.timestamp_ms > target_timestamp_ms:
            previous = track_points[index - 1]
            gap_ms = current.timestamp_ms - previous.timestamp_ms
            if gap_ms <= 0:
                return previous.latitude, previous.longitude, "使用鄰近 GPX 點"
            if gap_ms > max_gap_seconds * 1000:
                return None, None, f"相鄰 GPX 點間隔超過 {max_gap_seconds} 秒"

            ratio = (target_timestamp_ms - previous.timestamp_ms) / gap_ms
            latitude = previous.latitude + (current.latitude - previous.latitude) * ratio
            longitude = previous.longitude + (current.longitude - previous.longitude) * ratio
            return latitude, longitude, "依 GPX 時間線性插值"

    last = track_points[-1]
    return last.latitude, last.longitude, "使用最後一個 GPX 點"


def load_gpx_processing_context(
    gpx_file: Optional[Path],
    time_offset_seconds: int = 0,
    fallback_latitude: Optional[float] = None,
    fallback_longitude: Optional[float] = None,
    fallback_mode: str = "manual",
    max_gap_seconds: int = 300,
) -> Optional[GpxProcessingContext]:
    """預先載入 GPX 軌跡，供逐張配對使用。"""
    if gpx_file is None:
        return None

    track_points = parse_gpx_track(gpx_file)
    if len(track_points) < 2:
        raise ValueError("GPX 軌跡點不足，至少需要 2 個含時間的 trkpt")

    return GpxProcessingContext(
        track_points=track_points,
        time_offset_seconds=time_offset_seconds,
        fallback_latitude=fallback_latitude,
        fallback_longitude=fallback_longitude,
        fallback_mode=fallback_mode,
        max_gap_seconds=max_gap_seconds,
    )


def match_photo_with_context(file_path: Path, context: Optional[GpxProcessingContext]) -> Optional[PhotoMatchInfo]:
    """對單張照片做 GPX 對時。"""
    if context is None:
        return None

    capture_timestamp_ms = extract_capture_timestamp(file_path)
    if capture_timestamp_ms is None:
        if context.fallback_mode == "manual" and context.fallback_latitude is not None and context.fallback_longitude is not None:
            return PhotoMatchInfo(
                file_path=file_path,
                capture_timestamp_ms=None,
                latitude=context.fallback_latitude,
                longitude=context.fallback_longitude,
                source="manual-fallback",
                message="缺少 EXIF 拍攝時間，改用手動座標",
            )
        return PhotoMatchInfo(
            file_path=file_path,
            capture_timestamp_ms=None,
            latitude=None,
            longitude=None,
            source="unmatched",
            message="缺少 EXIF 拍攝時間，無法匹配 GPX",
        )

    adjusted_timestamp_ms = capture_timestamp_ms + (context.time_offset_seconds * 1000)
    latitude, longitude, reason = interpolate_track_point(
        track_points=context.track_points,
        target_timestamp_ms=adjusted_timestamp_ms,
        max_gap_seconds=context.max_gap_seconds,
    )

    if latitude is not None and longitude is not None:
        return PhotoMatchInfo(
            file_path=file_path,
            capture_timestamp_ms=adjusted_timestamp_ms,
            latitude=latitude,
            longitude=longitude,
            source="gpx",
            message=reason,
        )

    if context.fallback_mode == "manual" and context.fallback_latitude is not None and context.fallback_longitude is not None:
        return PhotoMatchInfo(
            file_path=file_path,
            capture_timestamp_ms=adjusted_timestamp_ms,
            latitude=context.fallback_latitude,
            longitude=context.fallback_longitude,
            source="manual-fallback",
            message=f"{reason}，改用手動座標",
        )

    return PhotoMatchInfo(
        file_path=file_path,
        capture_timestamp_ms=adjusted_timestamp_ms,
        latitude=None,
        longitude=None,
        source="unmatched",
        message=reason,
    )


def build_photo_match_map(
    files: List[Path],
    gpx_file: Optional[Path],
    time_offset_seconds: int = 0,
    fallback_latitude: Optional[float] = None,
    fallback_longitude: Optional[float] = None,
    fallback_mode: str = "manual",
    max_gap_seconds: int = 300,
) -> Tuple[dict[Path, PhotoMatchInfo], dict[str, int]]:
    """建立每張照片的座標匹配結果。"""
    match_map: dict[Path, PhotoMatchInfo] = {}
    summary = {
        "matched": 0,
        "fallback_manual": 0,
        "missing_exif": 0,
        "outside_track": 0,
        "unmatched": 0,
    }

    context = load_gpx_processing_context(
        gpx_file=gpx_file,
        time_offset_seconds=time_offset_seconds,
        fallback_latitude=fallback_latitude,
        fallback_longitude=fallback_longitude,
        fallback_mode=fallback_mode,
        max_gap_seconds=max_gap_seconds,
    )
    if context is None:
        return match_map, summary

    for file_path in files:
        match_info = match_photo_with_context(file_path, context)
        if match_info is None:
            continue
        match_map[file_path] = match_info
        if match_info.source == "gpx":
            summary["matched"] += 1
        elif match_info.source == "manual-fallback":
            summary["fallback_manual"] += 1
        else:
            summary["unmatched"] += 1
            if match_info.capture_timestamp_ms is None:
                summary["missing_exif"] += 1
        if "超出 GPX 時間範圍" in match_info.message:
            summary["outside_track"] += 1

    return match_map, summary


def collectImageFiles(root_dir: Path) -> List[Path]:
    if not root_dir.exists() or not root_dir.is_dir():
        logging.error(f"指定路徑不存在或不是資料夾：{root_dir}")
        sys.exit(1)

    files: List[Path] = []
    try:
        # 使用 rglob 掃描
        for path in root_dir.rglob("*"):
            try:
                # 忽略 . 開頭的 hidden files
                if path.name.startswith("."):
                    continue
                if path.is_file():
                    ext = path.suffix.lower()
                    if ext in ALLOWED_EXTENSIONS:
                        files.append(path)
            except OSError as e:
                logging.warning(f"無法存取檔案（略過）：{path} - {e}")
                continue
    except OSError as e:
        logging.error(f"掃描資料夾時發生 I/O 錯誤：{e}")
        # 若整個資料夾讀取失敗，可能還是要讓使用者知道，但至少不要 crash 得太難看，
        # 或者視為已找到的部分檔案繼續做。
        # 這裡選擇僅記錄錯誤，回傳已找到的。

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
    endpoint: Optional[str] = None,
) -> UploadResult:
    target_endpoint = endpoint or API_ENDPOINT
    headers = {"Authorization": f"Bearer {token}"}

    # Ensure the 'org_' prefix is removed if present
    clean_event_id = str(event_id)
    if clean_event_id.startswith("org_"):
        clean_event_id = clean_event_id[4:]

    form_data = {
        "eventId": clean_event_id,
        "album_id": clean_event_id, # Host API specifically looks for album_id
        "location": str(location),
        "price": str(price),
    }
    if bib_number:
        form_data["bibNumber"] = str(bib_number)
    if longitude is not None:
        form_data["longitude"] = str(longitude)
    if latitude is not None:
        form_data["latitude"] = str(latitude)

    image_file = buildMultipart(file_path)
    images_field = [
        ("file", image_file[1]),
        ("image", image_file[1]),
        ("images", image_file[1])
    ]

    attempt = 0
    last_error: Optional[str] = None
    last_status: Optional[int] = None
    while True:
        attempt += 1
        exc: Optional[BaseException] = None
        try:
            resp = session.post(
                target_endpoint,
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

            if payload is not None:
                # 依照題目回傳格式處理
                success = bool(payload.get("success")) or payload.get("status") == "success"
                message = str(payload.get("message") or payload.get("error") or "")
                photo_ids = payload.get("photoIds") or []
                if not photo_ids and payload.get("photo_id"):
                    photo_ids = [payload.get("photo_id")]
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
                        signature="",
                    )
                else:
                    # 失敗情境
                    is_dup, dup_pid = False, None
                    if isinstance(failure_items, list) and failure_items:
                        f0 = failure_items[0]
                        err_msg = f0.get("error") or message or "上傳失敗"
                        is_dup, dup_pid = isDuplicateFailure(err_msg, f0)
                    else:
                        err_msg = message or f"HTTP {resp.status_code}"
                        is_dup, dup_pid = isDuplicateFailure(err_msg, payload or {})

                    if is_dup or resp.status_code == 409:
                        logging.info(f"☑️ 已上傳（視為成功）：{file_path.name}")
                        return UploadResult(
                            file_name=file_path.name,
                            success=True,
                            message="已上傳（視為成功）",
                            photo_id=dup_pid or payload.get("photoId") or payload.get("photo_id"),
                            status_code=resp.status_code,
                            file_path=str(file_path.resolve()),
                            signature="",
                        )

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
                            signature="",
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
            signature="",
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
    endpoint: Optional[str] = None,
) -> List[UploadResult]:
    """一次上傳多張圖片（同一請求），回傳逐檔結果清單。"""
    target_endpoint = endpoint or API_ENDPOINT
    headers = {"Authorization": f"Bearer {token}"}
    # Ensure the 'org_' prefix is removed if present
    clean_event_id = str(event_id)
    if clean_event_id.startswith("org_"):
        clean_event_id = clean_event_id[4:]

    form_data = {
        "eventId": clean_event_id,
        "album_id": clean_event_id,
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
        file_tuple = buildMultipart(p)
        files_field.append(("images", file_tuple[1]))
        files_field.append(("image", file_tuple[1]))
        files_field.append(("file", file_tuple[1]))

    attempt = 0
    last_error: Optional[str] = None
    last_status: Optional[int] = None
    session = requests.Session()
    while True:
        attempt += 1
        exc: Optional[BaseException] = None
        try:
            resp = session.post(
                target_endpoint,
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
                    signature=""
                )
                for p in file_paths
            ]

            if payload is not None:
                success = bool(payload.get("success")) or payload.get("status") == "success"
                message = str(payload.get("message") or payload.get("error") or "")
                photo_ids = payload.get("photoIds") or []
                if not photo_ids and payload.get("photo_id"):
                    photo_ids = [payload.get("photo_id")]
                failure_items = payload.get("failures") or []

                # 將失敗項目以 fileName 建立查表
                failed_by_name: dict[str, str] = {}
                dup_by_name: dict[str, Optional[str]] = {}
                unmapped_failure_count = 0
                
                # 建立一個正規化檔名的對照表 (basename -> list of original names)
                # 以便處理伺服器可能只回傳 basename 的情況
                name_map = {}
                for p in file_paths:
                    name_map.setdefault(p.name, []).append(p.name)

                for f in failure_items if isinstance(failure_items, list) else []:
                    fn = f.get("fileName") or f.get("filename")
                    err = f.get("error") or message or "上傳失敗"
                    is_dup, dup_pid = isDuplicateFailure(err, f)
                    
                    if fn:
                        # 嘗試精確比對
                        if is_dup:
                            dup_by_name[fn] = dup_pid
                        else:
                            failed_by_name[fn] = err
                        
                        # 若原始檔名沒對上，嘗試用 basename 比對
                        # (有些後端實作可能會去掉路徑)
                        if fn not in [p.name for p in file_paths]:
                             # 這裡簡化處理：若 batch 內有同名檔案，可能無法精確區分哪一個失敗
                             # 但至少標記為失敗
                            unmapped_failure_count += 1
                    else:
                        # 無檔名的失敗，視為全域變數處理，稍後若還有成功狀態的則強制標記
                        unmapped_failure_count += 1

                # 邏輯修正：如果 top-level success 為 False，且 failed_by_name 為空 (或不足)，
                # 則應該將所有「未被標記為重複」的項目都視為失敗。
                
                has_global_failure = not success and not failure_items

                # 先標記『已上傳』為成功
                for r in results:
                    if r.file_name in dup_by_name:
                        r.success = True
                        r.message = "已上傳（視為成功）"
                        r.photo_id = dup_by_name[r.file_name]

                # 再標記由 Server 明確指出的失敗
                for r in results:
                    if r.file_name in failed_by_name:
                        r.success = False
                        r.error = failed_by_name[r.file_name]
                        r.message = message or r.error or "上傳失敗"
                
                # 若發生全域失敗 (success=False 且無細節)，則將剩餘看似成功的都標為失敗
                if has_global_failure:
                    for r in results:
                        if r.success and r.file_name not in dup_by_name and not r.error:
                             r.success = False
                             r.error = message or "批次上傳失敗（未知原因）"
                             r.message = r.error

                # 其餘標記為成功
                for r in results:
                    # 只有當沒有 error 且尚未被標記成功(如重複)，才設為成功
                    if r.success == False and r.message == "上傳失敗" and not r.error:
                        # 這是初始狀態，若沒被標記失敗，則視為成功
                         r.success = True
                         r.message = message or "上傳成功"
                         r.photo_id = None

                # 若回傳有未知的失敗數（沒有 fileName），將尚未標記的成功項目回填為失敗以符合計數
                if unmapped_failure_count > 0:
                    patched = 0
                    for r in results:
                        if patched >= unmapped_failure_count:
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
                            # 避免覆蓋已有的 dup id
                            if not r.photo_id:
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
                signature="",
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
        try:
            p = Path(line)
            # 檢查路徑是否存在（可能會引發 OSError）
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
        except OSError as e:
            logging.warning(f"處理失敗清單項目時發生錯誤（略過）：{line} - {e}")
            continue

    # 去重
    unique_files = list(set(files_to_reupload))
    logging.info(f"從失敗清單解析出 {len(unique_files)} 個待重試檔案（路徑匹配: {count_by_path}, 檔名匹配: {count_by_name}）")
    return unique_files


def read_history_keys(event_id: str) -> set:
    """讀取歷史紀錄，回傳已成功上傳過的 (file_path, event_id) 集合。"""
    keys = set()
    if not HISTORY_CSV.exists():
        return keys
    try:
        with open(HISTORY_CSV, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            # csv header: signature, event_id, photo_id, file_path, uploaded_at
            for row in reader:
                if len(row) >= 4:
                    evt = row[1]
                    fpath = row[3]
                    if evt == str(event_id):
                        keys.add((fpath, evt))
    except Exception as e:
        logging.warning(f"讀取歷史紀錄失敗：{e}")
    return keys


def clear_event_history(event_id: str) -> int:
    """
    清除指定 Event ID 的上傳紀錄。
    回傳清除的筆數。
    """
    if not HISTORY_CSV.exists():
        return 0

    removed_count = 0
    temp_rows = []
    
    with WRITE_LOCK:
        try:
            with open(HISTORY_CSV, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if header:
                    temp_rows.append(header)
                
                for row in reader:
                    # csv header: signature, event_id, photo_id, file_path, uploaded_at
                    # row[1] is event_id
                    if len(row) >= 2 and row[1] == str(event_id):
                        removed_count += 1
                        continue
                    temp_rows.append(row)
            
            # 寫回檔案
            if removed_count > 0:
                with open(HISTORY_CSV, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerows(temp_rows)
                logging.info(f"已清除 Event ID={event_id} 的 {removed_count} 筆歷史紀錄")
        except Exception as e:
            logging.error(f"清除歷史紀錄時發生錯誤：{e}")
            raise e

    return removed_count


def consume_limited_futures(
    executor: ThreadPoolExecutor,
    items: List,
    submit_func,
    max_in_flight: int,
):
    """控制同時在途的 future 數量，避免大量檔案時記憶體與排程壓力過大。"""
    if max_in_flight <= 0:
        max_in_flight = 1

    item_iter = iter(items)
    future_map = {}

    while len(future_map) < max_in_flight:
        try:
            item = next(item_iter)
        except StopIteration:
            break
        future = submit_func(item)
        future_map[future] = item

    while future_map:
        for future in as_completed(list(future_map.keys()), timeout=None):
            item = future_map.pop(future)
            yield item, future
            try:
                next_item = next(item_iter)
            except StopIteration:
                next_item = None
            if next_item is not None:
                next_future = submit_func(next_item)
                future_map[next_future] = next_item
            break


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
    env_gpx_file = os.getenv("RACESHOT_GPX_FILE")
    env_gpx_time_offset = os.getenv("RACESHOT_GPX_TIME_OFFSET")
    env_gpx_fallback_mode = os.getenv("RACESHOT_GPX_FALLBACK_MODE")
    env_gpx_max_gap = os.getenv("RACESHOT_GPX_MAX_GAP")

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

    gpx_file_eff: Optional[Path] = None
    raw_gpx_file = args.gpx_file or env_gpx_file
    if raw_gpx_file:
        gpx_file_eff = Path(raw_gpx_file).expanduser().resolve()

    if args.gpx_time_offset is not None:
        gpx_time_offset_eff = int(args.gpx_time_offset)
    else:
        try:
            gpx_time_offset_eff = int(env_gpx_time_offset) if env_gpx_time_offset is not None else 0
        except Exception:
            logging.warning(f"RACESHOT_GPX_TIME_OFFSET 無法解析為整數：{env_gpx_time_offset!r}，改用 0")
            gpx_time_offset_eff = 0

    gpx_fallback_mode_eff = args.gpx_fallback_mode or env_gpx_fallback_mode or "manual"
    if gpx_fallback_mode_eff not in {"manual", "empty"}:
        logging.warning(f"RACESHOT_GPX_FALLBACK_MODE 無效：{gpx_fallback_mode_eff!r}，改用 manual")
        gpx_fallback_mode_eff = "manual"

    if args.gpx_max_gap is not None:
        gpx_max_gap_eff = max(1, int(args.gpx_max_gap))
    else:
        try:
            gpx_max_gap_eff = max(1, int(env_gpx_max_gap)) if env_gpx_max_gap is not None else 300
        except Exception:
            logging.warning(f"RACESHOT_GPX_MAX_GAP 無法解析為整數：{env_gpx_max_gap!r}，改用 300")
            gpx_max_gap_eff = 300
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

    if gpx_file_eff and not gpx_file_eff.exists():
        logging.error(f"指定的 GPX 檔案不存在：{gpx_file_eff}")
        sys.exit(1)

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

    gpx_context = None
    if gpx_file_eff:
        logging.info(f"GPX 自動座標模式啟用：{gpx_file_eff}")
        preview_files = files[: min(len(files), 200)]
        _, match_summary = build_photo_match_map(
            files=preview_files,
            gpx_file=gpx_file_eff,
            time_offset_seconds=gpx_time_offset_eff,
            fallback_latitude=latitude_eff,
            fallback_longitude=longitude_eff,
            fallback_mode=gpx_fallback_mode_eff,
            max_gap_seconds=gpx_max_gap_eff,
        )
        gpx_context = load_gpx_processing_context(
            gpx_file=gpx_file_eff,
            time_offset_seconds=gpx_time_offset_eff,
            fallback_latitude=latitude_eff,
            fallback_longitude=longitude_eff,
            fallback_mode=gpx_fallback_mode_eff,
            max_gap_seconds=gpx_max_gap_eff,
        )
        logging.info(
            f"GPX 預檢結果（前 {len(preview_files)} 張）："
            f"成功 {match_summary['matched']}，"
            f"手動回退 {match_summary['fallback_manual']}，"
            f"缺少 EXIF {match_summary['missing_exif']}，"
            f"超出範圍 {match_summary['outside_track']}，"
            f"未匹配 {match_summary['unmatched']}"
        )
        if batch_size_eff != 1:
            logging.info("GPX 模式需逐張帶入不同座標，已自動將 batch_size 調整為 1")
            batch_size_eff = 1

    if dry_run_eff:
        for p in files:
            match_info = match_photo_with_context(p, gpx_context)
            if match_info:
                logging.info(
                    f"DRY-RUN 將上傳：{p} "
                    f"(lat={match_info.latitude}, lon={match_info.longitude}, source={match_info.source}, note={match_info.message})"
                )
            else:
                logging.info(f"DRY-RUN 將上傳：{p} (Sig={getFileSignature(p)})")
        logging.info("Dry run 結束，未呼叫 API。")
        return

    results: List[UploadResult] = []
    if batch_size_eff == 1:
        # 舊行為：單執行緒、逐張
        def upload_one(idx: int, file_path: Path) -> UploadResult:
            match_info = match_photo_with_context(file_path, gpx_context)
            effective_longitude = longitude_eff
            effective_latitude = latitude_eff
            if match_info:
                effective_latitude = match_info.latitude
                effective_longitude = match_info.longitude
                logging.info(
                    f"({idx}/{len(files)}) 上傳 {file_path.name} 中… "
                    f"[{match_info.source}] {match_info.message}"
                )
            else:
                logging.info(f"({idx}/{len(files)}) 上傳 {file_path.name} 中…")

            session = requests.Session()
            return uploadSingleImage(
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
                longitude=effective_longitude,
                latitude=effective_latitude,
                endpoint=API_ENDPOINT,
            )

        if concurrency_eff == 1:
            for idx, file_path in enumerate(files, start=1):
                result = upload_one(idx, file_path)
                results.append(result)
                append_results([result], event_id=event_id, location=location)
        else:
            with ThreadPoolExecutor(max_workers=concurrency_eff) as executor:
                indexed_files = list(enumerate(files, start=1))

                def submit_indexed(item):
                    idx, file_path = item
                    return executor.submit(upload_one, idx, file_path)

                for item, future in consume_limited_futures(
                    executor=executor,
                    items=indexed_files,
                    submit_func=submit_indexed,
                    max_in_flight=max(concurrency_eff * 2, 4),
                ):
                    try:
                        result = future.result()
                        results.append(result)
                        append_results([result], event_id=event_id, location=location)
                    except Exception as e:
                        idx, file_path = item
                        logging.exception(f"單張上傳失敗：{file_path} - {e}")
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
