"""
LOF 基金数据模块。

参考 https://github.com/mydreamworldpolly/LOF-Fund-Tools 的套利分析思路实现：
通过比较二级市场价格与基金净值/估值，计算溢价率/折价率，识别套利机会。

数据源（均避开 push2.eastmoney.com，使用稳定接口）：
  - 行情：ak.fund_etf_category_sina("LOF基金")  → 市价、成交额、涨跌幅
  - 估值：ak.fund_value_estimation_em("LOF")     → 实时估算值（部分基金有）
  - 申赎：ak.fund_purchase_em()                   → 申购/赎回状态 + 最新净值（净值兜底）

溢价率口径：(市价 / 参考净值 - 1) × 100
  参考净值优先级：实时估算值 → 最新单位净值
"""
import re
import math
import logging

import akshare as ak
import pandas as pd

logger = logging.getLogger("bond_history")


def _safe_float(v):
    """将字符串/NaN/None 安全转为 float，失败返回 None"""
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _pure_code(code) -> str:
    """去掉 sz/sh 前缀，补齐 6 位纯数字代码"""
    s = re.sub(r'^(sh|sz)', '', str(code).strip(), flags=re.IGNORECASE)
    return s.zfill(6) if s.isdigit() else s


def _clean_date_str(v) -> str:
    """清洗日期字符串，过滤 pandas 空值占位（NaT/nan/None）"""
    s = str(v or "").strip()
    if s in ("", "NaT", "nan", "NaN", "None", "-"):
        return ""
    return s


def get_all_lof_funds() -> list:
    """
    获取全市场 LOF 基金列表，含市价、净值、溢价率、成交额、申赎状态等。

    :return: list[dict]，每只基金一条；获取失败返回空列表
    """
    # ── 1. 行情（新浪 LOF 分类）──────────────────────────────────────────────
    try:
        spot = ak.fund_etf_category_sina(symbol="LOF基金")
    except Exception as e:
        logger.error("[lof] 获取 LOF 行情失败：%s", e)
        return []
    if spot is None or spot.empty:
        logger.warning("[lof] LOF 行情为空")
        return []
    spot = spot.copy()
    spot["code"] = spot["代码"].map(_pure_code)

    # ── 2. 实时估值（东方财富，部分基金有）─────────────────────────────────────
    est_map = {}  # code -> 估算值
    try:
        est = ak.fund_value_estimation_em(symbol="LOF")
        if est is not None and not est.empty:
            est = est.copy()
            est["code"] = est["基金代码"].map(_pure_code)
            # 列名带动态日期前缀，如 "2026-06-30-估算数据-估算值"
            est_cols = [c for c in est.columns if "估算数据-估算值" in c]
            if est_cols:
                col = est_cols[0]
                for _, r in est.iterrows():
                    v = _safe_float(r.get(col))
                    if v is not None:
                        est_map[r["code"]] = v
    except Exception as e:
        logger.warning("[lof] 获取 LOF 估值失败（不影响主数据）：%s", e)

    # ── 3. 申赎状态 + 最新净值（净值兜底）─────────────────────────────────────
    nav_map = {}     # code -> 最新单位净值
    status_map = {}  # code -> {"申购状态", "赎回状态", "基金类型", "日累计限定金额", "购买起点"}
    try:
        pur = ak.fund_purchase_em()
        if pur is not None and not pur.empty:
            pur = pur.copy()
            pur["code"] = pur["基金代码"].map(_pure_code)
            nav_cols = [c for c in pur.columns if "最新净值" in c and "时间" not in c]
            nav_col = nav_cols[0] if nav_cols else None
            for _, r in pur.iterrows():
                code = r["code"]
                if nav_col:
                    v = _safe_float(r.get(nav_col))
                    if v is not None and v > 0:
                        nav_map[code] = v
                status_map[code] = {
                    "申购状态": str(r.get("申购状态") or "").strip(),
                    "赎回状态": str(r.get("赎回状态") or "").strip(),
                    "基金类型": str(r.get("基金类型") or "").strip(),
                    "日累计限定金额": _safe_float(r.get("日累计限定金额")),  # 元，0/None 表示无限制
                    "购买起点": _safe_float(r.get("购买起点")),            # 元
                    "申购费率": _safe_float(r.get("手续费")),              # %，申购成本（套利成本）
                    "下一开放日": _clean_date_str(r.get("下一开放日")),      # 定开基金下次可申购日
                }
    except Exception as e:
        logger.warning("[lof] 获取 LOF 申赎状态失败（不影响主数据）：%s", e)

    # 注：官方折价率接口 fund_etf_fund_daily_em 仅覆盖 ETF（5xxxxx/159xxx），
    # 不含 LOF（16xxxx/50xxxx），实测与 LOF 交集为 0，故不采用。
    # LOF 折溢价以自算「溢价率」为准（估值/净值口径）。

    # ── 4. 合并 + 计算溢价率 ───────────────────────────────────────────────────
    records = []
    for _, row in spot.iterrows():
        code = row["code"]
        price = _safe_float(row.get("最新价"))
        amount = _safe_float(row.get("成交额"))  # 元
        change_pct = _safe_float(row.get("涨跌幅"))

        # 非交易时段（盘前/盘后）新浪接口最新价返回 0，用昨收兜底，
        # 使溢价率在非交易时段也可参考（标记价格来源便于前端区分）。
        # 注意：停牌/退市的 LOF 昨收会是占位值 1.0（且最新价、今开均为 0），
        # 需剔除，否则会算出 300%+ 的虚假溢价率。
        prev_close = _safe_float(row.get("昨收"))
        open_price = _safe_float(row.get("今开"))
        is_placeholder = (prev_close == 1.0 and not (price and price > 0)
                          and not (open_price and open_price > 0))
        if is_placeholder:
            price = None
            price_source = "无行情"
        elif not (price and price > 0) and prev_close and prev_close > 0:
            price = prev_close
            price_source = "昨收"
        else:
            price_source = "实时"

        est_val = est_map.get(code)
        nav_val = nav_map.get(code)
        # 参考净值：估值优先，净值兜底
        ref_nav = est_val if est_val and est_val > 0 else nav_val
        nav_source = "估值" if (est_val and est_val > 0) else ("净值" if nav_val else "")

        premium_rate = None
        if price and price > 0 and ref_nav and ref_nav > 0:
            premium_rate = round((price / ref_nav - 1) * 100, 3)

        status = status_map.get(code, {})
        buy_fee = status.get("申购费率")
        # 净溢价 = 溢价率 − 申购费率（溢价套利的真实空间，正值才有套利可能）
        net_premium = None
        if premium_rate is not None:
            net_premium = round(premium_rate - (buy_fee or 0), 3)
        records.append({
            "代码":       code,
            "名称":       str(row.get("名称") or "").strip(),
            "最新价":     price,
            "价格来源":   price_source,   # "实时" | "昨收"
            "估算值":     est_val,
            "单位净值":   nav_val,
            "参考净值":   ref_nav,
            "净值来源":   nav_source,
            "溢价率":     premium_rate,   # >0 溢价，<0 折价
            "申购费率":   buy_fee,        # %，申购成本
            "净溢价":     net_premium,    # 溢价率 − 申购费率
            "成交额":     round(amount, 0) if amount is not None else None,  # 元
            "涨跌幅":     change_pct,
            "申购状态":   status.get("申购状态", ""),
            "赎回状态":   status.get("赎回状态", ""),
            "基金类型":   status.get("基金类型", ""),
            "下一开放日": status.get("下一开放日", ""),  # 定开基金下次可申购日
            "日累计限定金额": status.get("日累计限定金额"),  # 元，限大额时的单日上限
            "购买起点":   status.get("购买起点"),           # 元，最低申购金额
        })

    logger.info("[lof] LOF 数据合并完成：行情 %d 只，估值 %d 只，净值 %d 只，有效溢价率 %d 只",
                len(spot), len(est_map), len(nav_map),
                sum(1 for r in records if r["溢价率"] is not None))
    return records
