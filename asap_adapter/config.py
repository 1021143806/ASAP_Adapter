"""
配置加载模块
从 TOML 文件加载配置，再叠加上 JSON 运行时覆盖（persistent overrides）。
优先级: overrides.json > env.toml > 默认值
"""

import os
import sys
import json
from dataclasses import dataclass, field
from typing import Optional

# TOML 解析: Python 3.11+ 使用 tomllib, 3.9/3.10 使用 tomli 回退
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


def load_config(path: Optional[str] = None) -> AppConfig:
    """从 TOML 文件加载配置"""
    if path is None:
        # 默认查找项目目录下的 config/env.toml
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base_dir, "config", "env.toml")

    cfg = AppConfig()

    if not os.path.exists(path):
        # 配置文件不存在，使用默认值
        return cfg

    with open(path, "rb") as f:
        data = tomllib.load(f)

    # Server
    if "server" in data:
        for key in ("host", "port", "reload"):
            if key in data["server"]:
                setattr(cfg.server, key, data["server"][key])

    # Log
    if "log" in data:
        for key in ("level", "file", "rotation", "backup_count"):
            if key in data["log"]:
                setattr(cfg.log, key, data["log"][key])

    # Angel
    if "angel" in data:
        for key in ("base_url", "outer_door_id", "inner_door_id",
                    "poll_interval", "poll_timeout"):
            if key in data["angel"]:
                setattr(cfg.angel, key, data["angel"][key])

    # Zone
    if "zone" in data:
        for key in ("enter_url", "exit_url", "status_url", "zone_id",
                    "client_id", "retry_interval", "max_retries",
                    "exit_retry_interval", "exit_max_retries"):
            if key in data["zone"]:
                setattr(cfg.zone, key, data["zone"][key])

    # Air shower
    if "air_shower" in data:
        for key in ("duration", "agv_enter_timeout", "agv_exit_timeout"):
            if key in data["air_shower"]:
                setattr(cfg.air_shower, key, data["air_shower"][key])

    # RCS
    if "rcs" in data:
        for key in ("change_status_url", "report_interval", "door_code_mapping"):
            if key in data["rcs"]:
                setattr(cfg.rcs, key, data["rcs"][key])

    # ── 加载运行时覆盖 overrides.json ──────────
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    overrides_path = os.path.join(base_dir, "config", "overrides.json")
    if os.path.exists(overrides_path):
        try:
            with open(overrides_path, "r", encoding="utf-8") as f:
                overrides = json.load(f)

            # ── 加载所有可热更新的配置段 ──
            # RCS
            rcs_overrides = overrides.get("rcs", {})
            for key in ("change_status_url", "report_interval", "door_code_mapping"):
                if key in rcs_overrides:
                    setattr(cfg.rcs, key, rcs_overrides[key])

            # AB 门
            angel_overrides = overrides.get("angel", {})
            for key in ("base_url",):
                if key in angel_overrides:
                    setattr(cfg.angel, key, angel_overrides[key])

            # 区域管控
            zone_overrides = overrides.get("zone", {})
            for key in ("enter_url", "exit_url", "status_url"):
                if key in zone_overrides:
                    setattr(cfg.zone, key, zone_overrides[key])
        except Exception as e:
            print(f"[Config] 加载 overrides.json 失败: {e}")

    return cfg


# ═══════════════════════════════════════════
#  运行时配置持久化
# ═══════════════════════════════════════════

def save_override(section: str, key: str, value):
    """
    保存运行时配置覆盖到 overrides.json (持久化，重启后保留)
    section: 配置段名, 如 "rcs"
    key: 配置键名, 如 "change_status_url"
    value: 配置值
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
