"""
升级管理服务 - 备份、解压覆盖、回滚、记录管理

支持 POST ZIP 包升级，自动备份当前代码、校验版本、回滚。
"""

import os
import json
import zipfile
import shutil
import time
import threading
from datetime import datetime
from pathlib import Path

# 项目根目录 (asap_adapter/ 的父目录)
BASE_DIR = Path(__file__).parent.parent
BACKUP_DIR = BASE_DIR / "backup"
UPGRADE_LOG_FILE = BACKUP_DIR / "upgrade_log.json"

MAX_BACKUPS = 10
SERVICE_NAME = "asap_adapter"

# 排除覆盖的文件/目录
EXCLUDE_PATTERNS = [
    "config/env.toml",
    "config/old/",
    "data/config.toml",
    "venv/",
    "logs/",
    "backup/",
    "dev/",
    "test/",
    ".git/",
    ".gitignore",
    "skill.md",
    "README.md",
    "deploy_iraypleos/",
    "__pycache__/",
    "*.pyc",
]


def _should_exclude(relative_path: str) -> bool:
    """判断文件是否应被排除（不解压覆盖）"""
    rel = relative_path.replace("\\", "/")
    for pattern in EXCLUDE_PATTERNS:
        if pattern.endswith("/"):
            if rel.startswith(pattern) or rel == pattern.rstrip("/"):
                return True
        else:
            if rel == pattern:
                return True
    return False


def _get_app_version() -> str:
    """从 asap_adapter/__init__.py 读取版本号"""
    try:
        init_file = BASE_DIR / "asap_adapter" / "__init__.py"
        with open(init_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("__version__"):
                    return line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "unknown"


def _read_upgrade_log() -> list:
    """读取升级记录"""
    if not UPGRADE_LOG_FILE.exists():
        return []
    try:
        with open(UPGRADE_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _write_upgrade_log(records: list):
    """写入升级记录"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with open(UPGRADE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def _cleanup_old_backups():
    """清理超出 MAX_BACKUPS 的旧备份"""
    records = _read_upgrade_log()
    upgrade_records = [r for r in records if r.get("backup_name", "").startswith("upgrade_")]
    if len(upgrade_records) <= MAX_BACKUPS:
        return

    to_remove = sorted(upgrade_records, key=lambda r: r.get("timestamp", ""))[:-MAX_BACKUPS]
    remove_names = {r["backup_name"] for r in to_remove}

    for name in remove_names:
        backup_path = BACKUP_DIR / name
        if backup_path.is_dir():
            shutil.rmtree(backup_path)

    records = [r for r in records if r.get("backup_name", "") not in remove_names]
    _write_upgrade_log(records)


def _backup_project_files(backup_path: Path):
    """备份当前项目文件"""
    files_dir = backup_path / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    for root, dirs, files in os.walk(BASE_DIR):
        for file in files:
            src_path = Path(root) / file
            rel_path = src_path.relative_to(BASE_DIR)

            if _should_exclude(str(rel_path)):
                continue

            dst_path = files_dir / rel_path
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)


def _read_version_json(extract_dir: Path) -> dict:
    """从解压目录读取 version.json"""
    vj_path = extract_dir / "version.json"
    if not vj_path.exists():
        return {}
    try:
        with open(vj_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        vj_path.unlink()  # 删除 version.json，防止覆盖到项目目录
        return data
    except Exception:
        return {}


def get_version_info() -> dict:
    """获取服务器版本信息"""
    return {
        "app_version": _get_app_version(),
    }


def get_upgrade_records() -> list:
    """获取升级记录列表"""
    return _read_upgrade_log()


def get_exclude_patterns() -> list:
    """获取排除列表"""
    return EXCLUDE_PATTERNS


# ── 执行升级 ──────────────────────────────

def do_upgrade(zip_path: str, remark: str = "") -> dict:
    """
    执行升级
    zip_path: 上传的 ZIP 文件路径
    remark:   可选的升级备注
    返回: {"success": True/False, "message": "...", "backup": "backup_dir_name"}
    """
    if not os.path.exists(zip_path):
        return {"success": False, "error": "上传文件不存在"}

    if not zipfile.is_zipfile(zip_path):
        os.remove(zip_path)
        return {"success": False, "error": "文件不是有效的 ZIP 格式"}

    old_version = _get_app_version()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"upgrade_{timestamp}"
    backup_path = BACKUP_DIR / backup_name
    extract_dir = BACKUP_DIR / f"_extract_{timestamp}"

    try:
        # 备份
        backup_path.mkdir(parents=True, exist_ok=True)
        _backup_project_files(backup_path)

        # 校验增量包
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            all_names = [n.replace("\\", "/") for n in zf.namelist()]
            has_app_py = any(
                n.endswith("app.py") and "/" not in n.rstrip("/")
                for n in all_names
            )
            has_version_json = "version.json" in all_names
            has_asap_init = any(
                n.replace("\\", "/").endswith("asap_adapter/__init__.py")
                for n in zf.namelist()
            )
            if not has_app_py and not has_version_json and not has_asap_init:
                shutil.rmtree(extract_dir)
                os.remove(zip_path)
                shutil.rmtree(backup_path)
                return {
                    "success": False,
                    "error": "ZIP 包不合法：未找到 app.py、version.json 或 asap_adapter/__init__.py",
                }

            zf.extractall(extract_dir)

        # 读取 version.json
        version_info = _read_version_json(extract_dir)
        release_notes = version_info.get("changes", []) or []
        release_title = version_info.get("title", "")
        files_changed = version_info.get("files_changed", {})
        from_version = version_info.get("from_version", "")

        if not release_notes and remark:
            release_notes = [remark]

        # 增量包版本校验
        if from_version:
            current_ver = _get_app_version()
            if from_version != current_ver:
                shutil.rmtree(extract_dir)
                os.remove(zip_path)
                shutil.rmtree(backup_path)
                return {
                    "success": False,
                    "error": f"版本不匹配：升级包基线 v{from_version}，当前服务器 v{current_ver}。"
                             f"请确认服务器版本后再生成升级包。"
                }

        # 逐文件覆盖（跳过排除项）
        overlay_count = 0
        skip_count = 0
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                src_path = Path(root) / file
                rel_path = src_path.relative_to(extract_dir)

                if _should_exclude(str(rel_path)):
                    skip_count += 1
                    continue

                dst_path = BASE_DIR / rel_path
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
                overlay_count += 1

        # 清理被删除的文件（增量包支持）
        deleted_paths = files_changed.get("D", []) if isinstance(files_changed, dict) else []
        delete_count = 0
        for rel_path in deleted_paths:
            dst_path = BASE_DIR / rel_path.replace("\\", "/")
            if dst_path.is_file():
                dst_path.unlink()
                delete_count += 1
            elif dst_path.is_dir():
                shutil.rmtree(dst_path, ignore_errors=True)
                delete_count += 1

        # 记录升级信息
        new_version = _get_app_version()
        record = {
            "backup_name": backup_name,
            "timestamp": timestamp,
            "old_version": old_version,
            "new_version": new_version,
            "files_overlay": overlay_count,
            "files_skipped": skip_count,
            "status": "success",
            "release_title": release_title or f"从 v{old_version} 升级到 v{new_version}",
            "release_notes": release_notes,
        }
        if from_version:
            record["upgrade_type"] = "incremental"
            record["from_version"] = from_version
        if delete_count:
            record["files_deleted"] = delete_count
        if not record["release_notes"]:
            record.pop("release_notes", None)

        records = _read_upgrade_log()
        records.insert(0, record)
        _write_upgrade_log(records)

        # 写入备份 meta
        meta = {
            "timestamp": timestamp,
            "old_version": old_version,
            "new_version": new_version,
            "description": release_title or f"从 v{old_version} 升级到 v{new_version}",
            "release_notes": release_notes,
        }
        with open(backup_path / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        _cleanup_old_backups()

    except Exception as e:
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        if os.path.exists(zip_path):
            os.remove(zip_path)
        return {"success": False, "error": f"升级失败: {str(e)}"}

    finally:
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        if os.path.exists(zip_path):
            os.remove(zip_path)

    result = {
        "success": True,
        "message": f"升级完成（{old_version} → {new_version}），系统3秒后自动重启...",
        "backup": backup_name,
        "release_title": release_title,
        "release_notes": release_notes,
    }
    if from_version:
        result["upgrade_type"] = "incremental"
    if delete_count:
        result["files_deleted"] = delete_count
    return result


# ── 回滚 ──────────────────────────────────

def do_rollback(backup_name: str) -> dict:
    """
    回滚到指定备份版本
    """
    backup_path = BACKUP_DIR / backup_name
    files_path = backup_path / "files"

    if not files_path.exists():
        return {"success": False, "error": f'备份 "{backup_name}" 不存在或已损坏'}

    try:
        # 先备份当前代码
        pre_rollback_dir = BACKUP_DIR / f"prerollback_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        _backup_project_files(pre_rollback_dir)

        # 恢复备份
        count = 0
        for root, dirs, files in os.walk(files_path):
            for file in files:
                src_path = Path(root) / file
                rel_path = src_path.relative_to(files_path)
                dst_path = BASE_DIR / rel_path
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
                count += 1

        # 读取 meta
        meta = {}
        meta_path = backup_path / "meta.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

        old_version = meta.get("old_version", "unknown")
        new_version = meta.get("new_version", "unknown")

        _cleanup_old_backups()

        record = {
            "backup_name": f"restore_from_{backup_name}",
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "old_version": new_version,
            "new_version": old_version,
            "files_overlay": count,
            "status": "rollback",
            "description": f"从 {backup_name} 回滚",
        }
        records = _read_upgrade_log()
        records.insert(0, record)
        _write_upgrade_log(records)

    except Exception as e:
        return {"success": False, "error": f"回滚失败: {str(e)}"}

    return {
        "success": True,
        "message": f"已从 {backup_name} 回滚，共恢复 {count} 个文件，系统3秒后自动重启...",
    }


# ── 重启 ──────────────────────────────────

def trigger_restart(delay: int = 3):
    """延迟退出进程，依靠 supervisor autorestart 自动拉起（避免自杀式重启）"""
    def _restart():
        time.sleep(delay)
        import os
        os._exit(0)

    thread = threading.Thread(target=_restart, daemon=True)
    thread.start()
