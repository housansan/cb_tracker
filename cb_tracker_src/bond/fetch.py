import math
import time
import logging

import akshare as ak
import pandas as pd
import requests

from bond.cache import get_cache, set_cache
from config import NETWORK_CONFIG

logger = logging.getLogger("bond_history")

# 根据配置构建 requests 的 proxies 参数
# proxy = ""         → {"http": None, "https": None}，忽略系统代理，直连目标服务器
# proxy = "system"   → None，让 requests 跟随系统代理（HTTP_PROXY / HTTPS_PROXY）
# proxy = "http://…" → {"http": <addr>, "https": <addr>}，使用指定代理地址
def _build_proxies(proxy: str):
    proxy = (proxy or "").strip()
    if proxy == "system":
        return None
    if proxy:
        return {"http": proxy, "https": proxy}
    return {"http": None, "https": None}

_PROXIES = _build_proxies(NETWORK_CONFIG.get("proxy", ""))


def is_delisted(bond_code: str) -> bool:
    """
    判断可转债是否已退市（退市日期早于今天）。
    通过 bond_zh_cov_info 接口获取退市日期，结果缓存在内存中。
    """
    from datetime import date
    pure = bond_code.replace("sh", "").replace("sz", "").strip()
    cache_key = f"delist_check:{pure}"
    cached = get_cache(cache_key)
    if cached is not None:
        return cached

    try:
        info_df = ak.bond_zh_cov_info(symbol=pure)
        if info_df.empty:
            set_cache(cache_key, False)
            return False
        info = info_df.iloc[0]
        raw_delist = info.get("DELIST_DATE")
        if raw_delist:
            delist_date = pd.to_datetime(raw_delist).date()
            result = delist_date < date.today()
        else:
            result = False
    except Exception:
        result = False

    set_cache(cache_key, result)
    return result


def fetch_bond_kline(bond_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    通过 akshare bond_zh_hs_cov_daily 接口获取可转债历史 K 线数据（支持所有可转债）。
    结果按 (bond_code, start_date, end_date) 缓存在内存中，当天内有效。

    :param bond_code: 纯数字代码，如 "113701" 或 "123200"
    :param start_date: 开始日期 YYYYMMDD
    :param end_date: 结束日期 YYYYMMDD
    :return: DataFrame，列：日期、开盘价、最高价、最低价、收盘价、成交量
    """
    pure = bond_code.replace("sh", "").replace("sz", "").strip()

    cache_key = f"hist:{pure}:{start_date}:{end_date}"
    cached = get_cache(cache_key)
    if cached is not None:
        logger.debug("[内存缓存命中] 可转债 [%s] 历史K线 %s~%s", pure, start_date, end_date)
        return cached.copy()

    # 上交所：110xxx、113xxx、118xxx；深交所：其余
    if pure.startswith(("110", "113", "118")):
        symbol = f"sh{pure}"
    else:
        symbol = f"sz{pure}"

    logger.info("请求接口获取可转债历史K线 symbol=%s", symbol)
    df = ak.bond_zh_hs_cov_daily(symbol=symbol)
    if df is None or df.empty:
        logger.warning("可转债历史K线数据为空 symbol=%s", symbol)
        return pd.DataFrame()

    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
    df = df.rename(columns={
        "date":   "日期",
        "open":   "开盘价",
        "close":  "收盘价",
        "high":   "最高价",
        "low":    "最低价",
        "volume": "成交量",
    })
    full_result = df[["日期", "开盘价", "收盘价", "最高价", "最低价", "成交量"]].reset_index(drop=True)

    filtered = full_result[(full_result["日期"] >= start_date) & (full_result["日期"] <= end_date)].reset_index(drop=True)
    set_cache(cache_key, filtered)
    logger.info("可转债 [%s] 历史K线共 %d 条", pure, len(filtered))
    return filtered.copy()


def fetch_iss_amt_history(bond_code: str) -> pd.DataFrame:
    """
    通过集思录接口获取可转债历史剩余规模变更记录（亿元）。
    结果按 bond_code 缓存在内存中，当天内有效。

    :param bond_code: 纯数字代码，如 "123200"
    :return: DataFrame，列：日期(YYYYMMDD)、剩余规模(亿元)；获取失败返回空 DataFrame
    """
    pure = bond_code.replace("sh", "").replace("sz", "").strip()
    local_key = f"iss_amt_hist:{pure}"

    cached = get_cache(local_key)
    if cached is not None:
        logger.debug("[内存缓存命中] 可转债 [%s] 剩余规模历史", pure)
        return cached.copy()

    logger.info("请求集思录接口获取可转债 [%s] 剩余规模历史", pure)
    try:
        url = f"https://www.jisilu.cn/data/cbnew/detail_hist/{pure}"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.jisilu.cn/"}
        r = requests.get(url, headers=headers, timeout=10, proxies=_PROXIES)
        data = r.json()
        rows = data.get("rows", [])
        if rows:
            records = []
            for row in rows:
                cell = row["cell"]
                dt_str = cell.get("last_chg_dt", "")
                val    = cell.get("curr_iss_amt")
                if dt_str and val is not None:
                    try:
                        f = float(val)
                        if not (math.isnan(f) or math.isinf(f)):
                            records.append({
                                "日期": pd.to_datetime(dt_str).strftime("%Y%m%d"),
                                "剩余规模": f,
                            })
                    except Exception:
                        pass
            if records:
                df = pd.DataFrame(records).sort_values("日期").reset_index(drop=True)
                set_cache(local_key, df)
                logger.info("可转债 [%s] 剩余规模历史获取成功，共 %d 条", pure, len(df))
                return df.copy()
        logger.warning("可转债 [%s] 剩余规模历史数据为空", pure)
    except Exception as e:
        logger.error("获取剩余规模历史失败 [%s]：%s", pure, e)
    return pd.DataFrame()


def fetch_all_cb_remaining() -> dict:
    """
    一次性获取全市场所有已上市可转债的剩余规模（亿元）。
    来源：东方财富 push2 接口（fs=b:MK0354 可转债板块）。
    剩余规模(亿) = f20(流通市值,元) × 100 / f2(当前价) / 1e8
    结果缓存在内存中，当天内有效。
    请求失败时记录失败时间，5分钟内不重试，避免频繁请求失败接口。

    :return: dict，key 为纯数字债券代码，value 为剩余规模（亿元）；失败返回空 dict
    """
    cache_key = "all_cb_remaining"
    fail_key  = "all_cb_remaining_fail_ts"
    _FAIL_COOLDOWN = 300  # 失败后冷却时间（秒），5分钟内不重试

    cached = get_cache(cache_key)
    if cached is not None:
        logger.debug("[内存缓存命中] 全市场可转债剩余规模")
        return cached

    # 检查是否在失败冷却期内
    fail_ts = get_cache(fail_key)
    if fail_ts is not None:
        elapsed = time.time() - fail_ts
        if elapsed < _FAIL_COOLDOWN:
            logger.warning("[剩余规模] 上次请求失败，冷却中（还剩 %.0f 秒），跳过重试", _FAIL_COOLDOWN - elapsed)
            return {}

    logger.info("请求东方财富接口获取全市场可转债剩余规模")
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://quote.eastmoney.com/center/gridlist.html#convertible_bond",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive",
    }
    result = {}
    page = 1
    total = 999
    all_items = []

    try:
        while len(all_items) < total:
            params = {
                "pn": page, "pz": 100, "po": 1, "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2, "invt": 2, "fid": "f20",
                "fs": "b:MK0354",
                "fields": "f12,f14,f2,f20",
            }
            r = requests.get(url, params=params, headers=headers, timeout=10,
                             proxies=_PROXIES)
            data = r.json().get("data", {})
            if not data:
                break
            total = data.get("total", 0)
            items = data.get("diff", [])
            if not items:
                break
            all_items.extend(items)
            logger.debug("fetch_all_cb_remaining page=%d +%d %d/%d", page, len(items), len(all_items), total)
            page += 1
            if page > 1:
                time.sleep(0.2)

        for item in all_items:
            code  = str(item.get("f12", "")).strip()
            price = item.get("f2", "-")
            f20   = item.get("f20", "-")
            if not code:
                continue
            try:
                remain = round(float(f20) * 100 / float(price) / 1e8, 2)
                result[code] = remain
            except Exception:
                pass

        set_cache(cache_key, result)
        logger.info("全市场可转债剩余规模获取成功，共 %d 只", len(result))
    except Exception as e:
        logger.error("获取全市场可转债剩余规模失败：%s", e)
        # 记录失败时间戳，冷却期内不重试
        set_cache(fail_key, time.time())

    return result


def fetch_curr_iss_amt(bond_code: str):
    """
    获取可转债当前（最新）剩余规模（亿元）。
    优先从全量东方财富接口缓存中取值，失败时回退到集思录历史记录最后一条。

    :param bond_code: 纯数字代码，如 "123200"
    :return: 剩余规模（亿元），获取失败返回 None
    """
    pure = bond_code.replace("sh", "").replace("sz", "").strip()
    # 优先从全量接口取
    try:
        all_remaining = fetch_all_cb_remaining()
        if pure in all_remaining:
            return all_remaining[pure]
    except Exception as e:
        logger.warning("从全量接口获取剩余规模失败 [%s]：%s，回退到集思录", pure, e)
    # 回退：集思录历史记录最后一条
    df = fetch_iss_amt_history(pure)
    if not df.empty:
        return df.iloc[-1]["剩余规模"]
    return None


def fetch_cov_value_analysis(bond_code: str) -> pd.DataFrame:
    """
    通过 akshare bond_zh_cov_value_analysis 接口获取可转债历史价值分析数据。
    结果按 bond_code 缓存在内存中，当天内有效。

    :param bond_code: 纯数字代码，如 "127099"
    :return: DataFrame，列：日期(YYYYMMDD)、转股价值、转股溢价率；获取失败返回空 DataFrame
    """
    pure = bond_code.replace("sh", "").replace("sz", "").strip()
    local_key = f"cov_value:{pure}"

    cached = get_cache(local_key)
    if cached is not None:
        logger.debug("[内存缓存命中] 可转债 [%s] 价值分析数据", pure)
        return cached.copy()

    logger.info("请求接口获取可转债 [%s] 价值分析数据", pure)
    try:
        df = ak.bond_zh_cov_value_analysis(symbol=pure)
        if df is None or df.empty:
            logger.warning("可转债 [%s] 价值分析数据为空", pure)
            return pd.DataFrame()
        df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y%m%d")
        cols = ["日期"]
        if "转股价值" in df.columns:
            cols.append("转股价值")
        if "转股溢价率" in df.columns:
            cols.append("转股溢价率")
        result = df[cols].reset_index(drop=True)
        set_cache(local_key, result)
        logger.info("可转债 [%s] 价值分析数据获取成功，共 %d 条", pure, len(result))
        return result.copy()
    except Exception as e:
        logger.error("获取价值分析数据失败 [%s]：%s", pure, e)
        return pd.DataFrame()


def fetch_bond_adj_logs(bond_code: str) -> list:
    """
    获取可转债转股价格调整记录（集思录接口）。
    结果按 bond_code 缓存在内存中，当天内有效。

    :param bond_code: 纯数字代码，如 "127099"
    :return: 调整记录列表，每条为字典；获取失败返回空列表
    """
    import math as _math
    pure = bond_code.replace("sh", "").replace("sz", "").strip()
    local_key = f"adj_logs:{pure}"

    cached = get_cache(local_key)
    if cached is not None:
        logger.debug("[内存缓存命中] 可转债 [%s] 转股价调整记录", pure)
        return cached

    logger.info("请求集思录接口获取可转债 [%s] 转股价调整记录", pure)
    try:
        df = ak.bond_cb_adj_logs_jsl(symbol=pure)
        if df is None or df.empty:
            logger.info("可转债 [%s] 无转股价调整记录", pure)
            set_cache(local_key, [])
            return []
        records = []
        for _, row in df.iterrows():
            record = {}
            for col in df.columns:
                val = row[col]
                if hasattr(val, 'strftime'):
                    val = val.strftime("%Y-%m-%d")
                elif pd.isna(val) if not isinstance(val, str) else False:
                    val = None
                else:
                    try:
                        f = float(val)
                        if _math.isnan(f) or _math.isinf(f):
                            val = None
                        else:
                            val = f
                    except (TypeError, ValueError):
                        val = str(val) if val is not None else None
                record[col] = val
            records.append(record)
        set_cache(local_key, records)
        logger.info("可转债 [%s] 转股价调整记录获取成功，共 %d 条", pure, len(records))
        return records
    except Exception as e:
        logger.error("获取转股价调整记录失败 [%s]：%s", pure, e)
        return []


def get_stock_history(stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    获取正股历史收盘价

    :param stock_code: 正股代码，如 "601009"
    :param start_date: 开始日期 YYYYMMDD
    :param end_date: 结束日期 YYYYMMDD
    :return: 包含 日期、收盘 列的 DataFrame
    """
    logger.info("获取正股历史数据 stock_code=%s start=%s end=%s", stock_code, start_date, end_date)
    try:
        df = ak.stock_zh_a_hist(symbol=stock_code, period="daily",
                                start_date=start_date, end_date=end_date, adjust="")
        if df is None or df.empty:
            logger.warning("正股历史数据为空 stock_code=%s", stock_code)
            return pd.DataFrame()
        df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y%m%d")
        logger.info("正股历史数据获取成功 stock_code=%s 共 %d 条", stock_code, len(df))
        return df[["日期", "收盘"]].rename(columns={"收盘": "正股收盘价"})
    except Exception as e:
        logger.error("获取正股历史数据失败 stock_code=%s：%s", stock_code, e)
        return pd.DataFrame()
