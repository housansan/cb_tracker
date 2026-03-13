"""
config.py —— 统一配置入口
读取 ../conf/config.toml，对外暴露各模块所需的配置常量。
其他模块只需 `from config import LOG_CONFIG, CACHE_CONFIG, EXPORT_CONFIG` 即可使用。
所有路径均为相对路径（相对于项目根目录），由各模块自行拼接使用。
"""

import os
import sys

# 项目根目录（config.py 所在目录）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 配置文件目录，默认为 ../conf
_CONF_DIR = os.path.join(BASE_DIR, "..", "conf")
_CONFIG_PATH = os.path.join(_CONF_DIR, "config.toml")

# ── 默认值 ────────────────────────────────────────────────────────────────────
_DEFAULTS = {
    "log": {
        "dir":          "../log",
        "max_bytes":    10 * 1024 * 1024,   # 10 MB
        "backup_count": 30,
        "level":        "INFO",
    },
    "cache": {
        "dir": "../cache/bond",
    },
    "export": {
        "dir": "../exports",
    },
    "db": {
        "dir": "../data",
    },
    "bond": {
        "fill_details_sleep": 0.2,
    },
    "network": {
        "proxy": "",   # 默认不走代理，直连目标服务器
    },
}


def _load_toml(path: str) -> dict:
    """读取 TOML 文件，Python 3.11+ 使用内置 tomllib，否则尝试 tomli"""
    if not os.path.exists(path):
        print(f"[config] 配置文件不存在：{path}，使用默认配置")
        return {}
    try:
        if sys.version_info >= (3, 11):
            import tomllib
            with open(path, "rb") as f:
                return tomllib.load(f)
        else:
            import tomli  # pip install tomli
            with open(path, "rb") as f:
                return tomli.load(f)
    except Exception as e:
        print(f"[config] 读取 config.toml 失败，使用默认配置：{e}")
        return {}


_cfg = _load_toml(_CONFIG_PATH)

# 标识配置文件是否成功加载（可供外部模块判断）
config_loaded: bool = bool(_cfg)


def _resolve_path(raw: str, default: str) -> str:
    """
    返回路径字符串：
    - 若配置中有值则使用配置值，否则使用 default
    - 相对路径以项目根目录（BASE_DIR）为基准，转换为绝对路径
    - 绝对路径直接返回
    """
    p = raw if raw else default
    if os.path.isabs(p):
        return p
    return os.path.normpath(os.path.join(BASE_DIR, p))


def _get(section: str, key: str):
    """从已加载配置中取值，缺失时回退到 _DEFAULTS"""
    return _cfg.get(section, {}).get(key, _DEFAULTS.get(section, {}).get(key))


# ── 日志配置 ─────────────────────────────────────────────────────────────────
LOG_CONFIG = {
    # 日志目录
    "dir":          _resolve_path(_get("log", "dir"), _DEFAULTS["log"]["dir"]),
    # 单文件最大字节数
    "max_bytes":    int(_get("log", "max_bytes")),
    # 最多保留的历史日志文件个数
    "backup_count": int(_get("log", "backup_count")),
    # 日志级别字符串，如 "INFO"
    "level":        str(_get("log", "level")).upper(),
}

# ── 缓存配置 ─────────────────────────────────────────────────────────────────
CACHE_CONFIG = {
    # 本地文件缓存根目录
    "dir": _resolve_path(_get("cache", "dir"), _DEFAULTS["cache"]["dir"]),
}

# ── 导出配置 ─────────────────────────────────────────────────────────────────
EXPORT_CONFIG = {
    # 导出文件保存目录
    "dir": _resolve_path(_get("export", "dir"), _DEFAULTS["export"]["dir"]),
}

# ── 数据库配置 ────────────────────────────────────────────
DB_CONFIG = {
    # SQLite 数据库文件存放目录
    "dir": _resolve_path(_get("db", "dir"), _DEFAULTS["db"]["dir"]),
}

# ── 可转债业务配置 ─────────────────────────────────────────
BOND_CONFIG = {
    # 后台补全线程每次请求之间的间隔时间（秒）
    "fill_details_sleep": float(_get("bond", "fill_details_sleep")),
}

# ── 网络配置 ──────────────────────────────────────────────
NETWORK_CONFIG = {
    # 代理配置，支持三种模式：
    #   ""                      → 不走代理，直连目标服务器
    #   "system"                → 使用系统代理（HTTP_PROXY / HTTPS_PROXY 环境变量）
    #   "http://127.0.0.1:7890" → 使用指定代理地址
    "proxy": str(_get("network", "proxy")),
}