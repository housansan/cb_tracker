import re
import math
import os
import logging

import akshare as ak
import pandas as pd
from datetime import datetime

from bond.cache import get_cache, set_cache, read_local_cache, write_local_cache
from bond.fetch import (
    is_delisted,
    fetch_bond_kline,
    fetch_iss_amt_history,
    fetch_curr_iss_amt,
    fetch_all_cb_remaining,
    fetch_cov_value_analysis,
    fetch_bond_adj_logs,
    get_stock_history,
)
from bond.calc import parse_coupon_rates, calc_ytm_series
from bond.db import (
    query_daily,
    query_daily_latest_date,
    upsert_daily_batch,
    history_df_to_daily_rows,
)

logger = logging.getLogger("bond_history")


def daily_rows_to_df(rows: list) -> pd.DataFrame:
    """
    将 t_bond_daily 的 dict 列表还原为与 get_convertible_bond_history() 格式兼容的 DataFrame。
    整数字段除回浮点，列名还原为中文。
    """
    if not rows:
        return pd.DataFrame()

    def _from100(v):
        return round(v / 100, 4) if v is not None else None

    def _from10000(v):
        return round(v / 10000, 4) if v is not None else None

    records = []
    for r in rows:
        # trade_date: "YYYY-MM-DD 00:00:00" → "YYYYMMDD"
        td = str(r.get("trade_date", ""))
        date_str = td[:10].replace("-", "") if len(td) >= 10 else ""
        records.append({
            "日期":       date_str,
            "开盘价":     _from100(r.get("open")),
            "最高价":     _from100(r.get("high")),
            "最低价":     _from100(r.get("low")),
            "收盘价":     _from100(r.get("close")),
            "成交量":     r.get("volume"),
            "转股溢价率": _from10000(r.get("conv_premium_rate")),
            "到期收益率": _from10000(r.get("ytm")),
            "转股价值":   _from100(r.get("conv_value")),
            "剩余规模":   _from100(r.get("issue_size")),
            "正股收盘价": _from100(r.get("stock_close")),
        })
    return pd.DataFrame(records)


def get_convertible_bond_history(bond_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    获取可转债历史成交数据

    :param bond_code: 可转债代码，例如 "113701"（上交所）或 "127113"（深交所）
    :param start_date: 开始日期，格式 "YYYYMMDD"，例如 "20240101"
    :param end_date: 结束日期，格式 "YYYYMMDD"，例如 "20241231"
    :return: 历史成交数据 DataFrame
    """
    try:
        pure_code = bond_code.replace("sh", "").replace("sz", "").strip()

        # -------------------------------------------------------
        # 内存缓存（进程内有效，避免同一进程内重复查 DB）
        # -------------------------------------------------------
        full_cache_key = f"full_hist:{pure_code}:{start_date}:{end_date}"
        cached_df = get_cache(full_cache_key)
        if cached_df is not None:
            logger.info("[内存缓存命中] 可转债 [%s] 完整历史数据 %s~%s，共 %d 条", pure_code, start_date, end_date, len(cached_df))
            return cached_df.copy()

        # -------------------------------------------------------
        # DB 命中检查：优先从 SQLite 读取
        # -------------------------------------------------------
        delisted = is_delisted(pure_code)
        try:
            latest_date_ts = query_daily_latest_date(int(pure_code))
            # 将 trade_date "YYYY-MM-DD 00:00:00" 转为 "YYYYMMDD" 用于比较
            latest_date = latest_date_ts[:10].replace("-", "") if latest_date_ts else None

            # 判断 DB 数据是否完整：
            # - 退市债：DB 有数据且最新日期 >= end_date，视为完整
            # - 在途债：DB 最新日期 >= end_date（今天），视为完整
            db_complete = latest_date is not None and latest_date >= end_date

            if db_complete:
                # 构造查询用日期格式
                start_ts = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]} 00:00:00"
                end_ts   = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]} 00:00:00"
                db_rows = query_daily(
                    int(pure_code),
                    start_date=start_ts,
                    end_date=end_ts,
                    limit=10000,
                )
                if db_rows:
                    df_db = daily_rows_to_df(db_rows)
                    set_cache(full_cache_key, df_db)
                    logger.info("[DB命中] 可转债 [%s] 历史数据 %s~%s，共 %d 条", pure_code, start_date, end_date, len(df_db))
                    return df_db.copy()
        except Exception as ex:
            logger.warning("DB 查询失败，回退至接口拉取 [%s]：%s", pure_code, ex)

        logger.info("开始获取可转债 [%s] 历史成交数据 %s ~ %s", pure_code, start_date, end_date)
        df = fetch_bond_kline(pure_code, start_date, end_date)

        if df is None or df.empty:
            logger.warning("可转债 [%s] 未获取到历史成交数据", pure_code)
            return pd.DataFrame()

        df.reset_index(drop=True, inplace=True)

        def _safe_float(v):
            try:
                f = float(v)
                return None if (math.isnan(f) or math.isinf(f)) else f
            except Exception:
                return None

        # 合并历史转股溢价率和转股价值
        try:
            cov_df = fetch_cov_value_analysis(pure_code)
            if not cov_df.empty:
                cov_df = cov_df[(cov_df["日期"] >= start_date) & (cov_df["日期"] <= end_date)]
                df = df.merge(cov_df, on="日期", how="left")
                for col in ["转股溢价率", "转股价值"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                        df[col] = df[col].round(2 if col == "转股溢价率" else 3)
                        df[col] = df[col].apply(_safe_float)
                logger.info("可转债 [%s] 合并转股溢价率/转股价值成功", pure_code)
        except Exception as ex:
            logger.warning("获取历史转股溢价率失败（不影响主数据）[%s]：%s", pure_code, ex)

        # 合并历史剩余规模（按日期向前填充，单位：亿元）
        stock_code = None
        try:
            bond_info_df = ak.bond_zh_cov()
            bond_info_df["债券代码"] = bond_info_df["债券代码"].astype(str).str.strip()
            info_row = bond_info_df[bond_info_df["债券代码"] == pure_code]
            if not info_row.empty:
                stock_code = str(info_row.iloc[0].get("正股代码", "")).strip()
            iss_df = fetch_iss_amt_history(pure_code)
            if not iss_df.empty:
                df_sorted  = df.sort_values("日期").reset_index(drop=True)
                iss_sorted = iss_df.sort_values("日期").reset_index(drop=True)
                df_sorted["_date_int"]  = df_sorted["日期"].astype(int)
                iss_sorted["_date_int"] = iss_sorted["日期"].astype(int)
                merged = pd.merge_asof(
                    df_sorted, iss_sorted[["_date_int", "剩余规模"]],
                    on="_date_int", direction="backward"
                )
                merged.drop(columns=["_date_int"], inplace=True)
                merged["剩余规模"] = merged["剩余规模"].bfill()
                df = merged
                logger.info("可转债 [%s] 合并历史剩余规模成功", pure_code)
            else:
                df["剩余规模"] = fetch_curr_iss_amt(pure_code)
                logger.info("可转债 [%s] 使用当前剩余规模填充", pure_code)
        except Exception as ex:
            logger.warning("获取历史剩余规模失败（不影响主数据）[%s]：%s", pure_code, ex)

        # 合并正股历史收盘价
        try:
            if not stock_code:
                info_df = ak.bond_zh_cov_info(symbol=pure_code)
                if not info_df.empty:
                    stock_code = str(info_df.iloc[0].get("STOCK_CODE", "")).strip()
            if stock_code:
                stock_df = get_stock_history(stock_code, start_date, end_date)
                if not stock_df.empty:
                    df = df.merge(stock_df, on="日期", how="left")
                    df["正股收盘价"] = pd.to_numeric(df["正股收盘价"], errors="coerce").apply(_safe_float)
                    logger.info("可转债 [%s] 合并正股 [%s] 收盘价成功", pure_code, stock_code)
                else:
                    logger.warning("可转债 [%s] 正股 [%s] 历史收盘价为空", pure_code, stock_code)
            else:
                logger.warning("可转债 [%s] 未能获取正股代码，跳过合并正股收盘价", pure_code)
        except Exception as ex:
            logger.warning("合并正股收盘价失败（不影响主数据）[%s]：%s", pure_code, ex)

        # 计算历史到期收益率
        try:
            df["到期收益率"] = calc_ytm_series(df, pure_code)
            df["到期收益率"] = df["到期收益率"].apply(
                lambda v: None if (v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))) else round(v, 4)
            )
        except Exception as ex:
            logger.warning("计算历史到期收益率失败（不影响主数据）[%s]：%s", pure_code, ex)

        logger.info("可转债 [%s] 历史数据获取完成，共 %d 条记录", pure_code, len(df))

        # -------------------------------------------------------
        # 写入 DB（持久化，供下次直接命中）
        # -------------------------------------------------------
        try:
            db_rows = history_df_to_daily_rows(pure_code, df)
            if db_rows:
                upsert_daily_batch(db_rows)
                logger.info("[DB写入] 可转债 [%s] 写入 %d 条日线记录", pure_code, len(db_rows))
        except Exception as ex:
            logger.warning("写入 DB 失败（不影响返回结果）[%s]：%s", pure_code, ex)

        # 写入内存缓存
        set_cache(full_cache_key, df)

        return df

    except Exception as e:
        logger.error("获取可转债历史数据失败 bond_code=%s：%s", bond_code, e)
        return pd.DataFrame()


def get_all_convertible_bonds() -> pd.DataFrame:
    """
    获取当前市场上所有可转债的基本信息列表

    :return: 可转债列表 DataFrame，包含债券代码、债券简称、债现价、正股代码、正股简称、正股价、转股溢价率、剩余规模等
    """
    logger.info("开始获取全市场可转债列表")
    try:
        df = ak.bond_zh_cov()
        logger.info("全市场可转债列表获取成功，共 %d 只", len(df))
    except Exception as e:
        logger.error("获取可转债列表失败：%s", e)
        return pd.DataFrame()

    df["剩余规模"] = None

    return df


def build_cashflows_from_coupon_info(coupon_info: dict) -> tuple:
    """
    从已解析的付息信息字典，构建以「今日」为基准的现金流和时间列表。
    结构与 calc_ytm / calc_price_for_yield 期望的参数完全一致。

    :param coupon_info: get_bond_info() 返回的 info["付息信息"]，需含：
                        "付息日列表"（list[str] "YYYYMMDD"）
                        "票息率列表"（list[float] %）
                        "赎回价"（float 元，含最后一期利息）
    :return: (cashflows, times) 两个等长列表；无未来现金流时返回 ([], [])
    """
    pay_date_strs = coupon_info.get("付息日列表", [])
    rates         = coupon_info.get("票息率列表", [])
    redeem_price  = coupon_info.get("赎回价", 100.0)

    if not pay_date_strs or not rates:
        return [], []

    today    = datetime.today()
    last_idx = len(pay_date_strs) - 1

    cashflows, times = [], []
    for i, d_str in enumerate(pay_date_strs):
        try:
            pay_dt = datetime.strptime(d_str, "%Y%m%d")
        except ValueError:
            continue
        if pay_dt <= today:
            continue
        t = (pay_dt - today).days / 365.0
        if t <= 0:
            continue
        if i == last_idx:
            cashflows.append(float(redeem_price))
        else:
            coupon = 100.0 * rates[i] / 100.0  # 与 calc_ytm_series 第114行逻辑一致
            cashflows.append(coupon)
        times.append(t)

    return cashflows, times


def get_bond_info(bond_code: str) -> dict:
    """
    获取单只可转债的基础信息（名称、价格、正股代码、正股价格、转股溢价率等）

    :param bond_code: 可转债代码，如 "113050"
    :return: 包含基础信息的字典，若未找到则返回空字典
    """
    try:
        df = get_all_convertible_bonds()
        if df.empty:
            return {}
        df["债券代码"] = df["债券代码"].astype(str).str.strip()
        row = df[df["债券代码"] == bond_code.strip()]
        if row.empty:
            return {}
        r = row.iloc[0]

        def safe_float(val, default=0.0):
            """将 NaN / None 转为 default，避免 JSON 序列化失败"""
            try:
                v = float(val)
                return default if math.isnan(v) or math.isinf(v) else v
            except (TypeError, ValueError):
                return default

        # 获取上市/退市日期 & 付息信息
        listing_date = ""
        delist_date = ""
        coupon_info = {}
        try:
            pure = bond_code.strip()
            info_df = ak.bond_zh_cov_info(symbol=pure)
            if not info_df.empty:
                info_r = info_df.iloc[0]
                raw_listing = info_r.get("LISTING_DATE")
                raw_delist  = info_r.get("DELIST_DATE")
                if raw_listing:
                    listing_date = pd.to_datetime(raw_listing).strftime("%Y%m%d")
                if raw_delist:
                    delist_date = pd.to_datetime(raw_delist).strftime("%Y%m%d")

                # 解析付息信息（字段名兼容新旧版本）
                rate_explain = str(info_r.get("INTEREST_RATE_EXPLAIN", ""))
                coupon_rates = parse_coupon_rates(rate_explain)
                value_date   = pd.to_datetime(info_r.get("VALUE_DATE"))
                expire_date  = pd.to_datetime(info_r.get("EXPIRE_DATE") or info_r.get("CEASE_DATE"))

                # 赎回价 —— 优先从赎回条款取，再从利率说明取，最后回退到末期票息公式
                # 匹配 "面值的108%" 或 "面值108%"（部分债券省略 "的"）
                redeem_clause = str(info_r.get("REDEEM_CLAUSE", ""))
                m = (re.search(r'面值[的]?(\d+(?:\.\d+)?)%', redeem_clause) or
                     re.search(r'面值[的]?(\d+(?:\.\d+)?)%', rate_explain) or
                     re.search(r'(\d+(?:\.\d+)?)元[（(]含最后', rate_explain))
                redeem_ratio = float(m.group(1)) / 100 if m else (1 + (coupon_rates[-1] if coupon_rates else 0) / 100)
                redeem_price = round(100 * redeem_ratio, 4)

                # 生成付息日列表
                pay_dates = []
                if coupon_rates and not pd.isna(value_date) and not pd.isna(expire_date):
                    for i in range(1, len(coupon_rates) + 1):
                        try:
                            pd_date = value_date.replace(year=value_date.year + i)
                        except ValueError:
                            pd_date = value_date.replace(year=value_date.year + i, day=28)
                        if pd_date <= expire_date:
                            pay_dates.append(pd_date)
                    if not pay_dates or pay_dates[-1] != expire_date:
                        pay_dates.append(expire_date)
                    rates_used = coupon_rates[:len(pay_dates)]
                    while len(rates_used) < len(pay_dates):
                        rates_used.append(coupon_rates[-1])
                else:
                    rates_used = coupon_rates

                coupon_info = {
                    "起息日":     value_date.strftime("%Y%m%d") if not pd.isna(value_date) else "",
                    "到期日":     expire_date.strftime("%Y%m%d") if not pd.isna(expire_date) else "",
                    "赎回价":     redeem_price,
                    "赎回条款":   redeem_clause,
                    "利率说明":   rate_explain,
                    "票息率列表": rates_used,
                    "付息日列表": [d.strftime("%Y%m%d") for d in pay_dates],
                }
        except Exception:
            pass

        # 获取剩余规模（亿元），从全量东方财富接口取
        curr_iss_scale = None
        try:
            all_remaining = fetch_all_cb_remaining()
            curr_iss_scale = all_remaining.get(bond_code.strip())
        except Exception:
            pass

        # 计算剩余年限（到期日距今的天数 / 365，保留2位小数）
        remaining_years = None
        try:
            expire_date_str = coupon_info.get("到期日", "")
            if expire_date_str:
                expire_dt = datetime.strptime(expire_date_str, "%Y%m%d")
                delta_days = (expire_dt - datetime.today()).days
                remaining_years = round(max(delta_days, 0) / 365, 2)
        except Exception:
            pass

        return {
            "债券代码":   str(r.get("债券代码", "")),
            "债券简称":   str(r.get("债券简称", "")),
            "债现价":     safe_float(r.get("债现价")),
            "正股代码":   str(r.get("正股代码", "")),
            "正股简称":   str(r.get("正股简称", "")),
            "正股价":     safe_float(r.get("正股价")),
            "转股溢价率": safe_float(r.get("转股溢价率")),
            "转股价":     safe_float(r.get("转股价")),
            "转股价值":   safe_float(r.get("转股价值")),
            "信用评级":   str(r.get("信用评级", "")),
            "剩余规模":   curr_iss_scale,
            "剩余年限":   remaining_years,
            "上市日期":   listing_date,
            "退市日期":   delist_date,
            "付息信息":   coupon_info,
        }
    except Exception as e:
        logger.error("获取可转债基础信息失败 bond_code=%s：%s", bond_code, e)
        return {}


def fetch_bond_detail_only(bond_code: str) -> dict:
    """
    仅通过 ak.bond_zh_cov_info() 获取单只可转债的详细字段，
    不调用 get_all_convertible_bonds()，避免频繁拉取全量列表。
    用于后台补全 DB 中缺失的 listing_date / delist_date / 付息信息等字段。

    :param bond_code: 纯数字代码，如 "113050"
    :return: 包含详细字段的字典（格式与 get_bond_info 返回值兼容），失败返回空字典
    """
    try:
        pure = bond_code.strip()
        info_df = ak.bond_zh_cov_info(symbol=pure)
        if info_df.empty:
            return {}
        info_r = info_df.iloc[0]

        listing_date = ""
        delist_date = ""
        raw_listing = info_r.get("LISTING_DATE")
        raw_delist  = info_r.get("DELIST_DATE")
        if raw_listing:
            listing_date = pd.to_datetime(raw_listing).strftime("%Y%m%d")
        if raw_delist:
            delist_date = pd.to_datetime(raw_delist).strftime("%Y%m%d")

        # 解析付息信息
        rate_explain = str(info_r.get("INTEREST_RATE_EXPLAIN", ""))
        coupon_rates = parse_coupon_rates(rate_explain)
        value_date   = pd.to_datetime(info_r.get("VALUE_DATE"))
        expire_date  = pd.to_datetime(info_r.get("EXPIRE_DATE"))

        # 赎回价 —— 优先从赎回条款取，再从利率说明取，最后回退到末期票息公式
        # 匹配 "面值的108%" 或 "面值108%"（部分债券省略 "的"）
        redeem_clause = str(info_r.get("REDEEM_CLAUSE", ""))
        m = (re.search(r'面值[的]?(\d+(?:\.\d+)?)%', redeem_clause) or
             re.search(r'面值[的]?(\d+(?:\.\d+)?)%', rate_explain) or
             re.search(r'(\d+(?:\.\d+)?)元[（(]含最后', rate_explain))
        redeem_ratio = float(m.group(1)) / 100 if m else (1 + (coupon_rates[-1] if coupon_rates else 0) / 100)
        redeem_price = round(100 * redeem_ratio, 4)

        # 生成付息日列表
        pay_dates = []
        if coupon_rates and not pd.isna(value_date) and not pd.isna(expire_date):
            for i in range(1, len(coupon_rates) + 1):
                try:
                    pd_date = value_date.replace(year=value_date.year + i)
                except ValueError:
                    pd_date = value_date.replace(year=value_date.year + i, day=28)
                if pd_date <= expire_date:
                    pay_dates.append(pd_date)
            if not pay_dates or pay_dates[-1] != expire_date:
                pay_dates.append(expire_date)
            rates_used = coupon_rates[:len(pay_dates)]
            while len(rates_used) < len(pay_dates):
                rates_used.append(coupon_rates[-1])
        else:
            rates_used = coupon_rates

        coupon_info = {
            "起息日":     value_date.strftime("%Y%m%d") if not pd.isna(value_date) else "",
            "到期日":     expire_date.strftime("%Y%m%d") if not pd.isna(expire_date) else "",
            "赎回价":     redeem_price,
            "赎回条款":   redeem_clause,
            "利率说明":   rate_explain,
            "票息率列表": rates_used,
            "付息日列表": [d.strftime("%Y%m%d") for d in pay_dates],
        }

        # 剩余年限
        remaining_years = None
        try:
            expire_date_str = coupon_info.get("到期日", "")
            if expire_date_str:
                expire_dt = datetime.strptime(expire_date_str, "%Y%m%d")
                delta_days = (expire_dt - datetime.today()).days
                remaining_years = round(max(delta_days, 0) / 365, 2)
        except Exception:
            pass

        # 从 bond_zh_cov_info 中获取债券基础字段
        # 注意：akshare 接口字段名已更新，使用新字段名
        logger.info("[fetch_bond_detail_only] bond_code=%s 接口返回列名: %s", pure, list(info_df.columns))
        logger.info("[fetch_bond_detail_only] bond_code=%s SECURITY_NAME_ABBR=%r BOND_SHORT_NAME=%r SECURITY_CODE=%r CONVERT_STOCK_CODE=%r SECURITY_SHORT_NAME=%r",
                    pure,
                    info_r.get("SECURITY_NAME_ABBR"),
                    info_r.get("BOND_SHORT_NAME"),
                    info_r.get("SECURITY_CODE"),
                    info_r.get("CONVERT_STOCK_CODE"),
                    info_r.get("SECURITY_SHORT_NAME"))
        bond_code_str = str(info_r.get("SECURITY_CODE") or info_r.get("BOND_CODE") or pure).strip()
        bond_name     = str(info_r.get("SECURITY_NAME_ABBR") or info_r.get("BOND_SHORT_NAME") or "").strip()
        stock_code    = str(info_r.get("CONVERT_STOCK_CODE") or info_r.get("STOCK_CODE") or "").strip()
        stock_name    = str(info_r.get("SECURITY_SHORT_NAME") or info_r.get("STOCK_SHORT_NAME") or "").strip()
        logger.info("[fetch_bond_detail_only] bond_code=%s 解析结果 bond_name=%r stock_name=%r listing_date=%r",
                    pure, bond_name, stock_name, listing_date)

        def _safe_float(val, default=0.0):
            try:
                v = float(val)
                return default if (math.isnan(v) or math.isinf(v)) else v
            except (TypeError, ValueError):
                return default

        conv_price = _safe_float(info_r.get("TRANSFER_PRICE") or info_r.get("CONVERT_PRICE"))

        return {
            "债券代码":   bond_code_str or pure,
            "债券简称":   bond_name,
            "债现价":     None,
            "正股代码":   stock_code,
            "正股简称":   stock_name,
            "正股价":     None,
            "转股溢价率": None,
            "转股价":     conv_price,
            "转股价值":   None,
            "信用评级":   str(info_r.get("RATING") or info_r.get("CREDIT_RATING") or ""),
            "剩余规模":   None,
            "剩余年限":   remaining_years,
            "上市日期":   listing_date,
            "退市日期":   delist_date,
            "付息信息":   coupon_info,
        }
    except Exception as e:
        logger.error("fetch_bond_detail_only 失败 bond_code=%s：%s", bond_code, e)
        return {}


def get_bond_adj_logs(bond_code: str) -> list:
    """
    获取可转债转股价格调整记录。

    :param bond_code: 纯数字代码，如 "127099"
    :return: 调整记录列表，每条为字典；获取失败返回空列表
    """
    pure = bond_code.replace("sh", "").replace("sz", "").strip()
    return fetch_bond_adj_logs(pure)


def save_to_csv(df: pd.DataFrame, filename: str) -> None:
    """
    将数据保存为 CSV 文件，统一存放至 data/exports/ 目录

    :param df: 数据 DataFrame
    :param filename: 文件名（不含扩展名）
    """
    if df.empty:
        logger.warning("数据为空，跳过保存 filename=%s", filename)
        return
    from config import EXPORT_CONFIG
    export_dir = EXPORT_CONFIG["dir"]
    os.makedirs(export_dir, exist_ok=True)
    filepath = os.path.join(export_dir, f"{filename}.csv")
    df.to_csv(filepath, index=False, encoding="utf-8-sig")
    logger.info("数据已保存至：%s", filepath)


if __name__ == "__main__":
    # -------------------------------------------------------
    # 示例1：获取单只可转债的历史成交数据
    # -------------------------------------------------------
    BOND_CODE  = "113050"   # 可转债代码（示例：南银转债）
    START_DATE = "20240101"
    END_DATE   = datetime.today().strftime("%Y%m%d")

    df_history = get_convertible_bond_history(BOND_CODE, START_DATE, END_DATE)

    if not df_history.empty:
        print("\n=== 历史成交数据（前5条）===")
        print(df_history.head())
        print("\n=== 数据列信息 ===")
        print(df_history.dtypes)
        # 保存到 CSV
        save_to_csv(df_history, f"bond_{BOND_CODE}_history")

    # -------------------------------------------------------
    # 示例2：获取全市场可转债列表
    # -------------------------------------------------------
    # df_list = get_all_convertible_bonds()
    # if not df_list.empty:
    #     print("\n=== 可转债列表（前5条）===")
    #     print(df_list.head())
    #     save_to_csv(df_list, "all_convertible_bonds")
