"""
配置加载模块
从 TOML 文件加载配置，优先级: data/config.toml > env.toml > 默认值

文件说明:
  - config/env.toml       — 系统基础配置（server/log），修改需重启
  - /data/config.toml     — 业务配置（angel/zone/rcs/sim），修改即时生效
                           首次启动无此文件时自动从模板生成
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
    base_url: str = "http://127.0.0.1:5012/sim"
    outer_door_id: str = "DOOR01"
    inner_door_id: str = "DOOR02"
    poll_interval: float = 1.0
    poll_timeout: float = 30.0


@dataclass
class ZoneConfig:
    enter_url: str = "http://127.0.0.1:5012/sim/api/zones/enter"
    exit_url: str = "http://127.0.0.1:5012/sim/api/zones/exit"
    status_url: str = "http://127.0.0.1:5012/sim/api/zones/status"
    zone_id: str = "zone_001"
    client_id: str = "asap_adapter_01"
    entry_door_code: str = "q001"
    retry_interval: float = 1.0
    max_retries: int = 30
    enter_retry_max: int = 30
    exit_retry_interval: float = 1.0
    exit_max_retries: int = 30
    zone_poll_interval: float = 300.0


@dataclass
class AirShowerConfig:
    duration: float = 4.0
    agv_enter_timeout: float = 30.0
    agv_exit_timeout: float = 30.0


@dataclass
class RcsConfig:
    door_code_mapping: dict = field(default_factory=lambda: {"DOOR01": "1001", "DOOR02": "1002"})


@dataclass
class SimConfig:
    """内置模拟器配置"""
    auto_open_delay: float = 2.0
    auto_close_delay: float = 2.0
    zone_always_busy: bool = False
    zone_id: str = "zone_001"


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    log: LogConfig = field(default_factory=LogConfig)
    angel: AngelConfig = field(default_factory=AngelConfig)
    zone: ZoneConfig = field(default_factory=ZoneConfig)
    air_shower: AirShowerConfig = field(default_factory=AirShowerConfig)
    rcs: RcsConfig = field(default_factory=RcsConfig)
    sim: SimConfig = field(default_factory=SimConfig)


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
    """加载配置: env.toml (系统) → data/config.toml (业务, 自动生成)"""
    base_dir = _project_dir()
    if path is None:
        path = os.path.join(base_dir, "config", "env.toml")
    cfg = AppConfig()

    # 1. env.toml — 仅系统基础配置 (server/log)
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            _apply_section(cfg, data, "server", ("host", "port", "reload"))
            _apply_section(cfg, data, "log", ("level", "file", "rotation", "backup_count"))
        except Exception as e:
            logger.error("加载 env.toml 失败: %s", e)

    # 2. data/config.toml — 业务配置，不存在则自动从模板生成
    _load_unified(cfg)

    return cfg


# ═══════════════════════════════════════════
#  env.toml 系统配置读写
# ═══════════════════════════════════════════
#  运行时配置管理
# ═══════════════════════════════════════════

def runtime_path() -> str:
    return os.path.join(_project_dir(), "config", "runtime.toml")


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
    保存单条运行时配置到 /data/config.toml
    读取现有配置 → 修改 → 写回
    """
    # 读取现有
    old_data = {}
    if os.path.exists(UNIFIED_CONFIG_PATH):
        try:
            with open(UNIFIED_CONFIG_PATH, "rb") as f:
                old_data = tomllib.load(f)
        except Exception:
            old_data = {}
    # 更新
    if section not in old_data:
        old_data[section] = {}
    old_data[section][key] = value
    # 写回（复用 save_unified_config 逻辑，但不递增版本号）
    _write_unified_file(old_data)
    logger.info("配置已持久化到 /data/config.toml: [%s] %s = %s", section, key, value)


def _write_unified_file(data: dict):
    """将数据写入 /data/config.toml"""
    from datetime import datetime
    os.makedirs(os.path.dirname(UNIFIED_CONFIG_PATH), exist_ok=True)
    sections = ["# ASAP Adapter 业务配置", "# 可通过 WebUI 修改，即时生效", ""]
    for sec_key in ("angel", "zone", "rcs", "air_shower", "sim"):
        sec = data.get(sec_key, {})
        if sec:
            sections.append(f"[{sec_key}]")
            for k, v in sec.items():
                sections.append(_toml_kv(k, v))
            sections.append("")
    # meta
    ver = data.get("meta", {}).get("version", 1)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections.append("[meta]")
    sections.append(f'version = {ver}')
    sections.append(f'updated = "{now}"')
    sections.append("")
    with open(UNIFIED_CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(sections))


def _generate_unified_file():
    """在 /data/config.toml 不存在时从模板生成"""
    template = (
        "# ASAP Adapter 业务配置\n"
        "# 可通过 WebUI 修改，即时生效\n\n"
        "[angel]\n"
        'base_url = "http://127.0.0.1:5012/sim"\n'
        'outer_door_id = "DOOR01"\n'
        'inner_door_id = "DOOR02"\n'
        "poll_interval = 1.0\n"
        "poll_timeout = 30.0\n\n"
        "[zone]\n"
        'enter_url = "http://127.0.0.1:5012/sim/api/zones/enter"\n'
        'exit_url = "http://127.0.0.1:5012/sim/api/zones/exit"\n'
        'status_url = "http://127.0.0.1:5012/sim/api/zones/status"\n'
        'zone_id = "zone_001"\n'
        'client_id = "asap_adapter_01"\n'
        'entry_door_code = "q001"\n'
        "retry_interval = 1.0\n"
        "max_retries = 30\n"
        "enter_retry_max = 30\n"
        "exit_retry_interval = 1.0\n"
        "exit_max_retries = 30\n"
        "zone_poll_interval = 300.0\n\n"
        "[rcs]\n"
        'door_code_mapping = {DOOR01 = "1001", DOOR02 = "1002"}\n\n'
        "[air_shower]\n"
        "duration = 4.0\n"
        "agv_enter_timeout = 30.0\n"
        "agv_exit_timeout = 30.0\n\n"
        "[sim]\n"
        "auto_open_delay = 2.0\n"
        "auto_close_delay = 2.0\n"
        "zone_always_busy = false\n"
        'zone_id = "zone_001"\n\n'
        "[meta]\n"
        "version = 1\n"
        f'updated = "{__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")}"\n'
    )
    os.makedirs(os.path.dirname(UNIFIED_CONFIG_PATH), exist_ok=True)
    with open(UNIFIED_CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(template)
    logger.info("已自动生成 /data/config.toml")


# ═══════════════════════════════════════════
#  统一运行时配置 (/data/config.toml)
# ═══════════════════════════════════════════

CONFIG_VERSION = 1
UNIFIED_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "config.toml")


def _load_unified(cfg: AppConfig):
    """加载 /data/config.toml 业务配置，不存在时自动从模板生成"""
    if not os.path.exists(UNIFIED_CONFIG_PATH):
        logger.info("/data/config.toml 不存在，从模板自动生成...")
        _generate_unified_file()
    try:
        with open(UNIFIED_CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
        _apply_section(cfg, data, "angel",
                       ("base_url", "outer_door_id", "inner_door_id",
                        "poll_interval", "poll_timeout"))
        _apply_section(cfg, data, "zone",
                        ("enter_url", "exit_url", "status_url", "zone_id",
                         "client_id", "entry_door_code", "exit_door_code",
                         "retry_interval", "max_retries",
                         "enter_retry_max",
                         "exit_retry_interval", "exit_max_retries",
                         "zone_poll_interval"))
        _apply_section(cfg, data, "rcs",
                       ("door_code_mapping",))
        _apply_section(cfg, data, "air_shower",
                       ("duration", "agv_enter_timeout", "agv_exit_timeout"))
        _apply_section(cfg, data, "sim",
                       ("auto_open_delay", "auto_close_delay",
                        "zone_always_busy", "zone_id"))
        version = data.get("meta", {}).get("version", 0)
        global CONFIG_VERSION
        CONFIG_VERSION = version if version > CONFIG_VERSION else CONFIG_VERSION
        logger.info("已加载 /data/config.toml (版本 %d)", CONFIG_VERSION)
    except Exception as e:
        logger.error("加载 /data/config.toml 失败: %s", e)


def read_unified_config() -> dict:
    """读取统一配置（供 WebUI 使用）"""
    if not os.path.exists(UNIFIED_CONFIG_PATH):
        # 从当前内存配置生成默认文件
        return _generate_default_config()
    try:
        with open(UNIFIED_CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
        return {
            "version": data.get("meta", {}).get("version", 0),
            "angel": data.get("angel", {}),
            "zone": data.get("zone", {}),
            "rcs": data.get("rcs", {}),
            "air_shower": data.get("air_shower", {}),
            "sim": data.get("sim", {}),
            "raw": _read_raw(UNIFIED_CONFIG_PATH),
        }
    except Exception as e:
        logger.error("读取 /data/config.toml 失败: %s", e)
        return {"error": str(e)}


def save_unified_config(data: dict) -> dict:
    """
    保存统一配置到 data/config.toml
    自动递增版本号，备份旧文件，合并现有配置
    返回 {success, message, version, backup?}
    """
    try:
        # 读取当前配置（作为基础）
        old_data = {}
        old_version = 0
        if os.path.exists(UNIFIED_CONFIG_PATH):
            try:
                with open(UNIFIED_CONFIG_PATH, "rb") as f:
                    old_data = tomllib.load(f)
                old_version = old_data.get("meta", {}).get("version", 0)
            except Exception:
                pass

        new_version = old_version + 1
        now = __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 合并：新数据覆盖旧数据
        merged = {k: v for k, v in old_data.items() if k != "meta"}
        for section, values in data.items():
            if section == "meta":
                continue
            if section not in merged:
                merged[section] = {}
            if isinstance(values, dict):
                merged[section].update(values)
            else:
                merged[section] = values

        # 构建 TOML 内容
        sections = []
        sections.append("# ASAP Adapter 统一配置")
        sections.append(f"# 版本: {new_version} | 更新: {now}")
        sections.append("")

        # [angel]
        angel = merged.get("angel", {})
        if angel:
            sections.append("[angel]")
            for k in ("base_url", "outer_door_id", "inner_door_id",
                      "poll_interval", "poll_timeout"):
                if k in angel:
                    sections.append(_toml_kv(k, angel[k]))
            sections.append("")

        # [zone]
        zone = merged.get("zone", {})
        if zone:
            sections.append("[zone]")
            for k in ("enter_url", "exit_url", "status_url", "zone_id",
                      "client_id", "entry_door_code",
                      "retry_interval", "max_retries",
                      "enter_retry_max",
                      "exit_retry_interval", "exit_max_retries",
                      "zone_poll_interval"):
                if k in zone:
                    sections.append(_toml_kv(k, zone[k]))
            sections.append("")

        # [rcs]
        rcs = merged.get("rcs", {})
        if rcs:
            sections.append("[rcs]")
            dcm = rcs.get("door_code_mapping", {})
            if dcm:
                sections.append("")
                sections.append("[rcs.door_code_mapping]")
                for dk, dv in dcm.items():
                    sections.append(f'{dk} = "{dv}"')
            sections.append("")

        # [air_shower]
        ash = merged.get("air_shower", {})
        if ash:
            sections.append("[air_shower]")
            for k in ("duration", "agv_enter_timeout", "agv_exit_timeout"):
                if k in ash:
                    sections.append(_toml_kv(k, ash[k]))
            sections.append("")

        # [sim]
        sim = merged.get("sim", {})
        if sim:
            sections.append("[sim]")
            for k in ("auto_open_delay", "auto_close_delay", "zone_always_busy", "zone_id"):
                if k in sim:
                    sections.append(_toml_kv(k, sim[k]))
            sections.append("")

        # [meta]
        sections.append("[meta]")
        sections.append(f'version = {new_version}')
        sections.append(f'updated = "{now}"')
        sections.append("")

        content = "\n".join(sections)

        # 备份（版本化: data/config.toml.v{version}，最多保留20个）
        backup = ""
        if os.path.exists(UNIFIED_CONFIG_PATH):
            backup = UNIFIED_CONFIG_PATH + f".v{old_version}"
            shutil.copy2(UNIFIED_CONFIG_PATH, backup)
            # 清理超出20个的旧备份
            _cleanup_versioned_backups()

        # 确保目录存在
        os.makedirs(os.path.dirname(UNIFIED_CONFIG_PATH), exist_ok=True)

        # 写入
        with open(UNIFIED_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(content)

        global CONFIG_VERSION
        CONFIG_VERSION = new_version

        logger.info("统一配置已保存: /data/config.toml (版本 %d)", new_version)
        return {
            "success": True,
            "message": f"配置已保存 (版本 {new_version})",
            "version": new_version,
            "backup": backup,
        }
    except Exception as e:
        logger.error("保存统一配置失败: %s", e)
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════
#  配置导出/导入/版本管理
# ═══════════════════════════════════════════

MAX_CONFIG_VERSIONS = 20


def _cleanup_versioned_backups():
    """清理超出 MAX_CONFIG_VERSIONS 的旧版本备份"""
    import glob
    import os as _os
    pattern = UNIFIED_CONFIG_PATH + ".v*"
    files = sorted(glob.glob(pattern))
    while len(files) > MAX_CONFIG_VERSIONS:
        _os.remove(files[0])
        files.pop(0)


def export_config() -> str:
    """导出当前 data/config.toml 内容"""
    if os.path.exists(UNIFIED_CONFIG_PATH):
        with open(UNIFIED_CONFIG_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return _generate_unified_content()


def import_config(content: str) -> dict:
    """
    导入配置：验证 TOML → 备份当前 → 写入 → 返回结果
    """
    try:
        tomllib.loads(content)
    except Exception as e:
        return {"success": False, "error": f"TOML 格式错误: {e}"}

    try:
        # 备份当前
        import shutil
        if os.path.exists(UNIFIED_CONFIG_PATH):
            old_data = {}
            try:
                with open(UNIFIED_CONFIG_PATH, "rb") as f:
                    old_data = tomllib.load(f)
                old_ver = old_data.get("meta", {}).get("version", 0)
            except Exception:
                old_ver = 0
            backup = UNIFIED_CONFIG_PATH + f".v{old_ver}"
            shutil.copy2(UNIFIED_CONFIG_PATH, backup)
            _cleanup_versioned_backups()

        # 写入
        os.makedirs(os.path.dirname(UNIFIED_CONFIG_PATH), exist_ok=True)
        with open(UNIFIED_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(content)

        return {"success": True, "message": "配置已导入，即时生效"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_config_versions() -> list:
    """列出所有版本备份"""
    import glob
    import os as _os
    pattern = UNIFIED_CONFIG_PATH + ".v*"
    files = sorted(glob.glob(pattern), reverse=True)
    versions = []
    for fp in files:
        vnum = fp.rsplit(".v", 1)[-1]
        try:
            vnum = int(vnum)
        except ValueError:
            continue
        mtime = datetime.fromtimestamp(_os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M:%S")
        # 读取 meta
        note = ""
        try:
            with open(fp, "rb") as f:
                d = tomllib.load(f)
            note = d.get("meta", {}).get("updated", "")
        except Exception:
            pass
        versions.append({"version": vnum, "file": _os.path.basename(fp), "time": mtime, "note": note})
    return versions


def get_config_version(version: int) -> dict:
    """获取指定版本的配置内容"""
    fp = UNIFIED_CONFIG_PATH + f".v{version}"
    if not os.path.exists(fp):
        return {"error": f"版本 {version} 不存在"}
    with open(fp, "r", encoding="utf-8") as f:
        content = f.read()
    data = tomllib.loads(content)
    return {"version": version, "content": content, "data": data}


def rollback_config(version: int) -> dict:
    """回滚到指定版本"""
    src = UNIFIED_CONFIG_PATH + f".v{version}"
    if not os.path.exists(src):
        return {"success": False, "error": f"版本 {version} 不存在"}
    try:
        import shutil
        # 先备份当前
        if os.path.exists(UNIFIED_CONFIG_PATH):
            old_data = {}
            try:
                with open(UNIFIED_CONFIG_PATH, "rb") as f:
                    old_data = tomllib.load(f)
                old_ver = old_data.get("meta", {}).get("version", 0)
            except Exception:
                old_ver = 0
            pre_rollback = UNIFIED_CONFIG_PATH + f".v{old_ver}"
            shutil.copy2(UNIFIED_CONFIG_PATH, pre_rollback)

        # 恢复
        shutil.copy2(src, UNIFIED_CONFIG_PATH)
        _cleanup_versioned_backups()
        return {"success": True, "message": f"已回滚到版本 {version}", "version": version}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _generate_unified_content() -> str:
    """生成默认统一配置 TOML 文本"""
    return (
        "# ASAP Adapter 业务配置\n"
        "# 可通过 WebUI 修改，即时生效\n\n"
        "[angel]\n"
        'base_url = "http://127.0.0.1:5012/sim"\n'
        'outer_door_id = "DOOR01"\n'
        'inner_door_id = "DOOR02"\n'
        "poll_interval = 1.0\n"
        "poll_timeout = 30.0\n\n"
        "[zone]\n"
        'enter_url = "http://127.0.0.1:5012/sim/api/zones/enter"\n'
        'exit_url = "http://127.0.0.1:5012/sim/api/zones/exit"\n'
        'status_url = "http://127.0.0.1:5012/sim/api/zones/status"\n'
        'zone_id = "zone_001"\n'
        'entry_door_code = "q001"\n'
        "zone_poll_interval = 300.0\n\n"
        "[rcs]\n"
        'door_code_mapping = {DOOR01 = "1001", DOOR02 = "1002"}\n\n'
        "[air_shower]\n"
        "duration = 4.0\n\n"
        "[sim]\n"
        "auto_open_delay = 2.0\n"
        "auto_close_delay = 2.0\n"
        "zone_always_busy = false\n"
        'zone_id = "zone_001"\n\n'
        "[meta]\n"
        "version = 0\n"
        f'updated = "{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}"\n'
    )


def _generate_default_config() -> dict:
    """生成默认统一配置"""
    return {
        "version": 0,
        "angel": {"base_url": "http://127.0.0.1:5012/sim", "outer_door_id": "DOOR01", "inner_door_id": "DOOR02"},
        "zone": {"enter_url": "", "exit_url": "", "status_url": "", "zone_id": "zone_001",
                 "entry_door_code": "q001", "zone_poll_interval": 300.0},
        "rcs": {"door_code_mapping": {"DOOR01": "1001", "DOOR02": "1002"}},
        "air_shower": {"duration": 4.0},
        "sim": {"auto_open_delay": 2.0, "auto_close_delay": 2.0, "zone_always_busy": False, "zone_id": "zone_001"},
        "raw": "",
    }


def _read_raw(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _toml_kv(key: str, value) -> str:
    """格式化为 TOML 键值对"""
    if isinstance(value, bool):
        return f'{key} = {"true" if value else "false"}'
    elif isinstance(value, (int, float)):
        return f'{key} = {value}'
    elif isinstance(value, str):
        return f'{key} = "{value}"'
    else:
        return f'{key} = {value}'
