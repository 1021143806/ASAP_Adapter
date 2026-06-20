"""
配置加载模块
从 TOML 文件加载配置，运行时配置覆盖静态配置。
优先级: /data/config.toml > runtime.toml > env.toml > 默认值

文件说明:
  - config/env.toml       — 静态项目配置（server/log），修改需重启
  - /data/config.toml     — 统一运行时配置（含版本号），修改即时生效
  - config/runtime.toml   — 运行时配置（兼容旧版），修改即时生效
  - config/overrides.json — 运行时覆盖（兼容旧版），可视化编辑器写入
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
    zone_id: str = "zone_001"
    client_id: str = "asap_adapter_01"
    entry_door_code: str = "q001"   # RCS doorNum → 进入区域
    retry_interval: float = 3.0
    max_retries: int = 10
    enter_retry_max: int = 30        # 进入区域最大重试次数（每次间隔1s）
    exit_retry_interval: float = 1.0
    exit_max_retries: int = 30
    zone_poll_interval: float = 300.0  # 区域状态定时轮询间隔(秒)，默认5分钟


@dataclass
class AirShowerConfig:
    duration: float = 4.0
    agv_enter_timeout: float = 30.0
    agv_exit_timeout: float = 30.0


@dataclass
class RcsConfig:
    change_status_url: str = ""
    report_interval: float = 0.5
    door_code_mapping: dict = field(default_factory=lambda: {"DOOR01": "1001", "DOOR02": "1002"})


@dataclass
class SimConfig:
    """内置模拟器配置"""
    auto_open_delay: float = 2.0     # 门自动打开过渡时间(秒)
    auto_close_delay: float = 2.0    # 门自动关闭过渡时间(秒)
    zone_always_busy: bool = False   # 区域始终占用(测试用)
    zone_id: str = "air_shower_room" # 模拟区域ID


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
                             "client_id", "entry_door_code",
                             "retry_interval", "max_retries",
                             "enter_retry_max",
                             "exit_retry_interval", "exit_max_retries",
                             "zone_poll_interval"))
            _apply_section(cfg, data, "air_shower",
                           ("duration", "agv_enter_timeout", "agv_exit_timeout"))
            _apply_section(cfg, data, "rcs",
                           ("change_status_url", "report_interval", "door_code_mapping"))
            _apply_section(cfg, data, "sim",
                           ("auto_open_delay", "auto_close_delay",
                            "zone_always_busy", "zone_id"))
        except Exception as e:
            logger.error("加载 env.toml 失败: %s", e)

    # 2. runtime.toml（运行时，覆盖 env.toml）
    _load_runtime(cfg, os.path.join(base_dir, "config", "runtime.toml"))

    # 3. overrides.json（向后兼容，覆盖前两者）
    _load_overrides(cfg, os.path.join(base_dir, "config", "overrides.json"))

    # 4. /data/config.toml（统一运行时配置，最高优先级）
    _load_unified(cfg)

    return cfg


def _load_runtime(cfg: AppConfig, path: str):
    if not os.path.exists(path):
        return
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        _apply_section(cfg, data, "angel", ("base_url", "outer_door_id", "inner_door_id"))
        _apply_section(cfg, data, "zone",
                       ("enter_url", "exit_url", "status_url",
                        "zone_poll_interval", "entry_door_code"))
        _apply_section(cfg, data, "rcs", ("change_status_url", "report_interval", "door_code_mapping"))
        _apply_section(cfg, data, "air_shower",
                       ("duration", "agv_enter_timeout", "agv_exit_timeout"))
        _apply_section(cfg, data, "sim",
                       ("auto_open_delay", "auto_close_delay",
                        "zone_always_busy", "zone_id"))
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
    _apply_section(config, data, "zone", ("enter_url", "exit_url", "status_url", "zone_poll_interval"))
    _apply_section(config, data, "rcs", ("change_status_url", "report_interval", "door_code_mapping"))
    _apply_section(config, data, "air_shower",
                   ("duration", "agv_enter_timeout", "agv_exit_timeout"))
    _apply_section(config, data, "sim",
                   ("auto_open_delay", "auto_close_delay",
                    "zone_always_busy", "zone_id"))


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


# ═══════════════════════════════════════════
#  统一运行时配置 (/data/config.toml)
# ═══════════════════════════════════════════

CONFIG_VERSION = 1
UNIFIED_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "config.toml")


def _load_unified(cfg: AppConfig):
    """加载 /data/config.toml 统一配置（最高优先级）"""
    if not os.path.exists(UNIFIED_CONFIG_PATH):
        return
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
                       ("change_status_url", "report_interval", "door_code_mapping"))
        _apply_section(cfg, data, "air_shower",
                       ("duration", "agv_enter_timeout", "agv_exit_timeout"))
        _apply_section(cfg, data, "sim",
                       ("auto_open_delay", "auto_close_delay",
                        "zone_always_busy", "zone_id"))
        # 读取版本号
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
            for k in ("change_status_url", "report_interval"):
                if k in rcs:
                    sections.append(_toml_kv(k, rcs[k]))
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

        # 备份
        backup = ""
        if os.path.exists(UNIFIED_CONFIG_PATH):
            import shutil
            backup = UNIFIED_CONFIG_PATH + ".bak"
            shutil.copy2(UNIFIED_CONFIG_PATH, backup)

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


def _generate_default_config() -> dict:
    """生成默认统一配置"""
    return {
        "version": 0,
        "angel": {"base_url": "http://localhost:8080", "outer_door_id": "DOOR01", "inner_door_id": "DOOR02"},
        "zone": {"enter_url": "", "exit_url": "", "status_url": "", "zone_id": "zone_001",
                 "entry_door_code": "q001", "zone_poll_interval": 300.0},
        "rcs": {"change_status_url": "", "report_interval": 0.5,
                "door_code_mapping": {"DOOR01": "1001", "DOOR02": "1002"}},
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
