"""
配置加载模块
从 TOML 文件加载配置，运行时配置覆盖静态配置。
优先级: runtime.toml > overrides.json > env.toml > 默认值

文件说明:
  - config/env.toml       — 静态项目配置（server/log/duration/超时），修改需重启
  - config/runtime.toml   — 运行时配置（地址/URL/映射），修改即时生效（文件编辑器）
  - config/overrides.json — 运行时配置（兼容，可视化编辑器写入），即时生效
"""

import os
import sys
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# TOML 解析
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 5012
    reload: bool = False


@dataclass
class LogConfig:
    level: str = "INFO"
    file: str = "logs/asap.log"
    rotation: str = "5 MB"
    backup_count: int = 3


@dataclass
class AngelConfig:
    base_url: str = "http://localhost:8080"
    outer_door_id: str = "DOOR_OUTER"
    inner_door_id: str = "DOOR_INNER"
    poll_interval: float = 1.0
    poll_timeout: float = 30.0


@dataclass
class ZoneConfig:
    enter_url: str = ""
    exit_url: str = ""
    status_url: str = ""
    zone_id: str = "air_shower_room"
    client_id: str = "asap_adapter_01"
    retry_interval: float = 3.0
    max_retries: int = 10
    exit_retry_interval: float = 1.0
    exit_max_retries: int = 30


@dataclass
class AirShowerConfig:
    duration: float = 15.0
    agv_enter_timeout: float = 30.0
    agv_exit_timeout: float = 30.0


@dataclass
class RcsConfig:
    change_status_url: str = ""
    report_interval: float = 0.5
    door_code_mapping: dict = field(default_factory=lambda: {"DOOR_OUTER": "1001", "DOOR_INNER": "1002"})


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    log: LogConfig = field(default_factory=LogConfig)
    angel: AngelConfig = field(default_factory=AngelConfig)
    zone: ZoneConfig = field(default_factory=ZoneConfig)
    air_shower: AirShowerConfig = field(default_factory=AirShowerConfig)
    rcs: RcsConfig = field(default_factory=RcsConfig)


def _project_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _apply_section(cfg: AppConfig, data: dict, section: str, keys: list):
    if section not in data:
        return
    target = getattr(cfg, section, None)
    if target is None:
        return
    for key in keys:
        if key in data[section]:
            setattr(target, key, data[section][key])


def load_config(path: Optional[str] = None) -> AppConfig:
    """加载全部配置: env.toml → runtime.toml → overrides.json"""
    base_dir = _project_dir()
    if path is None:
        path = os.path.join(base_dir, "config", "env.toml")
    cfg = AppConfig()

    # 1. env.toml（静态）
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            _apply_section(cfg, data, "server", ("host", "port", "reload"))
            _apply_section(cfg, data, "log", ("level", "file", "rotation", "backup_count"))
            _apply_section(cfg, data, "angel",
                           ("base_url", "outer_door_id", "inner_door_id",
                            "poll_interval", "poll_timeout"))
            _apply_section(cfg, data, "zone",
                           ("enter_url", "exit_url", "status_url", "zone_id",
                            "client_id", "retry_interval", "max_retries",
                            "exit_retry_interval", "exit_max_retries"))
            _apply_section(cfg, data, "air_shower",
                           ("duration", "agv_enter_timeout", "agv_exit_timeout"))
            _apply_section(cfg, data, "rcs",
                           ("change_status_url", "report_interval", "door_code_mapping"))
        except Exception as e:
            logger.error("加载 env.toml 失败: %s", e)

    # 2. runtime.toml（运行时，覆盖 env.toml）
    _load_runtime(cfg, os.path.join(base_dir, "config", "runtime.toml"))

    # 3. overrides.json（向后兼容，覆盖前两者）
    _load_overrides(cfg, os.path.join(base_dir, "config", "overrides.json"))

    return cfg


def _load_runtime(cfg: AppConfig, path: str):
    if not os.path.exists(path):
        return
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        _apply_section(cfg, data, "angel", ("base_url", "outer_door_id", "inner_door_id"))
        _apply_section(cfg, data, "zone", ("enter_url", "exit_url", "status_url"))
        _apply_section(cfg, data, "rcs", ("change_status_url", "report_interval", "door_code_mapping"))
        logger.info("已加载 runtime.toml")
    except Exception as e:
        logger.error("加载 runtime.toml 失败: %s", e)


def _load_overrides(cfg: AppConfig, path: str):
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            ov = json.load(f)
        _apply_section(cfg, ov.get("rcs", {}), None,
                       ("change_status_url", "report_interval", "door_code_mapping"))
        # 手动处理各段
        rcs_ov = ov.get("rcs", {})
        for key in ("change_status_url", "report_interval", "door_code_mapping"):
            if key in rcs_ov:
                setattr(cfg.rcs, key, rcs_ov[key])
        angel_ov = ov.get("angel", {})
        for key in ("base_url", "outer_door_id", "inner_door_id"):
            if key in angel_ov:
                setattr(cfg.angel, key, angel_ov[key])
        zone_ov = ov.get("zone", {})
        for key in ("enter_url", "exit_url", "status_url"):
            if key in zone_ov:
                setattr(cfg.zone, key, zone_ov[key])
    except Exception as e:
        logger.error("加载 overrides.json 失败: %s", e)


# ═══════════════════════════════════════════
#  运行时配置管理
# ═══════════════════════════════════════════

def runtime_path() -> str:
    return os.path.join(_project_dir(), "config", "runtime.toml")


def read_runtime() -> str:
    """读取 runtime.toml 内容（文件编辑器用）"""
    path = runtime_path()
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def save_runtime(content: str) -> dict:
    """
    保存 runtime.toml，验证 TOML 格式，自动备份
    返回 {success, message, backup?}
    """
    path = runtime_path()
    try:
        tomllib.loads(content)
    except Exception as e:
        return {"success": False, "error": f"TOML 格式错误: {e}"}

    backup = ""
    if os.path.exists(path):
        import shutil
        backup = path + ".bak"
        shutil.copy2(path, backup)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return {"success": True, "message": "运行时配置已保存，即时生效", "backup": backup}


def apply_runtime_string(config: AppConfig, content: str):
    """
    将 runtime.toml 内容热更新到内存中的 config 对象
    """
    data = tomllib.loads(content)
    _apply_section(config, data, "angel", ("base_url", "outer_door_id", "inner_door_id"))
    _apply_section(config, data, "zone", ("enter_url", "exit_url", "status_url"))
    _apply_section(config, data, "rcs", ("change_status_url", "report_interval", "door_code_mapping"))


def env_path() -> str:
    """获取 env.toml 路径（文件编辑器用）"""
    p = os.path.join(_project_dir(), "config", "env.toml")
    return p


def read_env() -> str:
    """读取 env.toml 内容"""
    p = env_path()
    if not os.path.exists(p):
        return ""
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def save_env(content: str) -> dict:
    """保存 env.toml，验证 TOML 格式，自动备份"""
    p = env_path()
    try:
        tomllib.loads(content)
    except Exception as e:
        return {"success": False, "error": f"TOML 格式错误: {e}"}
    backup = ""
    if os.path.exists(p):
        import shutil
        backup = p + ".bak"
        shutil.copy2(p, backup)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return {"success": True, "message": "静态配置已保存，需重启服务生效", "backup": backup}


def save_override(section: str, key: str, value):
    """
    保存单条运行时覆盖到 overrides.json（可视化编辑器用）
    同时尝试更新 runtime.toml（追加键值对）
    """
    base_dir = _project_dir()
    overrides_path = os.path.join(base_dir, "config", "overrides.json")
    overrides = {}
    if os.path.exists(overrides_path):
        try:
            with open(overrides_path, "r", encoding="utf-8") as f:
                overrides = json.load(f)
        except Exception:
            overrides = {}
    if section not in overrides:
        overrides[section] = {}
    overrides[section][key] = value
    os.makedirs(os.path.dirname(overrides_path), exist_ok=True)
    with open(overrides_path, "w", encoding="utf-8") as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)
    logger.info("运行时配置已更新: [%s] %s = %s", section, key, value)
