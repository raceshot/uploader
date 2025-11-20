#!/usr/bin/env python3
"""
版本更新工具 - 跨平台支持
使用方式: python3 update_version.py patch|minor|major
"""

import sys
import subprocess
from pathlib import Path


def read_version():
    """讀取當前版本"""
    version_file = Path("VERSION")
    if not version_file.exists():
        print("ERROR: VERSION file not found")
        sys.exit(1)
    return version_file.read_text().strip()


def write_version(version):
    """寫入新版本"""
    version_file = Path("VERSION")
    version_file.write_text(version + "\n")


def parse_version(version_str):
    """解析版本字符串"""
    parts = version_str.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid version format: {version_str}")
    return [int(p) for p in parts]


def format_version(major, minor, patch):
    """格式化版本字符串"""
    return f"{major}.{minor}.{patch}"


def bump_version(current_version, bump_type):
    """更新版本號"""
    major, minor, patch = parse_version(current_version)

    if bump_type == "patch":
        patch += 1
    elif bump_type == "minor":
        minor += 1
        patch = 0
    elif bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        raise ValueError(f"Invalid bump type: {bump_type}")

    return format_version(major, minor, patch)


def run_command(cmd):
    """執行 shell 命令"""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}")
        sys.exit(1)
    return result.stdout.strip()


def main():
    if len(sys.argv) < 2:
        print("使用方式: python3 update_version.py patch|minor|major")
        print("例如: python3 update_version.py patch  # 1.0.0 -> 1.0.1")
        print("例如: python3 update_version.py minor  # 1.0.0 -> 1.1.0")
        print("例如: python3 update_version.py major  # 1.0.0 -> 2.0.0")
        sys.exit(1)

    bump_type = sys.argv[1]
    if bump_type not in ["patch", "minor", "major"]:
        print(f"ERROR: 無效的版本類型。使用 patch|minor|major")
        sys.exit(1)

    # 讀取當前版本
    current_version = read_version()
    print(f"當前版本: {current_version}")

    # 計算新版本
    new_version = bump_version(current_version, bump_type)
    print(f"新版本: {new_version}")

    # 更新 VERSION 檔案
    write_version(new_version)

    # 提交更改
    run_command("git add VERSION")
    run_command(f'git commit -m "chore: bump version to {new_version}"')

    # 建立 tag
    run_command(f'git tag -a "v{new_version}" -m "Release version {new_version}"')

    print(f"✅ 版本已更新到 {new_version}")
    print(f"📌 Tag 已建立: v{new_version}")
    print(f"🚀 執行 'git push origin main --tags' 以推送到 GitHub")


if __name__ == "__main__":
    main()
