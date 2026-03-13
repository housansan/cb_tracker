import os
import json
import logging
from datetime import date

logger = logging.getLogger("bond_history")

# -------------------------------------------------------
# 内存缓存：key -> (缓存日期, 数据)
# 缓存当天有效，次日自动失效
# -------------------------------------------------------
_cache: dict = {}

# -------------------------------------------------------
# 本地文件缓存目录（从 config.py 读取，不存在则使用默认值）
# -------------------------------------------------------
try:
    from config import CACHE_CONFIG
    _LOCAL_CACHE_DIR = CACHE_CONFIG["dir"]
except Exception:
    # 单独测试 bond 模块时的回退路径
    _LOCAL_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bond_cache")


def ensure_cache_dir():
    """确保本地缓存目录存在"""
    os.makedirs(_LOCAL_CACHE_DIR, exist_ok=True)


def local_cache_path(cache_key: str) -> str:
    """
    根据 cache_key 生成本地文件路径，按前缀分类存放到子目录：
      hist_full      -> kline/       （K线全量历史）
      full_hist      -> merged/      （合并后完整历史）
      cov_value      -> cov_value/   （价值分析数据）
      iss_amt_hist   -> iss_amt/     （剩余规模历史）
      adj_logs       -> adj_logs/    （转股价调整记录）
      其他           -> misc/        （其他）
    """
    _PREFIX_DIR_MAP = {
        "hist_full":    "kline",
        "full_hist":    "merged",
        "cov_value":    "cov_value",
        "iss_amt_hist": "iss_amt",
        "adj_logs":     "adj_logs",
    }
    sub_dir = "misc"
    for prefix, dirname in _PREFIX_DIR_MAP.items():
        if cache_key.startswith(prefix):
            sub_dir = dirname
            break
    safe_key = cache_key.replace(":", "_")
    target_dir = os.path.join(_LOCAL_CACHE_DIR, sub_dir)
    os.makedirs(target_dir, exist_ok=True)
    return os.path.join(target_dir, f"{safe_key}.json")


def read_local_cache(cache_key: str):
    """
    从本地文件读取缓存数据。
    :return: 原始数据（list/dict），不存在则返回 None
    """
    path = local_cache_path(cache_key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.debug("[本地文件缓存命中] %s", cache_key)
        return data
    except Exception as e:
        logger.warning("读取本地缓存失败 [%s]：%s", cache_key, e)
        return None


def write_local_cache(cache_key: str, data) -> None:
    """
    将数据写入本地文件缓存。
    data 可以是 list 或 dict（DataFrame 请先转为 records list）。
    """
    path = local_cache_path(cache_key)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("[本地文件缓存写入] %s -> %s", cache_key, path)
    except Exception as e:
        logger.warning("写入本地缓存失败 [%s]：%s", cache_key, e)


def get_cache(key: str):
    """获取缓存，若不存在或已过期（非今天）则返回 None"""
    entry = _cache.get(key)
    if entry is None:
        return None
    cached_date, data = entry
    if cached_date != date.today():
        del _cache[key]
        return None
    return data


def set_cache(key: str, data) -> None:
    """写入缓存，附带今天的日期"""
    _cache[key] = (date.today(), data)
