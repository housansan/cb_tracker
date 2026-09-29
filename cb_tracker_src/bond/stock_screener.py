"""
股票粗选模块。

策略：全市场按 PE（市盈率）和 PB（市净率）两个维度分别取前 100 只（剔除负值/零），
合计返回 200 条记录（不去重，来源字段标注 "PE" 或 "PB"）。

数据来源（均不依赖被屏蔽的 push2.eastmoney.com）：
  - A股行情：datacenter-web.eastmoney.com / RPT_DMSK_TS_STOCKNEW（实时 PE/PB/市值）
  - A股行业：datacenter-web.eastmoney.com / RPT_LICO_FN_CPD（最新财报期行业分类）
  - 港股：datacenter.eastmoney.com / RPT_HKF10_FN_MAININDICATOR（最新财报期 PE/PB）
"""
import math
import time
import logging
from datetime import datetime, timedelta

import requests

logger = logging.getLogger("bond_history")

_A_SHARE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_HK_SHARE_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_PAGE_SIZE = 500
_REQUEST_TIMEOUT = 15
_RETRY_TIMES = 3
_RETRY_DELAY = 3

# 港股行业接口
_HK_IND_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_HK_IND_REPORT = "RPT_PCF10_INDUSTRY_HKCVALUE"

# 港股行业列表缓存（TTL 24 小时）
_hk_industry_list_cache: dict = {"expire_at": None, "data": None}


def _fetch_hk_industry_for_codes(codes: list) -> dict:
    """港股精确行业查询：code -> TYPE_NAME"""
    if not codes:
        return {}
    result: dict = {}
    batch_size = 100
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        code_list = ",".join(f'"{c}"' for c in batch)
        params = {
            "pageSize": str(batch_size * 5),
            "pageNumber": "1",
            "reportName": _HK_IND_REPORT,
            "columns": "SECURITY_CODE,TYPE_NAME",
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "filter": f"(SECURITY_CODE in ({code_list}))",
        }
        try:
            data = _get_json(_HK_IND_URL, params)
            items = (data.get("result") or {}).get("data") or []
            for item in items:
                code = item.get("SECURITY_CODE")
                if code and code not in result:
                    result[code] = item.get("TYPE_NAME") or ""
        except Exception as e:
            logger.warning("[screener] 港股行业查询批次 %d 失败：%s", i // batch_size + 1, e)
    return result


def fetch_hk_industry_list() -> list:
    """获取港股所有行业分类列表，缓存 24 小时"""
    now = datetime.now()
    if _hk_industry_list_cache["expire_at"] and _hk_industry_list_cache["expire_at"] > now:
        return _hk_industry_list_cache["data"]

    industries: set = set()
    zero_new = 0
    for page in range(1, 20):
        params = {
            "pageSize": "500",
            "pageNumber": str(page),
            "reportName": _HK_IND_REPORT,
            "columns": "TYPE_NAME",
            "sortColumns": "REPORT_DATE,SECURITY_CODE",
            "sortTypes": "-1,1",
        }
        try:
            data = _get_json(_HK_IND_URL, params)
            items = (data.get("result") or {}).get("data") or []
        except Exception:
            break
        if not items:
            break
        prev = len(industries)
        for item in items:
            name = (item.get("TYPE_NAME") or "").strip()
            if name:
                industries.add(name)
        if len(industries) == prev:
            zero_new += 1
            if zero_new >= 2:
                break
        else:
            zero_new = 0

    result = sorted(industries)
    _hk_industry_list_cache["data"] = result
    _hk_industry_list_cache["expire_at"] = now + timedelta(hours=24)
    logger.info("[screener] 港股行业列表加载完成：%d 个", len(result))
    return result


def _get_json(url, params):
    """带重试的 GET 请求，返回解析后的 JSON"""
    last_err = None
    for attempt in range(1, _RETRY_TIMES + 1):
        try:
            resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = e
            logger.warning("[screener] 请求失败 第%d/%d次 err=%s", attempt, _RETRY_TIMES, e)
            if attempt < _RETRY_TIMES:
                time.sleep(_RETRY_DELAY)
    raise last_err


def _safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _to_cap_yi(v):
    """将市值（单位：元）转为亿元"""
    f = _safe_float(v)
    if f is None:
        return None
    return round(f / 1e8, 2)


# 行业列表缓存（TTL 24 小时）
_industry_list_cache: dict = {"expire_at": None, "data": None}


def fetch_industry_list() -> list:
    """
    获取 A股所有行业分类列表（BOARD_NAME），按名称排序，结果缓存 24 小时。
    逐页抓取直到连续两页无新增行业为止（通常 5 页内完成）。
    """
    now = datetime.now()
    if _industry_list_cache["expire_at"] and _industry_list_cache["expire_at"] > now:
        return _industry_list_cache["data"]

    industries: set = set()
    zero_new_count = 0
    for page in range(1, 15):
        params = {
            "pageSize": "500",
            "pageNumber": str(page),
            "reportName": "RPT_LICO_FN_CPD",
            "columns": "BOARD_NAME",
            "sortColumns": "REPORTDATE,SECURITY_CODE",
            "sortTypes": "-1,1",
        }
        try:
            data = _get_json(_A_SHARE_URL, params)
            items = (data.get("result") or {}).get("data") or []
        except Exception as e:
            logger.warning("[screener] 获取行业列表失败 page=%d: %s", page, e)
            break
        if not items:
            break
        prev_len = len(industries)
        for item in items:
            name = (item.get("BOARD_NAME") or "").strip()
            if name:
                industries.add(name)
        if len(industries) == prev_len:
            zero_new_count += 1
            if zero_new_count >= 2:
                break
        else:
            zero_new_count = 0

    result = sorted(industries)
    _industry_list_cache["data"] = result
    _industry_list_cache["expire_at"] = now + timedelta(hours=24)
    logger.info("[screener] 行业列表加载完成：%d 个", len(result))
    return result


def _fetch_industry_for_codes(codes: list) -> dict:
    """
    精确查询指定股票代码列表的行业分类，返回 {code -> BOARD_NAME} 映射。
    利用 datacenter-web RPT_LICO_FN_CPD 的 in 过滤，避免全量分页。
    """
    if not codes:
        return {}
    result: dict = {}
    batch_size = 100
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        code_list = ",".join(f'"{c}"' for c in batch)
        params = {
            "pageSize": str(batch_size * 3),
            "pageNumber": "1",
            "reportName": "RPT_LICO_FN_CPD",
            "columns": "SECURITY_CODE,BOARD_NAME",
            "sortColumns": "REPORTDATE,SECURITY_CODE",
            "sortTypes": "-1,1",
            "filter": f"(SECURITY_CODE in ({code_list}))",
        }
        try:
            data = _get_json(_A_SHARE_URL, params)
            items = (data.get("result") or {}).get("data") or []
            for item in items:
                code = item.get("SECURITY_CODE")
                if code and code not in result:
                    result[code] = item.get("BOARD_NAME") or ""
        except Exception as e:
            logger.warning("[screener] 行业查询批次 %d 失败：%s", i // batch_size + 1, e)
    return result


def _screen(records, pe_key, pb_key, code_key, name_key, cap_key, market_label, top=100, industry_map=None, price_key=None):
    """
    top=N: 返回 PE 前N + PB 前N（各剔除负值）
    top=0: 返回全部股票（按原始顺序，前端负责排序和筛选）
    每条记录均含 net_assets = market_cap / pb
    price_key 存在时额外带出 price（最新收盘价，A股用 CLOSE_PRICE）
    """
    def build_row(item, source, rank):
        pe = _safe_float(item.get(pe_key))
        pb = _safe_float(item.get(pb_key))
        cap = _to_cap_yi(item.get(cap_key))
        net_assets = round(cap / pb, 2) if (cap is not None and pb and pb > 0) else None
        code = str(item.get(code_key) or "").strip()
        price = _safe_float(item.get(price_key)) if price_key else None
        return {
            "rank": rank,
            "source": source,
            "market": market_label,
            "code": code,
            "name": str(item.get(name_key) or "").strip(),
            "pe": round(pe, 2) if pe is not None else None,
            "pb": round(pb, 2) if pb is not None else None,
            "market_cap": cap,
            "net_assets": net_assets,
            "price": round(price, 3) if price is not None else None,
            "industry": (industry_map or {}).get(code, ""),
        }

    if top == 0:
        return [build_row(r, "", rank) for rank, r in enumerate(records, 1)]

    result = []
    pe_valid = [(r, _safe_float(r.get(pe_key))) for r in records]
    pe_valid = sorted([(r, v) for r, v in pe_valid if v is not None and v > 0], key=lambda x: x[1])
    for rank, (r, _) in enumerate(pe_valid[:top], 1):
        result.append(build_row(r, "PE", rank))

    pb_valid = [(r, _safe_float(r.get(pb_key))) for r in records]
    pb_valid = sorted([(r, v) for r, v in pb_valid if v is not None and v > 0], key=lambda x: x[1])
    for rank, (r, _) in enumerate(pb_valid[:top], 1):
        result.append(build_row(r, "PB", rank))

    return result


def fetch_a_share_screener(top: int = 100):
    """
    A股粗选。top=N 返回 PE/PB 各前N，top=0 返回全市场所有股票。
    """
    all_items = []
    page = 1
    total = None
    while True:
        params = {
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
            "pageSize": str(_PAGE_SIZE),
            "pageNumber": str(page),
            "reportName": "RPT_DMSK_TS_STOCKNEW",
            "quoteColumns": (
                "f9~01~SECURITY_CODE~PE_DYNAMIC,"
                "f23~01~SECURITY_CODE~MARKET_NET_RATE,"
                "f20~01~SECURITY_CODE~TOTAL_MKT_CAP"
            ),
            "quoteType": "0",
            "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,CLOSE_PRICE",
        }
        data = _get_json(_A_SHARE_URL, params)
        result = data.get("result") or {}
        items = result.get("data") or []
        if not items:
            break
        all_items.extend(items)
        if total is None:
            total = result.get("count") or 0
        logger.info("[screener] A股 page %d: %d 条，累计 %d/%s", page, len(items), len(all_items), total or "?")
        if total and len(all_items) >= total:
            break
        if len(items) < _PAGE_SIZE:
            break
        page += 1

    if not all_items:
        raise ValueError("A股数据为空，datacenter-web.eastmoney.com 返回空结果")

    logger.info("[screener] A股共 %d 只，top=%d，开始筛选", len(all_items), top)
    screened = _screen(all_items, "PE_DYNAMIC", "MARKET_NET_RATE", "SECURITY_CODE", "SECURITY_NAME_ABBR",
                       "TOTAL_MKT_CAP", "A股", top=top, price_key="CLOSE_PRICE")

    # top=0 全量模式时股票太多，跳过行业查询（用户通过搜索框过滤）
    if top > 0:
        codes = list({r["code"] for r in screened})
        industry_map = _fetch_industry_for_codes(codes)
        for r in screened:
            r["industry"] = industry_map.get(r["code"], "")
    return screened


def fetch_hk_share_screener(top: int = 100):
    """
    港股粗选。top=N 返回 PE/PB 各前N，top=0 返回全市场所有港股。
    数据源：RPT_CUSTOM_HKF10_FN_MAININDICATORMAX（按代码排序，2804只，无需去重）
    股价 = TOTAL_MARKET_CAP ÷ ISSUED_COMMON_SHARES（港元）
    """
    all_items = []
    page = 1
    max_pages = 10

    while page <= max_pages:
        params = {
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
            "pageSize": str(_PAGE_SIZE),
            "pageNumber": str(page),
            "reportName": "RPT_CUSTOM_HKF10_FN_MAININDICATORMAX",
            "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,PE_TTM,PB_TTM,TOTAL_MARKET_CAP,ISSUED_COMMON_SHARES",
        }
        data = _get_json(_HK_SHARE_URL, params)
        items = (data.get("result") or {}).get("data") or []
        if not items:
            break
        all_items.extend(items)
        logger.info("[screener] 港股 page %d: %d 条，累计 %d", page, len(items), len(all_items))
        if len(items) < _PAGE_SIZE:
            break
        page += 1

    if not all_items:
        raise ValueError("港股数据为空，datacenter.eastmoney.com 返回空结果")

    # 注入 price = 总市值 ÷ 总股本（港元）
    for item in all_items:
        cap = _safe_float(item.get("TOTAL_MARKET_CAP"))
        shares = _safe_float(item.get("ISSUED_COMMON_SHARES"))
        if cap is not None and shares and shares > 0:
            item["price"] = round(cap / shares, 3)

    logger.info("[screener] 港股共 %d 只，top=%d，开始筛选", len(all_items), top)
    return _screen(all_items, "PE_TTM", "PB_TTM", "SECURITY_CODE", "SECURITY_NAME_ABBR",
                   "TOTAL_MARKET_CAP", "港股", top=top, price_key="price")


# ── 字段标签 ──────────────────────────────────────────────────────
_FIELD_LABELS = {
    "pe":         "PE",
    "pb":         "PB",
    "market_cap": "总市值(亿)",
    "net_assets": "净资产(亿)",
}


def _step_label(step: dict) -> str:
    t = step.get("type")
    if t == "range":
        f = _FIELD_LABELS.get(step.get("field"), step.get("field", "?"))
        return f"{f} {step.get('op','?')} {step.get('value','?')}"
    if t == "sort_limit":
        f = _FIELD_LABELS.get(step.get("field"), step.get("field", "?"))
        d = "↑" if step.get("dir", "asc") == "asc" else "↓"
        lim = step.get("limit")
        if lim:
            return f"{f}{d} 前{lim}只"
        return f"{f}{d} 排序"
    if t == "industry_cap":
        return f"行业上限 ≤{step.get('max','?')}只"
    if t == "industry_filter":
        inds = step.get("industries", [])
        if inds:
            return f"行业∈[{', '.join(inds[:3])}{'...' if len(inds)>3 else ''}]"
        return "行业筛选（未选）"
    if t == "dimension_union":
        dims = step.get("dimensions", [])
        parts = []
        for d in dims:
            f = _FIELD_LABELS.get(d.get("field"), d.get("field", "?"))
            arrow = "↑" if d.get("dir", "asc") == "asc" else "↓"
            parts.append(f"{f}{arrow}{d.get('limit','?')}")
        return "维度合并：" + " + ".join(parts) if parts else "维度合并"
    return str(step)


def execute_strategy(raw_stocks: list, steps: list, market: str = "a_share") -> dict:
    """
    通用策略执行器：按步骤顺序筛选股票。

    步骤类型：
    - range       : {'type':'range', 'field':'net_assets', 'op':'>=', 'value':100}
                    字段可选：pe / pb / market_cap / net_assets
                    运算符：>= / <= / > / < / =
    - sort_limit  : {'type':'sort_limit', 'field':'pb', 'dir':'asc', 'limit':100,
                     'skip_non_positive':True}
                    按字段排序后取前N只；skip_non_positive=True（默认）时剔除≤0的值
    - industry_cap: {'type':'industry_cap', 'max':5}
                    每个行业最多保留 max 只；行业为空的股票归入“其他”

    返回：
    {
      'stocks' : [...],   最终股票列表
      'stats'  : [        每步统计
        {'label':'全市场', 'count': 5191},
        {'label':'净资产 >= 100', 'count': 791},
        ...
      ]
    }
    """
    current = list(raw_stocks)
    stats = [{"label": "全市场", "count": len(current)}]

    _ops = {
        ">=": lambda x, v: x >= v,
        "<=": lambda x, v: x <= v,
        ">":  lambda x, v: x > v,
        "<":  lambda x, v: x < v,
        "=":  lambda x, v: abs(x - v) < 1e-9,
    }

    for step in steps:
        t = step.get("type")

        if t == "range":
            field = step.get("field")
            op_fn = _ops.get(step.get("op", ">="))
            value = _safe_float(step.get("value", 0)) or 0.0
            if field and op_fn:
                current = [
                    s for s in current
                    if s.get(field) is not None and op_fn(float(s[field]), value)
                ]

        elif t == "sort_limit":
            field   = step.get("field")
            is_desc = step.get("dir", "asc") == "desc"
            limit   = step.get("limit")         # None / 0 → 不限数量，只排序
            skip_np = step.get("skip_non_positive", True)
            if field:
                pool = [s for s in current if s.get(field) is not None]
                if skip_np:
                    pool = [s for s in pool if float(s[field]) > 0]
                pool = sorted(pool, key=lambda x: float(x[field]), reverse=is_desc)
                if limit:
                    pool = pool[:int(limit)]
                current = pool

        elif t == "industry_cap":
            max_ind = int(step.get("max", 5))
            total_limit = step.get("limit")
            ind_count: dict = {}
            result = []
            for stock in current:
                if total_limit and len(result) >= int(total_limit):
                    break
                ind = stock.get("industry") or "其他"
                if ind_count.get(ind, 0) < max_ind:
                    ind_count[ind] = ind_count.get(ind, 0) + 1
                    result.append(stock)
            current = result

        elif t == "industry_filter":
            selected = set(step.get("industries") or [])
            mode = step.get("mode", "include")   # include=仅包含, exclude=排除
            if selected:
                if mode == "exclude":
                    current = [s for s in current
                               if (s.get("industry") or "其他") not in selected]
                else:
                    current = [s for s in current
                               if (s.get("industry") or "其他") in selected]

        elif t == "dimension_union":
            # 并集操作：对每个维度分别排序取前N，合并结果（保留来源标签，允许同一股票多次出现）
            dimensions = step.get("dimensions", [])
            union_result = []
            for dim in dimensions:
                field   = dim.get("field")
                is_desc = dim.get("dir", "asc") == "desc"
                limit   = dim.get("limit")
                skip_np = dim.get("skip_non_positive", True)
                if not field:
                    continue
                pool = [s for s in current if s.get(field) is not None]
                if skip_np:
                    pool = [s for s in pool if float(s[field]) > 0]
                pool = sorted(pool, key=lambda x: float(x[field]), reverse=is_desc)
                if limit:
                    pool = pool[:int(limit)]
                source_label = _FIELD_LABELS.get(field, field).upper()
                for rank, stock in enumerate(pool, 1):
                    union_result.append({**stock, "source": source_label, "rank": rank})
            current = union_result

        stats.append({"label": _step_label(step), "count": len(current)})

    return {"stocks": current, "stats": stats}


def update_market_data(market: str) -> dict:
    """
    拉取全量股票数据（行情 + 行业）并写入数据库。
    支持 market='a_share' | 'hk_share' | 'both'。
    约需：A股 30-60 秒，港股 15-30 秒。
    """
    from bond.screener_db import get_all_stocks, save_stocks, set_updating

    targets = ["a_share", "hk_share"] if market == "both" else [market]
    result = {}

    for mkt in targets:
        set_updating(mkt, True)
        try:
            logger.info("[screener] 开始更新 %s 行情数据...", mkt)
            existing_industries = {
                row["code"]: row.get("industry", "")
                for row in get_all_stocks(mkt)
                if row.get("industry")
            }

            # ── 1. 拉取行情（PE/PB/市值/净资产，无行业）─────────────────────
            if mkt == "a_share":
                stocks = fetch_a_share_screener(top=0)
            else:
                stocks = fetch_hk_share_screener(top=0)

            logger.info("[screener] %s 行情获取完成：%d 只，开始批量查询行业...", mkt, len(stocks))

            # ── 2. 批量查询所有股票的行业（100 只/批）────────────────────────
            ind_fetch = _fetch_hk_industry_for_codes if mkt == "hk_share" else _fetch_industry_for_codes
            batch_size = 100
            codes = [s["code"] for s in stocks]
            industry_map: dict = {}
            for i in range(0, len(codes), batch_size):
                batch_map = ind_fetch(codes[i:i + batch_size])
                industry_map.update(batch_map)
                if i % 1000 == 0:
                    logger.info("[screener] %s 行业进度 %d/%d", mkt, i, len(codes))

            # ── 3. 填充行业到每只股票───────────────────────────────────────
            for s in stocks:
                # 行业接口短暂失败时保留上一版行业，避免一次更新清空全部分类。
                s["industry"] = industry_map.get(s["code"]) or existing_industries.get(s["code"], "")

            # ── 4. 写入 DB───────────────────────────────────────────────────
            save_stocks(mkt, stocks)

            from datetime import datetime
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            industry_count = sum(1 for s in stocks if s.get("industry"))
            result[mkt] = {
                "count": len(stocks),
                "industry_count": industry_count,
                "update_time": now_str,
            }
            logger.info("[screener] %s 更新完成：%d 只（含行业 %d 只）",
                        mkt, len(stocks), industry_count)

        except Exception as e:
            logger.error("[screener] %s 更新失败：%s", mkt, e)
            result[mkt] = {"error": str(e)}
        finally:
            set_updating(mkt, False)

    return result
