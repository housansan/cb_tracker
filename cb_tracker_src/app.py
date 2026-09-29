from __future__ import annotations

from flask import Flask, render_template, request, jsonify
from bond import get_convertible_bond_history, get_all_convertible_bonds, get_bond_info, fetch_bond_detail_only, get_bond_adj_logs, get_all_lof_funds
from bond.db import update_bond_region, query_positions, upsert_position, delete_position, query_alerts, add_alert, delete_alert, query_note, upsert_note, delete_note
from bond.cache import read_local_cache
from bond.fetch import fetch_all_cb_remaining
from bond.history import build_cashflows_from_coupon_info
from bond.calc import calc_ytm
from bond.db import _parse_redeem_price
from config import LOG_CONFIG, EXPORT_CONFIG, DB_CONFIG, USER_DB_CONFIG, BOND_CONFIG, NETWORK_CONFIG
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
import akshare as ak
import json
import logging
import math
import os
import threading
import time

# ── 网络代理配置（读取 conf/config.toml [network] proxy）──────────────────
# proxy = ""        → 直连，清除所有代理环境变量，并 patch requests.Session
#                     使 trust_env=False（避免读取 macOS 系统代理 / ClashX / Surge）
# proxy = "system"  → 保留系统代理环境变量，不做任何修改
# proxy = "http://…" → 强制指定代理地址
import requests as _requests

_proxy_cfg = NETWORK_CONFIG.get("proxy", "")

if _proxy_cfg == "system":
    # 显式声明使用系统代理，不做任何修改
    pass
elif _proxy_cfg:
    # 指定代理地址：写入环境变量，让 requests 自动读取
    os.environ["HTTP_PROXY"]  = _proxy_cfg
    os.environ["HTTPS_PROXY"] = _proxy_cfg
else:
    # 默认直连模式：清除代理环境变量，并禁用 macOS 系统代理读取
    for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                       "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        os.environ.pop(_proxy_var, None)
    os.environ["NO_PROXY"] = "*"   # requests 读到此变量后对所有主机跳过代理

    # requests 在 macOS 上还会读取系统网络偏好设置（System Proxy）。
    # akshare 每次请求都创建新 Session，所以必须 patch __init__，而非类属性。
    _orig_session_init = _requests.Session.__init__
    def _no_proxy_session_init(self, *args, **kwargs):
        _orig_session_init(self, *args, **kwargs)
        self.trust_env = False  # 禁用代理：不读 env 变量，也不读 macOS 系统代理
    _requests.Session.__init__ = _no_proxy_session_init

# ── Convertible Bond Tracker 应用配置 ──────────────────────────────────────
os.makedirs(LOG_CONFIG["dir"], exist_ok=True)

_log_formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 按天滚动，文件名后缀为日期（app.log.2026-03-11）
_file_handler = TimedRotatingFileHandler(
    filename=os.path.join(LOG_CONFIG["dir"], "app.log"),
    when="midnight",
    interval=1,
    backupCount=LOG_CONFIG["backup_count"],
    encoding="utf-8",
)
_file_handler.suffix = "%Y-%m-%d"
_file_handler.setFormatter(_log_formatter)

_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_log_formatter)

logging.basicConfig(
    level=getattr(logging, LOG_CONFIG["level"], logging.INFO),
    handlers=[_stream_handler, _file_handler],
)

# ── 导出目录 ────────────────────────────────────────────────────────────────
os.makedirs(EXPORT_CONFIG["dir"], exist_ok=True)

# ── 数据库初始化 ─────────────────────────────────────────────────────────────
from bond.db import (
    init_db, init_user_db, upsert_bond, upsert_bonds,
    query_bond, query_bonds, count_bonds,
    bond_info_to_db_row, bond_list_row_to_db_row,
    db_row_to_bond_info, query_bond_updated_at, query_latest_updated_at,
    decode_credit_rating, query_latest_daily_snapshot, query_latest_stock_snapshot,
    upsert_daily_batch, upsert_stock_daily_batch,
    upsert_stock_financials, query_stock_financials,
)
init_db(DB_CONFIG["dir"])
init_user_db(USER_DB_CONFIG["dir"])

# LOF 赎回费数据库（独立 lof.db，与转债库分离）
from bond.lof_db import init_lof_db, query_all_lof_fees, query_existing_lof_codes, upsert_lof_fee, parse_fee_tiers
init_lof_db(DB_CONFIG["dir"])

# bond_info 缓存有效期（秒）：24 小时
_BOND_INFO_DB_TTL = 86400
# bond_list DB 缓存有效期（秒）：7 天（基础信息变化慢，价格走独立实时接口）
_BOND_LIST_DB_TTL = 86400 * 7

logger = logging.getLogger("app")

app = Flask(__name__)

# ── 后台补全详细字段 ──────────────────────────────────────────────────────────

def _fill_bond_details_async(bond_codes: list) -> None:
    """
    后台线程：逐只调用 fetch_bond_detail_only() 补全 DB 中缺失的详细字段
    （listing_date / delist_date / value_date / expire_date /
      redeem_price / coupon_rate_desc / coupon_rates / coupon_pay_dates）
    注意：使用 fetch_bond_detail_only 而非 get_bond_info，避免每次补全都拉取全量列表
    """
    def _run():
        filled = 0
        for code in bond_codes:
            try:
                logger.info("[fill_details] 开始补全 bond_code=%s", code)
                info = fetch_bond_detail_only(str(code))
                if not info:
                    logger.warning("[fill_details] fetch_bond_detail_only 返回空 bond_code=%s", code)
                    continue
                bond_name = info.get("债券简称", "")
                stock_name = info.get("正股简称", "")
                listing_date = info.get("上市日期", "")
                logger.info("[fill_details] 接口返回 bond_code=%s bond_name=%r stock_name=%r listing_date=%r",
                            code, bond_name, stock_name, listing_date)
                db_row = bond_info_to_db_row(info)
                if db_row:
                    logger.info("[fill_details] 写入DB bond_code=%s bond_name=%r stock_name=%r listing_date=%r",
                                code, db_row.get("bond_name"), db_row.get("stock_name"), db_row.get("listing_date"))
                    upsert_bond(db_row)
                    filled += 1
                else:
                    logger.warning("[fill_details] bond_info_to_db_row 返回空 bond_code=%s", code)
                # 顺带获取转股价调整记录并写入缓存（不阻塞）
                try:
                    adj_logs = get_bond_adj_logs(str(code))
                    logger.info("[fill_details] 转股价调整记录 bond_code=%s count=%d", code, len(adj_logs))
                except Exception as _adj_e:
                    logger.warning("[fill_details] 获取转股价调整记录失败 bond_code=%s err=%s", code, _adj_e)
            except Exception as _e:
                logger.warning("[fill_details] 补全失败 bond_code=%s err=%s", code, _e)
            finally:
                time.sleep(BOND_CONFIG["fill_details_sleep"])  # 避免频繁请求接口
        logger.info("[fill_details] 后台补全完成，共补全 %d / %d 只", filled, len(bond_codes))
        # 补全完成后清除内存缓存，让下次请求重新从 DB 读取最新名称等字段
        if filled > 0:
            _bond_list_cache["data"] = None
            _bond_list_cache["expire_at"] = None
            logger.info("[fill_details] 已清除列表内存缓存，下次请求将从 DB 重新加载")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    logger.info("[fill_details] 已启动后台补全线程，待补全 %d 只", len(bond_codes))

# 全市场可转债列表内存缓存
_bond_list_cache = {
    "data": None,       # 缓存的 records 列表
    "expire_at": None,  # 过期时间 datetime
}
_BOND_LIST_CACHE_TTL = 3600  # 缓存有效期（秒），1 小时（与价格缓存对齐）


def _to_daily_row(row: dict, trade_date: str) -> dict | None:
    """将 get_all_convertible_bonds() 的一行转换为 t_bond_daily 所需的 dict"""
    code = str(row.get("债券代码", "")).strip()
    if not code:
        return None

    def _int100(val):
        try:
            v = float(val)
            if math.isnan(v) or math.isinf(v):
                return None
            return int(round(v * 100))
        except (TypeError, ValueError):
            return None

    def _int10000(val):
        try:
            v = float(val)
            if math.isnan(v) or math.isinf(v):
                return None
            return int(round(v * 10000))
        except (TypeError, ValueError):
            return None

    def _decimal(val):
        try:
            v = float(val)
            if math.isnan(v) or math.isinf(v):
                return None
            return round(v, 4)
        except (TypeError, ValueError):
            return None

    close_raw = _int100(row.get("债现价"))
    premium_raw = _int10000(row.get("转股溢价率"))
    double_low = None
    if close_raw is not None and premium_raw is not None:
        double_low = close_raw + int(round(premium_raw / 100))

    return {
        "bond_code": int(code),
        "trade_date": trade_date,
        "close": close_raw,
        "conv_premium_rate": premium_raw,
        "conv_value": _int100(row.get("转股价值")),
        "ytm": _int10000(row.get("到期收益率")),
        "issue_size": _int100(row.get("剩余规模")),
        "double_low": double_low,
    }


def _to_stock_daily_row(row: dict, trade_date: str, stock_code: int) -> dict | None:
    """将 get_all_convertible_bonds() 的一行转换为 t_stock_daily 所需的 dict"""
    if not stock_code:
        return None

    def _int100(val):
        try:
            v = float(val)
            if math.isnan(v) or math.isinf(v):
                return None
            return int(round(v * 100))
        except (TypeError, ValueError):
            return None

    def _decimal(val):
        try:
            v = float(val)
            if math.isnan(v) or math.isinf(v):
                return None
            return round(v, 4)
        except (TypeError, ValueError):
            return None

    return {
        "stock_code": stock_code,
        "trade_date": trade_date,
        "stock_close": _int100(row.get("正股价")),
        "stock_pb": _decimal(row.get("正股PB")),
        "stock_market_cap": _decimal(row.get("正股市值")),
    }

# 实时价格缓存（债现价、正股价、转股溢价率），TTL 较短
_price_cache = {
    "data": None,       # dict: bond_code -> {"债现价": x, "正股价": x, "转股溢价率": x}
    "expire_at": None,
}
_PRICE_CACHE_TTL = 3600  # 1 小时


def _fetch_spot_prices(codes: set) -> dict:
    """
    用新浪实时快照 bond_zh_hs_cov_spot() 为指定债补价（单次全市场请求，无封 IP 风险）。
    对停牌债 trade(现价)为 0，回退用 settlement(前结算/昨收)。

    :param codes: 待补价的纯数字代码集合
    :return: {code: price}，仅含成功补到正价的债；接口失败返回空 dict
    """
    if not codes:
        return {}
    try:
        spot_df = ak.bond_zh_hs_cov_spot()
        if spot_df.empty:
            return {}
        # symbol 形如 'sz123092'，去掉 2 位交易所前缀
        spot_df = spot_df.copy()
        spot_df["_code"] = spot_df["symbol"].astype(str).str[2:]
        result = {}
        for _, r in spot_df[spot_df["_code"].isin(codes)].iterrows():
            try:
                trade = float(r.get("trade") or 0)
                settle = float(r.get("settlement") or 0)
                px = trade if trade > 0 else settle
                if px and px > 0:
                    result[r["_code"]] = round(px, 4)
            except (TypeError, ValueError):
                continue
        return result
    except Exception as _e:
        logger.warning("[spot_prices] 兜底实时快照拉取失败 err=%s", _e)
        return {}


def _get_price_map() -> dict:
    """获取全市场实时价格 map，优先读缓存（1小时内有效）"""
    now = datetime.now()
    if _price_cache["data"] is not None and _price_cache["expire_at"] > now:
        return _price_cache["data"]
    try:
        df = get_all_convertible_bonds()
        if df.empty:
            return {}
        def _clean(v):
            """将 NaN / Inf 转为 None，避免 JSON 序列化失败"""
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return None
            return v

        price_map = {}
        suspect_codes = []  # 债现价被 fillna(100) 占位、需兜底补价的债
        for _, row in df.iterrows():
            code = str(row.get("债券代码", "")).strip()
            if code:
                bond_price = _clean(row.get("债现价"))
                stock_price = _clean(row.get("正股价"))
                premium = _clean(row.get("转股溢价率"))
                # akshare bond_zh_cov() 对无实时行情的债现价会 fillna(100)，
                # 表现为「债现价==100 且 正股价缺失」。此为占位假值（非真实市价），
                # 先置 None，再用新浪快照兜底补真实价（仍补不到则显示 '-'）。
                if bond_price == 100.0 and stock_price is None:
                    bond_price = None
                    suspect_codes.append(code)
                price_map[code] = {
                    "债现价":     bond_price,
                    "正股价":     stock_price,
                    "转股溢价率": premium,
                }

        # 对占位债用新浪实时快照兜底补价（单次全市场请求）
        filled_cnt = 0
        if suspect_codes:
            spot_prices = _fetch_spot_prices(set(suspect_codes))
            for code, px in spot_prices.items():
                if code in price_map:
                    price_map[code]["债现价"] = px
                    filled_cnt += 1

        _price_cache["data"] = price_map
        _price_cache["expire_at"] = now + timedelta(seconds=_PRICE_CACHE_TTL)
        logger.info("[price_map] 实时价格缓存已更新，共 %d 只（%d 只无行情占位，其中 %d 只快照兜底补价成功）",
                    len(price_map), len(suspect_codes), filled_cnt)
        return price_map
    except Exception as _e:
        logger.warning("[price_map] 获取实时价格失败 err=%s", _e)
        return _price_cache["data"] or {}


def _attach_cashflows(info: dict) -> None:
    """
    为未退市债券附加现金流数据，供前端实时计算目标买入价。
    已退市债券注入空列表，不做计算。
    """
    if info.get("退市日期"):
        info["cashflows"] = []
        info["times"] = []
        return
    try:
        coupon_info = info.get("付息信息") or {}
        cashflows, times = build_cashflows_from_coupon_info(coupon_info)
        info["cashflows"] = cashflows
        info["times"] = times
    except Exception as _e:
        logger.warning("[bond_info] 现金流构建失败 err=%s", _e)
        info["cashflows"] = []
        info["times"] = []


def _calc_pretax_ytm(bond_price, coupon_pay_dates: list, coupon_rates: list,
                     redeem_clause: str = "", rate_desc: str = "") -> float | None:
    """
    计算到期税前收益率（YTM，年化 %）。

    使用全额票息和含税赎回价（不扣 20% 利息税），因此结果为「税前」口径。
    复用 build_cashflows_from_coupon_info + calc_ytm，与详情页保持一致。

    :param bond_price:       债现价（元）
    :param coupon_pay_dates: 付息日列表 list[str] "YYYYMMDD"
    :param coupon_rates:     各年票息率 list[float]（%）
    :param redeem_clause:    赎回条款原文（用于解析到期赎回价）
    :param rate_desc:        利率说明原文（赎回价兜底来源）
    :return: YTM（%），无法计算时返回 None
    """
    try:
        if bond_price is None or not coupon_pay_dates or not coupon_rates:
            return None
        price = float(bond_price)
        if price <= 0 or math.isnan(price) or math.isinf(price):
            return None
        redeem_price = _parse_redeem_price(redeem_clause or "", rate_desc or "", coupon_rates)
        coupon_info = {
            "付息日列表": coupon_pay_dates,
            "票息率列表": coupon_rates,
            "赎回价":     redeem_price,
        }
        cashflows, times = build_cashflows_from_coupon_info(coupon_info)
        if not cashflows:
            return None
        ytm = calc_ytm(price, cashflows, times)
        if ytm is None or (isinstance(ytm, float) and (math.isnan(ytm) or math.isinf(ytm))):
            return None
        return round(ytm, 4)
    except Exception as _e:
        logger.warning("[_calc_pretax_ytm] 计算失败 err=%s", _e)
        return None


@app.route("/")
def index():
    return render_template("index.html")


# ── LOF 基金列表 ────────────────────────────────────────────────────────────────
_lof_list_cache = {
    "data": None,
    "expire_at": None,
}
_LOF_LIST_CACHE_TTL = 3600  # 1 小时（与价格缓存对齐）

# 赎回费后台抓取线程状态（避免重复启动）
_lof_fee_fetch_state = {"running": False, "done": False}


def _fetch_lof_fees_async(codes: list) -> None:
    """
    后台线程：逐只拉取 LOF 赎回费率（ak.fund_fee_em）并入库。
    赎回费是固定数据，只需抓一次；已入库的代码自动跳过（断点续跑）。
    限速 sleep 防止被限流。
    """
    if _lof_fee_fetch_state["running"]:
        logger.info("[lof_fee] 抓取线程已在运行，跳过")
        return
    _lof_fee_fetch_state["running"] = True

    def _run():
        try:
            existing = query_existing_lof_codes()
            todo = [c for c in codes if c not in existing]
            logger.info("[lof_fee] 开始抓取赎回费，待抓 %d 只（已入库 %d 只）", len(todo), len(existing))
            filled = 0
            for code in todo:
                try:
                    df = ak.fund_fee_em(symbol=code, indicator="赎回费率")
                    tiers = parse_fee_tiers(df)
                    if tiers:
                        upsert_lof_fee(code, "", tiers)
                        filled += 1
                except Exception as _e:
                    logger.warning("[lof_fee] 抓取失败 code=%s err=%s", code, _e)
                finally:
                    time.sleep(BOND_CONFIG.get("fill_details_sleep", 0.5))
            logger.info("[lof_fee] 赎回费抓取完成，本轮新增 %d 只", filled)
            _lof_fee_fetch_state["done"] = True
        finally:
            _lof_fee_fetch_state["running"] = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    logger.info("[lof_fee] 已启动赎回费后台抓取线程")


def _format_fee_tiers(tiers: list) -> str:
    """
    将结构化赎回费分档转为可读文本。
      [{min_day:0,max_day:7,fee:1.5},{min_day:7,max_day:30,fee:0.5},{min_day:30,max_day:None,fee:0}]
      → "<7天:1.5% | 7-30天:0.5% | ≥30天:0%"
    """
    def _fee_str(f):
        # 去掉多余小数末尾 0：1.50→1.5，0.00→0
        s = ("%.2f" % f).rstrip("0").rstrip(".")
        return f"{s}%"

    parts = []
    for t in tiers:
        lo, hi, fee = t.get("min_day"), t.get("max_day"), t.get("fee")
        if fee is None:
            continue
        # 无法量化为天数的档位（如“持有满一个封闭期”）直接用原始期限文本
        if lo is None and hi is None:
            label = t.get("term") or "?"
        elif (lo == 0 or lo is None) and hi:
            label = f"<{hi}天"
        elif hi is None:
            label = f"≥{lo}天"
        else:
            label = f"{lo}-{hi}天"
        parts.append(f"{label}:{_fee_str(fee)}")
    return " | ".join(parts)


@app.route("/api/lof_list")
def api_lof_list():
    """
    获取全市场 LOF 基金列表（含溢价率/折价率、成交额、申赎状态、赎回费）。
    策略：内存缓存 1 小时 → 实时拉取行情 + JOIN 本地赎回费库。
    赎回费缺失时后台线程自动抓取入库（首次不全，后续刷新即补上）。
    """
    now = datetime.now()
    if _lof_list_cache["data"] is not None and _lof_list_cache["expire_at"] > now:
        logger.info("[/api/lof_list] 命中内存缓存，返回 %d 只 LOF", len(_lof_list_cache["data"]))
        return jsonify({"success": True, "data": _lof_list_cache["data"], "from_cache": True})

    logger.info("[/api/lof_list] 缓存未命中，实时拉取 LOF 数据")
    records = get_all_lof_funds()
    if not records:
        logger.error("[/api/lof_list] 获取 LOF 数据失败")
        return jsonify({"success": False, "message": "获取 LOF 数据失败"}), 500

    # ── JOIN 本地赎回费数据 ────────────────────────────────────────────────────
    try:
        fee_map = query_all_lof_fees()
    except Exception as _e:
        logger.warning("[/api/lof_list] 读取赎回费库失败 err=%s", _e)
        fee_map = {}

    missing_fee_codes = []
    for row in records:
        code = row.get("代码", "")
        fee = fee_map.get(code)
        if fee:
            row["免赎费天数"] = fee.get("free_days")
            row["赎回费短线"] = fee.get("short_fee")
            tiers = fee.get("fee_tiers") or []
            row["赎回费分档"] = tiers                       # 结构化分档，供前端展开
            row["赎回费详情"] = _format_fee_tiers(tiers)     # 可读文本，如 "<7天:1.5% | 7-30天:0.5% | ≥30天:0%"
            # 净折价 = |折价率| − 短线赎回费（折价套利真实空间；折价率为负值）
            pr = row.get("溢价率")
            short_fee = fee.get("short_fee")
            if pr is not None and pr < 0 and short_fee is not None:
                row["净折价"] = round(abs(pr) - short_fee, 3)
            else:
                row["净折价"] = None
        else:
            row["免赎费天数"] = None
            row["赎回费短线"] = None
            row["赎回费分档"] = []
            row["赎回费详情"] = ""
            row["净折价"] = None
            if row.get("价格来源") != "无行情":
                missing_fee_codes.append(code)

    # 赎回费缺失的，后台异步抓取入库
    if missing_fee_codes:
        _fetch_lof_fees_async(missing_fee_codes)

    # 清理 NaN / Inf，避免 JSON 序列化失败
    for row in records:
        for k, v in row.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                row[k] = None

    _lof_list_cache["data"] = records
    _lof_list_cache["expire_at"] = now + timedelta(seconds=_LOF_LIST_CACHE_TTL)
    logger.info("[/api/lof_list] 返回 %d 只 LOF（%d 只待补赎回费），已写入内存缓存",
                len(records), len(missing_fee_codes))
    return jsonify({
        "success": True,
        "data": records,
        "pending_fee": len(missing_fee_codes) > 0,  # 前端据此决定是否延迟刷新
    })


@app.route("/api/history")
def api_history():
    """
    查询可转债历史成交数据
    参数：
      bond_code  - 可转债代码，如 113050
      start_date - 开始日期 YYYYMMDD
      end_date   - 结束日期 YYYYMMDD
    """
    bond_code = request.args.get("bond_code", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    if not bond_code:
        logger.warning("[/api/history] 请求缺少 bond_code 参数")
        return jsonify({"success": False, "message": "请输入可转债代码"}), 400

    logger.info("[/api/history] 请求历史数据 bond_code=%s start=%s end=%s", bond_code, start_date or "(auto)", end_date or "(auto)")

    # 若未传入日期，则用上市时间和退市时间（或当前时间）作为默认值
    if not start_date or not end_date:
        info = get_bond_info(bond_code)
        if not end_date:
            end_date = info.get("退市日期") or datetime.today().strftime("%Y%m%d")
        if not start_date:
            start_date = info.get("上市日期") or (datetime.today() - timedelta(days=365)).strftime("%Y%m%d")
        logger.info("[/api/history] 自动补全日期范围 start=%s end=%s", start_date, end_date)

    df = get_convertible_bond_history(bond_code, start_date, end_date)

    if df.empty:
        logger.warning("[/api/history] 未获取到数据 bond_code=%s start=%s end=%s", bond_code, start_date, end_date)
        return jsonify({"success": False, "message": "未获取到数据，请检查代码或日期范围"}), 404

    records = df.to_dict(orient="records")
    # 清理 NaN / Inf，避免 JSON 序列化失败
    for row in records:
        for k, v in row.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                row[k] = None
    logger.info("[/api/history] 返回 %d 条记录 bond_code=%s", len(records), bond_code)
    return jsonify({"success": True, "data": records, "total": len(records)})


@app.route("/api/bond_info")
def api_bond_info():
    """
    查询单只可转债基础信息
    参数：bond_code - 可转债代码，如 113050
    策略：先查 DB，updated_at 在 24 小时内则直接返回；否则请求接口并 upsert DB
    """
    bond_code = request.args.get("bond_code", "").strip()
    if not bond_code:
        logger.warning("[/api/bond_info] 请求缺少 bond_code 参数")
        return jsonify({"success": False, "message": "请输入可转债代码"}), 400

    # ── 尝试从 DB 读取 ────────────────────────────────────────────────────────
    try:
        bc_int = int(bond_code)
        updated_at_str = query_bond_updated_at(bc_int)
        if updated_at_str:
            age = (datetime.now() - datetime.strptime(updated_at_str, "%Y-%m-%d %H:%M:%S")).total_seconds()
            if age < _BOND_INFO_DB_TTL:
                db_row = query_bond(bc_int)
                if db_row:
                    info = db_row_to_bond_info(db_row)
                    # 实时补充价格字段（债现价、正股价、转股溢价率）
                    try:
                        price_map = _get_price_map()
                        prices = price_map.get(bond_code.strip(), {})
                        info["债现价"]     = prices.get("债现价")
                        info["正股价"]     = prices.get("正股价")
                        info["转股溢价率"] = prices.get("转股溢价率")
                        # 计算转股价值：正股价 / 转股价 * 100
                        try:
                            conv_price = info.get("转股价")
                            stock_price = info.get("正股价")
                            if conv_price and stock_price and conv_price > 0:
                                info["转股价值"] = round(float(stock_price) / float(conv_price) * 100, 4)
                        except Exception:
                            pass
                    except Exception as _pe:
                        logger.warning("[/api/bond_info] 补充实时价格失败 bond_code=%s err=%s", bond_code, _pe)
                    # 实时补充剩余规模（从东方财富全量缓存读，不写 DB）
                    try:
                        all_remaining = fetch_all_cb_remaining()
                        info["剩余规模"] = all_remaining.get(bond_code.strip())
                    except Exception as _re:
                        logger.warning("[/api/bond_info] 补充剩余规模失败 bond_code=%s err=%s", bond_code, _re)
                    # 检查是否有转股价下调记录（使用统一接口，自带内存缓存）
                    try:
                        adj_logs = get_bond_adj_logs(bond_code)
                        info["has_adj_logs"] = len(adj_logs) > 0
                    except Exception:
                        info["has_adj_logs"] = False
                    logger.info("[/api/bond_info] DB 缓存命中 bond_code=%s age=%.0fs has_adj=%s", bond_code, age, info["has_adj_logs"])
                    _attach_cashflows(info)
                    return jsonify({"success": True, "data": info, "from_cache": True})
    except Exception as _e:
        logger.warning("[/api/bond_info] DB 读取失败，降级请求接口 bond_code=%s err=%s", bond_code, _e)

    # ── 请求接口（单只查询，避免拉取全量列表）───────────────────────────────────
    logger.info("[/api/bond_info] 请求基础信息 bond_code=%s", bond_code)
    info = fetch_bond_detail_only(bond_code)
    if not info:
        logger.warning("[/api/bond_info] 未找到可转债 bond_code=%s", bond_code)
        return jsonify({"success": False, "message": f"未找到代码为 {bond_code} 的可转债"}), 404

    # 补充实时价格字段（债现价、正股价、转股溢价率）
    try:
        price_map = _get_price_map()
        prices = price_map.get(bond_code.strip(), {})
        info["债现价"]     = prices.get("债现价")
        info["正股价"]     = prices.get("正股价")
        info["转股溢价率"] = prices.get("转股溢价率")
        # 计算转股价值：正股价 / 转股价 * 100
        try:
            conv_price = info.get("转股价")
            stock_price = info.get("正股价")
            if conv_price and stock_price and conv_price > 0:
                info["转股价值"] = round(float(stock_price) / float(conv_price) * 100, 4)
        except Exception:
            pass
    except Exception as _pe:
        logger.warning("[/api/bond_info] 补充实时价格失败 bond_code=%s err=%s", bond_code, _pe)

    # 实时补充剩余规模（从东方财富全量缓存读，不写 DB）
    try:
        all_remaining = fetch_all_cb_remaining()
        info["剩余规模"] = all_remaining.get(bond_code.strip())
    except Exception as _re:
        logger.warning("[/api/bond_info] 补充剩余规模失败 bond_code=%s err=%s", bond_code, _re)

    # 获取转股价调整记录
    try:
        adj_logs = get_bond_adj_logs(bond_code)
        info["has_adj_logs"] = len(adj_logs) > 0
    except Exception:
        info["has_adj_logs"] = False

    # ── 写入 DB ───────────────────────────────────────────────────────────────
    try:
        db_row = bond_info_to_db_row(info)
        if db_row:
            upsert_bond(db_row)
            logger.info("[/api/bond_info] 已写入 DB bond_code=%s", bond_code)
    except Exception as _e:
        logger.warning("[/api/bond_info] 写入 DB 失败（不影响返回）bond_code=%s err=%s", bond_code, _e)

    logger.info("[/api/bond_info] 返回基础信息 bond_code=%s name=%s has_adj=%s", bond_code, info.get("债券简称", ""), info.get("has_adj_logs"))
    _attach_cashflows(info)
    return jsonify({"success": True, "data": info})


@app.route("/api/bond_adj_logs")
def api_bond_adj_logs():
    """
    查询可转债转股价格调整记录（集思录）
    参数：bond_code - 可转债代码，如 127099
    """
    bond_code = request.args.get("bond_code", "").strip()
    if not bond_code:
        logger.warning("[/api/bond_adj_logs] 请求缺少 bond_code 参数")
        return jsonify({"success": False, "message": "请输入可转债代码"}), 400

    logger.info("[/api/bond_adj_logs] 请求转股价调整记录 bond_code=%s", bond_code)
    records = get_bond_adj_logs(bond_code)
    logger.info("[/api/bond_adj_logs] 返回 %d 条记录 bond_code=%s", len(records), bond_code)
    return jsonify({"success": True, "data": records, "total": len(records)})


@app.route("/api/bond_list")
def api_bond_list():
    """
    获取全市场可转债列表
    策略：内存缓存（1小时）→ DB 缓存（7天）→ 请求接口并批量写入 DB
    """
    now = datetime.now()
    # ── 命中内存缓存 ──────────────────────────────────────────────────────────
    if _bond_list_cache["data"] is not None and _bond_list_cache["expire_at"] > now:
        logger.info("[/api/bond_list] 命中内存缓存，返回 %d 只可转债", len(_bond_list_cache["data"]))
        return jsonify({"success": True, "data": _bond_list_cache["data"], "from_cache": True})

    # ── 尝试从 DB 读取 ────────────────────────────────────────────────────────
    try:
        latest_updated = query_latest_updated_at()
        if latest_updated:
            age = (now - datetime.strptime(latest_updated, "%Y-%m-%d %H:%M:%S")).total_seconds()
            if age < _BOND_LIST_DB_TTL:
                # 不过滤 listing，让前端根据退市日期自行过滤
                db_rows = query_bonds(limit=2000)
                if db_rows:
                    def _ts_to_date(val):
                        """将 'YYYY-MM-DD HH:MM:SS' 转为 'YYYYMMDD'，空则返回空字符串"""
                        if not val:
                            return ""
                        try:
                            return val[:10].replace("-", "")
                        except Exception:
                            return ""

                    # 获取实时价格（债现价、正股价、转股溢价率）
                    price_map = _get_price_map()
                    # 从 t_bond_daily / t_stock_daily 获取最新高频变动数据
                    daily_snapshot = query_latest_daily_snapshot()
                    stock_snapshot = query_latest_stock_snapshot()
                    # 加载持仓数据
                    try:
                        positions_map = {str(p["bond_code"]): p for p in query_positions()}
                    except Exception:
                        positions_map = {}

                    records = []
                    missing_detail_codes = []  # listing_date 为空的，需后台补全
                    for r in db_rows:
                        code = str(r["bond_code"])
                        prices = price_map.get(code, {})
                        daily = daily_snapshot.get(r["bond_code"], {})
                        stock = stock_snapshot.get(r["stock_code"], {}) if r.get("stock_code") else {}
                        # 计算转股价值：正股价 / 转股价 * 100
                        stock_price = prices.get("正股价")
                        conv_price = round(r["conv_price"] / 100, 4) if r["conv_price"] else None
                        convert_value = None
                        if stock_price and conv_price and conv_price > 0:
                            convert_value = round(float(stock_price) / conv_price * 100, 4)
                        # 检查是否有转股价下调记录（本地缓存）
                        adj_cached = read_local_cache(f"adj_logs:{code}")
                        has_adj_logs = adj_cached is not None and len(adj_cached) > 0
                        # 付息日列表
                        pay_dates = []
                        if r.get("coupon_pay_dates"):
                            try:
                                pay_dates = json.loads(r["coupon_pay_dates"])
                            except Exception:
                                pass
                        # 票息率列表
                        coupon_rates = []
                        if r.get("coupon_rates"):
                            try:
                                coupon_rates = json.loads(r["coupon_rates"])
                            except Exception:
                                pass
                        # 到期税前收益率（YTM，%）：用债现价 + 付息结构实时计算
                        ytm = _calc_pretax_ytm(
                            prices.get("债现价"),
                            pay_dates,
                            coupon_rates,
                            r.get("redeem_clause") or "",
                            r.get("coupon_rate_desc") or "",
                        )
                        # 计算双低值 = 债现价 + 转股溢价率（%）
                        bond_price = prices.get("债现价")
                        premium_rate = prices.get("转股溢价率")
                        double_low = None
                        if bond_price is not None and premium_rate is not None:
                            double_low = round(float(bond_price) + float(premium_rate), 2)
                        # 距离强赎线（%）= 转股价值 / 130 * 100
                        redeem_progress = None
                        if convert_value is not None and convert_value > 0:
                            redeem_progress = round(convert_value / 130 * 100, 1)
                        # 下修博弈标记：剩余年限<=2年且转股价值<80，或债现价<100且溢价率>30
                        xiuzheng = False
                        remain_years = None
                        if r.get("expire_date"):
                            try:
                                from datetime import datetime as _dt
                                expire_dt = _dt.strptime(str(r["expire_date"])[:10], "%Y-%m-%d")
                                remain_years = (expire_dt - now).days / 365.25
                            except Exception:
                                pass
                        if remain_years is not None and remain_years <= 2:
                            if convert_value is not None and convert_value < 80:
                                xiuzheng = True
                        if bond_price is not None and bond_price < 100 and premium_rate is not None and premium_rate > 30:
                            xiuzheng = True

                        pos = positions_map.get(code)
                        position_info = None
                        if pos and bond_price is not None:
                            market_value = float(bond_price) * int(pos["quantity"]) * 10  # 1张=100元，价格*数量*10
                            cost = float(pos["cost_price"]) * int(pos["quantity"]) * 10
                            pnl = round(market_value - cost, 2)
                            pnl_pct = round(pnl / cost * 100, 2) if cost else 0
                            position_info = {
                                "cost_price": float(pos["cost_price"]),
                                "quantity": int(pos["quantity"]),
                                "market_value": round(market_value, 2),
                                "pnl": pnl,
                                "pnl_pct": pnl_pct,
                            }
                        records.append({
                            "债券代码":   code,
                            "债券简称":   r["bond_name"] or "",
                            "债现价":     bond_price,
                            "正股代码":   str(r["stock_code"] or "").zfill(6) if r["stock_code"] else "",
                            "正股简称":   r["stock_name"] or "",
                            "正股价":     prices.get("正股价"),
                            "转股溢价率": premium_rate,
                            "转股价":     conv_price,
                            "转股价值":   convert_value,
                            "双低值":     double_low,
                            "距离强赎线": redeem_progress,
                            "到期收益率": ytm,
                            "下修博弈":   xiuzheng,
                            "信用评级":   decode_credit_rating(r["credit_rating"]),
                            "剩余规模":   round(r["issue_size"] / 100, 2) if r["issue_size"] else None,
                            "发行规模":   round(r["issue_size_original"] / 100, 2) if r.get("issue_size_original") else None,
                            "上市日期":   _ts_to_date(r.get("listing_date")),
                            "退市日期":   _ts_to_date(r.get("delist_date")),
                            "到期日期":   _ts_to_date(r.get("expire_date")),
                            "正股PB":     stock.get("stock_pb"),
                            "正股市值":   stock.get("stock_market_cap"),
                            "强赎状态":   r.get("strong_redeem_status") or "",
                            "回售状态":   r.get("putback_status") or "",
                            "地区":       r.get("region") or "",
                            "has_adj_logs": has_adj_logs,
                            "付息日列表": pay_dates,
                            "持仓":       position_info,
                        })
                        if not r.get("listing_date") or not r.get("bond_name"):
                            missing_detail_codes.append(r["bond_code"])

                    # 后台补全缺失详细字段
                    if missing_detail_codes:
                        _fill_bond_details_async(missing_detail_codes)

                    _bond_list_cache["data"] = records
                    _bond_list_cache["expire_at"] = now + timedelta(seconds=_BOND_LIST_CACHE_TTL)
                    logger.info("[/api/bond_list] DB 缓存命中 age=%.0fs，返回 %d 只可转债（%d 只待后台补全）",
                                age, len(records), len(missing_detail_codes))
                    return jsonify({
                        "success": True,
                        "data": records,
                        "from_cache": True,
                        "pending_fill": len(missing_detail_codes) > 0,  # 前端据此决定是否延迟刷新
                    })
    except Exception as _e:
        logger.warning("[/api/bond_list] DB 读取失败，降级请求接口 err=%s", _e)

    # ── 请求接口 ──────────────────────────────────────────────────────────────
    logger.info("[/api/bond_list] 缓存未命中，请求全市场可转债列表")
    df = get_all_convertible_bonds()
    if df.empty:
        logger.error("[/api/bond_list] 获取可转债列表失败")
        return jsonify({"success": False, "message": "获取列表失败"}), 500

    # 返回列表页所需字段
    want_cols = ["债券代码", "债券简称", "债现价", "正股代码", "正股简称", "正股价", "转股溢价率", "到期收益率", "信用评级", "剩余规模", "发行规模", "上市日期", "退市日期", "到期日期", "正股PB", "正股市值", "强赎状态", "回售状态"]
    cols = [c for c in want_cols if c in df.columns]
    if not cols:
        cols = list(df.columns[:4])
    records = df[cols].to_dict(orient="records")
    # 添加转股价值、双低值、距离强赎线、下修博弈计算
    for row in records:
        # akshare bond_zh_cov() 对无实时行情的债现价会 fillna(100)，
        # 表现为「债现价==100 且 正股价缺失(NaN)」。此为占位假值，置为 None。
        _bp = row.get("债现价")
        _sp = row.get("正股价")
        _sp_missing = _sp is None or (isinstance(_sp, float) and math.isnan(_sp))
        if _bp == 100.0 and _sp_missing:
            row["债现价"] = None
        stock_price = row.get("正股价")
        conv_price = row.get("转股价")
        if stock_price and conv_price and conv_price > 0:
            row["转股价值"] = round(float(stock_price) / float(conv_price) * 100, 4)
        else:
            row["转股价值"] = None
        # 双低值
        bond_price = row.get("债现价")
        premium_rate = row.get("转股溢价率")
        if bond_price is not None and premium_rate is not None:
            row["双低值"] = round(float(bond_price) + float(premium_rate), 2)
        else:
            row["双低值"] = None
        # 距离强赎线
        convert_value = row.get("转股价值")
        if convert_value is not None and convert_value > 0:
            row["距离强赎线"] = round(convert_value / 130 * 100, 1)
        else:
            row["距离强赎线"] = None
        # 下修博弈
        xiuzheng = False
        remain_years = None
        expire_date = row.get("到期日期")
        if expire_date and len(str(expire_date)) == 8:
            try:
                expire_dt = datetime.strptime(str(expire_date), "%Y%m%d")
                remain_years = (expire_dt - now).days / 365.25
            except Exception:
                pass
        if remain_years is not None and remain_years <= 2:
            if convert_value is not None and convert_value < 80:
                xiuzheng = True
        if bond_price is not None and bond_price < 100 and premium_rate is not None and premium_rate > 30:
            xiuzheng = True
        row["下修博弈"] = xiuzheng
    # 加载持仓数据并合并
    try:
        positions_map2 = {str(p["bond_code"]): p for p in query_positions()}
    except Exception:
        positions_map2 = {}
    for row in records:
        code2 = str(row.get("债券代码", ""))
        pos2 = positions_map2.get(code2)
        bond_price2 = row.get("债现价")
        if pos2 and bond_price2 is not None:
            mv = float(bond_price2) * int(pos2["quantity"]) * 10
            cost2 = float(pos2["cost_price"]) * int(pos2["quantity"]) * 10
            pnl2 = round(mv - cost2, 2)
            pnl_pct2 = round(pnl2 / cost2 * 100, 2) if cost2 else 0
            row["持仓"] = {
                "cost_price": float(pos2["cost_price"]),
                "quantity": int(pos2["quantity"]),
                "market_value": round(mv, 2),
                "pnl": pnl2,
                "pnl_pct": pnl_pct2,
            }
        else:
            row["持仓"] = None
    # 清理 NaN / Inf，补充默认字段
    for row in records:
        for k, v in row.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                row[k] = None
        row.setdefault("has_adj_logs", False)
        row.setdefault("付息日列表", [])
        row.setdefault("到期收益率", None)

    # ── 批量写入 DB ───────────────────────────────────────────────────────────
    all_bond_codes = []
    try:
        db_rows = []
        for row in df.to_dict(orient="records"):
            db_row = bond_list_row_to_db_row(row)
            if db_row:
                db_rows.append(db_row)
                all_bond_codes.append(db_row["bond_code"])
        if db_rows:
            upsert_bonds(db_rows)
            logger.info("[/api/bond_list] 已批量写入 t_bond_info %d 条", len(db_rows))
    except Exception as _e:
        logger.warning("[/api/bond_list] 批量写入 t_bond_info 失败（不影响返回）err=%s", _e)

    # ── 批量写入 t_bond_daily（可转债日度数据）─────────────────────────────────
    try:
        trade_date = now.strftime("%Y-%m-%d 00:00:00")
        daily_rows = []
        for row in df.to_dict(orient="records"):
            dr = _to_daily_row(row, trade_date)
            if dr:
                daily_rows.append(dr)
        if daily_rows:
            upsert_daily_batch(daily_rows)
            logger.info("[/api/bond_list] 已批量写入 t_bond_daily %d 条", len(daily_rows))
    except Exception as _e:
        logger.warning("[/api/bond_list] 批量写入 t_bond_daily 失败（不影响返回）err=%s", _e)

    # ── 批量写入 t_stock_daily（正股日度数据：PB/市值等）────────────────────────
    try:
        trade_date = now.strftime("%Y-%m-%d 00:00:00")
        stock_daily_rows = []
        for row in df.to_dict(orient="records"):
            stock_code_raw = row.get("正股代码")
            stock_code = None
            try:
                stock_code = int(str(stock_code_raw).strip()) if stock_code_raw else None
            except (TypeError, ValueError):
                pass
            if stock_code:
                sr = _to_stock_daily_row(row, trade_date, stock_code)
                if sr:
                    stock_daily_rows.append(sr)
        if stock_daily_rows:
            upsert_stock_daily_batch(stock_daily_rows)
            logger.info("[/api/bond_list] 已批量写入 t_stock_daily %d 条", len(stock_daily_rows))
    except Exception as _e:
        logger.warning("[/api/bond_list] 批量写入 t_stock_daily 失败（不影响返回）err=%s", _e)

    # ── 后台补全详细字段（listing_date / delist_date 等）────────────────────────
    if all_bond_codes:
        _fill_bond_details_async(all_bond_codes)

    # 写入内存缓存
    _bond_list_cache["data"] = records
    _bond_list_cache["expire_at"] = now + timedelta(seconds=_BOND_LIST_CACHE_TTL)
    logger.info("[/api/bond_list] 返回 %d 只可转债，已写入内存缓存（TTL=%ds）", len(records), _BOND_LIST_CACHE_TTL)
    return jsonify({"success": True, "data": records})


# ── 正股财务缓存有效期（秒）：24 小时 ──────────────────────────────────────────
_STOCK_FINANCIALS_DB_TTL = 86400


def _safe_float(v):
    """
    将字符串 / NaN / Inf 安全转为 float 或 None。
    同花顺财务摘要数值常带单位后缀：
      "3.81亿"  → 38100.0（万元）
      "175.42亿" → 1754200.0（万元）
      "2.36%"   → 2.36
      "-30.56%" → -30.56
    统一以"万元"为基准存储（亿×10000），百分号直接去掉保留数值。
    """
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "-", "--", "nan", "NaN", "None"):
        return None
    try:
        multiplier = 1.0
        if s.endswith("亿"):
            s = s[:-1]
            multiplier = 10000.0   # 亿 → 万元
        elif s.endswith("万"):
            s = s[:-1]
            multiplier = 1.0       # 已是万元
        elif s.endswith("%"):
            s = s[:-1]
            multiplier = 1.0       # 百分号直接去掉，保留数值
        # 去掉千分位逗号
        s = s.replace(",", "")
        f = float(s) * multiplier
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


def _parse_financial_row(row: dict, period_col: str) -> dict:
    """
    将 stock_financial_abstract_ths 返回的一行转为统一字段 dict。
    period_col 是报告期列名（通常是 "报告期"）。
    同花顺返回的列名可能带单位后缀，如 "营业总收入(万元)"，因此先精确匹配，
    再做前缀/包含模糊匹配兜底。
    """
    def _g(aliases):
        """按候选列名列表取第一个有效值（精确 → 模糊前缀）"""
        # 1. 精确匹配
        for a in aliases:
            v = row.get(a)
            if v is not None and str(v).strip() not in ("", "-", "--", "nan"):
                return _safe_float(v)
        # 2. 模糊前缀/包含匹配（处理列名带单位后缀的情况）
        for a in aliases:
            for k, v in row.items():
                if k == period_col:
                    continue
                if k.startswith(a) or a in k:
                    if v is not None and str(v).strip() not in ("", "-", "--", "nan"):
                        return _safe_float(v)
        return None

    return {
        "period":        str(row.get(period_col, "")).strip(),
        "revenue":       _g(["营业总收入", "营业收入"]),
        "net_profit":    _g(["净利润", "归母净利润"]),
        "roe":           _g(["净资产收益率", "ROE(加权)"]),
        "debt_ratio":    _g(["资产负债率"]),
        # THS 摘要接口无经营现金流总额，使用每股经营现金流替代
        "op_cashflow":   _g(["每股经营现金流", "经营活动现金流量净额", "经营活动产生的现金流量净额"]),
        # THS 摘要接口无货币资金字段，前端显示 '-'
        "monetary_funds": _g(["货币资金"]),
        # 每股净资产（元/股）
        "bvps":           _g(["每股净资产"]),
        # 扣非净利润（万元，THS摘要字段）
        "net_profit_ex":  _g(["扣非净利润", "扣除非经常损益后的净利润"]),
        # 每股收益（元/股，用于前端估算 PE）
        "eps":            _g(["基本每股收益", "每股收益"]),
        # 每股分红（元/股，THS摘要）
        "dps":            _g(["每股股利", "每股现金股利", "每股分红"]),
    }


@app.route("/api/stock_financials")
def api_stock_financials():
    """
    查询正股财务数据（公司档案 + 近4年年度 + 近4季度单季数据）
    参数：stock_code - 正股代码（6位纯数字，如 600519）
    策略：DB 缓存 24h → 请求 akshare → 写入 DB
    """
    stock_code = request.args.get("stock_code", "").strip()
    if not stock_code:
        return jsonify({"success": False, "message": "请传入正股代码"}), 400

    # ── 尝试从 DB 读取缓存 ────────────────────────────────────────────────────
    try:
        cached = query_stock_financials(stock_code)
        if cached:
            age = (datetime.now() - datetime.strptime(cached["updated_at"], "%Y-%m-%d %H:%M:%S")).total_seconds()
            cached_annual    = json.loads(cached["annual_json"]    or "[]")
            cached_quarterly = json.loads(cached["quarterly_json"] or "[]")
            # 如果缓存行中 monetary_funds 为 None，说明是旧版缓存（sina 资产负债表尚未集成），强制视为过期
            # 同理，gross_profit 为 None 表示利润表字段未集成，也视为过期
            _a0 = cached_annual[0] if cached_annual else {}
            _has_sina = _a0.get("monetary_funds") is not None
            _has_income = _a0.get("gross_profit") is not None
            if age < _STOCK_FINANCIALS_DB_TTL and (cached_annual or cached_quarterly) and _has_sina and _has_income:
                logger.info("[/api/stock_financials] DB 缓存命中 stock_code=%s age=%.0fs", stock_code, age)
                profile = json.loads(cached["profile_json"] or "{}")
                # 同步地区到 t_bond_info（若存在）
                try:
                    region = profile.get("region", "")
                    if region:
                        from bond.db import get_conn
                        conn = get_conn()
                        conn.execute(
                            "UPDATE t_bond_info SET region = ? WHERE stock_code = ?",
                            (region, int(stock_code)),
                        )
                        conn.commit()
                except Exception as _re:
                    logger.warning("[/api/stock_financials] 同步地区到 t_bond_info 失败 stock_code=%s err=%s", stock_code, _re)
                return jsonify({
                    "success": True,
                    "data": {
                        "profile":   profile,
                        "annual":    cached_annual,
                        "quarterly": cached_quarterly,
                    },
                    "from_cache": True,
                })
            if not _has_sina or not _has_income:
                logger.info("[/api/stock_financials] 缓存缺 sina/利润表字段，强制重新拉取 stock_code=%s", stock_code)
    except Exception as _e:
        logger.warning("[/api/stock_financials] DB 读取失败，降级请求接口 stock_code=%s err=%s", stock_code, _e)

    # ── 从 akshare 拉取数据 ───────────────────────────────────────────────────
    logger.info("[/api/stock_financials] 请求财务数据 stock_code=%s", stock_code)

    # 1. 公司档案（巨潮资讯：注册地址、所属行业、证券简称、上市日期）
    # 注：原先使用东方财富 stock_individual_info_em，但 push2.eastmoney.com
    # 对无 User-Agent 的裸 requests 返回 RemoteDisconnected，已移除。
    # cninfo 可覆盖全部所需字段，直接作为唯一来源。
    profile = {}
    try:
        prof_df = ak.stock_profile_cninfo(symbol=stock_code)
        if not prof_df.empty:
            row0 = prof_df.iloc[0]
            # 注册地址 → 取省级地名
            addr = str(row0.get("注册地址", "") or "")
            region = ""
            for sep in ["省", "市", "自治区", "特别行政区"]:
                idx = addr.find(sep)
                if idx != -1:
                    region = addr[:idx + 1]
                    break
            if not region and addr:
                region = addr[:6]
            profile["region"]    = region
            profile["industry"]  = str(row0.get("所属行业", "") or "")
            profile["name"]      = str(row0.get("证券简称", "") or row0.get("公司名称", "") or "")
            profile["list_date"] = str(row0.get("上市日期", "") or "")
            # 企业性质推断：根据公司名称包含关键词判断
            company_name = str(row0.get("公司名称", "") or "")
            soe_kw = ["国有", "国资", "央企", "国控", "国投", "中国", "中央", "省属"]
            if any(kw in company_name for kw in soe_kw):
                profile["soe_type"] = "国企"
            else:
                profile["soe_type"] = "民营"
    except Exception as _e:
        logger.warning("[/api/stock_financials] 公司档案拉取失败 stock_code=%s err=%s", stock_code, _e)
        profile.setdefault("region",    "")
        profile.setdefault("industry",  "")
        profile.setdefault("name",      "")
        profile.setdefault("list_date", "")
        profile.setdefault("soe_type",  "")
    logger.info("[/api/stock_financials] 公司档案解析结果 stock_code=%s profile=%s", stock_code, profile)

    # 将地区信息同步回 t_bond_info（供列表页黑名单判断使用）
    try:
        region = profile.get("region", "")
        if region:
            from bond.db import get_conn
            conn = get_conn()
            conn.execute(
                "UPDATE t_bond_info SET region = ? WHERE stock_code = ?",
                (region, int(stock_code)),
            )
            conn.commit()
    except Exception as _re:
        logger.warning("[/api/stock_financials] 同步地区到 t_bond_info 失败 stock_code=%s err=%s", stock_code, _re)

    # 3. 年度财务（近4年，取最新4条）
    annual = []
    try:
        df = ak.stock_financial_abstract_ths(symbol=stock_code, indicator="按年度")
        logger.info("[/api/stock_financials] 年度财务列名 stock_code=%s cols=%s", stock_code, list(df.columns))
        logger.info("[/api/stock_financials] 年度财务原始数据 stock_code=%s rows=%d tail4=\n%s",
                    stock_code, len(df), df.tail(4).to_string())
        df = df.tail(4)  # 已按报告期升序，取最后4行（最新）
        for _, r in df.iterrows():
            annual.append(_parse_financial_row(r.to_dict(), "报告期"))
        annual.reverse()  # 最新在前
        logger.info("[/api/stock_financials] 年度财务解析结果 stock_code=%s parsed=%s", stock_code, annual)
    except Exception as _e:
        logger.warning("[/api/stock_financials] 年度财务拉取失败 stock_code=%s err=%s", stock_code, _e)

    # 4. 季度财务（近4个单季度，取最新4条）
    quarterly = []
    try:
        df = ak.stock_financial_abstract_ths(symbol=stock_code, indicator="按单季度")
        logger.info("[/api/stock_financials] 季度财务列名 stock_code=%s cols=%s", stock_code, list(df.columns))
        logger.info("[/api/stock_financials] 季度财务原始数据 stock_code=%s rows=%d tail4=\n%s",
                    stock_code, len(df), df.tail(4).to_string())
        df = df.tail(8)
        for _, r in df.iterrows():
            quarterly.append(_parse_financial_row(r.to_dict(), "报告期"))
        quarterly.reverse()
        logger.info("[/api/stock_financials] 季度财务解析结果 stock_code=%s parsed=%s", stock_code, quarterly)
    except Exception as _e:
        logger.warning("[/api/stock_financials] 季度财务拉取失败 stock_code=%s err=%s", stock_code, _e)

    # 5. 新浪资产负债表：货币资金 + 流动资产合计 + 流动负债合计（近期）+ 非流动负债合计（远期）
    # 接口返回单位为元，换算成亿（÷1e8）存储
    # 正股代码加交易所前缀：6开头→sh，其余→sz
    _sina_prefix = "sh" if stock_code.startswith("6") else "sz"
    _sina_symbol = _sina_prefix + stock_code
    # {报告日YYYYMMDD: {"monetary_funds": float, "current_assets": float, "current_liab": float, "noncurrent_liab": float}}
    _sina_map: dict[str, dict] = {}
    _SINA_COL_MAP = {
        "货币资金":    "monetary_funds",
        "流动资产合计":  "current_assets",
        "流动负债合计":  "current_liab",
        "非流动负债合计": "noncurrent_liab",
        "应收账款":    "accounts_receivable",
        "其他应收款":   "other_receivable",
        "资产总计":    "total_assets",
        "短期借款":    "short_term_debt",
        "长期借款":    "long_term_debt",
        "应付债券":    "bonds_payable",
        "一年内到期的非流动负债": "current_portion_long_debt",
    }
    try:
        sina_df = ak.stock_financial_report_sina(stock=_sina_symbol, symbol="资产负债表")
        if not sina_df.empty and "报告日" in sina_df.columns:
            avail_cols = [c for c in _SINA_COL_MAP if c in sina_df.columns]
            for _, r in sina_df.iterrows():
                rd_key = str(r["报告日"]).strip().replace("-", "")  # 统一为 YYYYMMDD
                entry: dict = {}
                for col in avail_cols:
                    v = _safe_float(r.get(col))
                    if v is not None:
                        entry[_SINA_COL_MAP[col]] = round(v / 1e8, 4)  # 元→亿
                if entry:
                    _sina_map[rd_key] = entry
        logger.info("[/api/stock_financials] 新浪资产负债表 map stock_code=%s size=%d keys=%s",
                    stock_code, len(_sina_map), list(_sina_map.keys())[:4])
    except Exception as _e:
        logger.warning("[/api/stock_financials] 新浪资产负债表拉取失败 stock_code=%s err=%s", stock_code, _e)

    # 6. 新浪现金流量表：经营活动产生的现金流量净额（元→亿）
    _cf_map: dict[str, float] = {}
    try:
        cf_df = ak.stock_financial_report_sina(stock=_sina_symbol, symbol="现金流量表")
        if not cf_df.empty and "报告日" in cf_df.columns:
            _CF_KEY = "经营活动产生的现金流量净额"
            if _CF_KEY in cf_df.columns:
                for _, r in cf_df.iterrows():
                    rd_key = str(r["报告日"]).strip().replace("-", "")
                    v = _safe_float(r.get(_CF_KEY))
                    if v is not None:
                        _cf_map[rd_key] = round(v / 1e8, 4)
        logger.info("[/api/stock_financials] 新浪现金流量表 map stock_code=%s size=%d keys=%s",
                    stock_code, len(_cf_map), list(_cf_map.keys())[:4])
    except Exception as _e:
        logger.warning("[/api/stock_financials] 新浪现金流量表拉取失败 stock_code=%s err=%s", stock_code, _e)

    # 7. 新浪利润表：毛利润 + 财务费用（利息费用）+ 扣非净利润（元→亿）
    # {报告日YYYYMMDD: {"gross_profit": float, "interest_exp": float, "net_profit_ex": float}}
    _income_map: dict[str, dict] = {}
    _INCOME_COL_MAP = {
        "营业利润":        "operating_profit",  # 部分公司用营业利润代替毛利
        "销售毛利润":      "gross_profit",       # 部分公司有此字段
        "财务费用":        "interest_exp",       # 财务费用≈利息净支出
        "扣除非经常损益后的净利润": "net_profit_ex",
        "非经常性损益":    "nonrecurring",       # 用于推算扣非：net_profit - nonrecurring
    }
    try:
        inc_df = ak.stock_financial_report_sina(stock=_sina_symbol, symbol="利润表")
        if not inc_df.empty and "报告日" in inc_df.columns:
            avail_income = [c for c in _INCOME_COL_MAP if c in inc_df.columns]
            logger.info("[/api/stock_financials] 新浪利润表可用列 stock_code=%s cols=%s", stock_code, avail_income)
            for _, r in inc_df.iterrows():
                rd_key = str(r["报告日"]).strip().replace("-", "")
                entry_i: dict = {}
                # 毛利润：优先用营业收入-营业成本推算
                rev_v = _safe_float(r.get("营业收入") or r.get("营业总收入"))
                cogs_v = _safe_float(r.get("营业成本"))
                if rev_v is not None and cogs_v is not None and rev_v != 0:
                    entry_i["gross_profit"] = round((rev_v - cogs_v) / 1e8, 4)
                elif _INCOME_COL_MAP.get("销售毛利润") and r.get("销售毛利润") is not None:
                    v = _safe_float(r.get("销售毛利润"))
                    if v is not None:
                        entry_i["gross_profit"] = round(v / 1e8, 4)
                # 其余字段
                for col in avail_income:
                    if col in ("销售毛利润", "营业利润"):
                        continue  # 已处理
                    field = _INCOME_COL_MAP[col]
                    v = _safe_float(r.get(col))
                    if v is not None:
                        entry_i[field] = round(v / 1e8, 4)
                if entry_i:
                    _income_map[rd_key] = entry_i
        logger.info("[/api/stock_financials] 新浪利润表 map stock_code=%s size=%d keys=%s",
                    stock_code, len(_income_map), list(_income_map.keys())[:4])
    except Exception as _e:
        logger.warning("[/api/stock_financials] 新浪利润表拉取失败 stock_code=%s err=%s", stock_code, _e)

    # 8. PE(TTM) + 每股分红：从实时行情摘要拉取（stock_individual_info_em 已知有问题，改用 stock_zh_a_spot_em）
    _spot_pe: float | None = None
    _spot_dps: float | None = None
    try:
        spot_df = ak.stock_zh_a_spot_em()
        if not spot_df.empty:
            row_s = spot_df[spot_df["代码"] == stock_code]
            if not row_s.empty:
                r0 = row_s.iloc[0]
                # 市盈率(动态) 或 市盈率(TTM)
                for pe_col in ["市盈率-动态", "市盈率(TTM)", "市盈率"]:
                    if pe_col in r0.index:
                        _spot_pe = _safe_float(r0.get(pe_col))
                        if _spot_pe is not None and _spot_pe <= 0:
                            _spot_pe = None  # 负PE无意义
                        break
        logger.info("[/api/stock_financials] 实时行情 PE stock_code=%s pe=%s", stock_code, _spot_pe)
    except Exception as _e:
        logger.warning("[/api/stock_financials] 实时行情拉取失败 stock_code=%s err=%s", stock_code, _e)

    # 9. 每股分红：从分红配送历史取最近一年合计（元/股）
    try:
        div_df = ak.stock_history_dividend_detail(symbol=stock_code, indicator="分红")
        if not div_df.empty:
            # 列名可能是：公告日期/每股派息/派现比例
            div_col = None
            for c in ["每股派息(税前)(元)", "每股派息", "派息(元/10股)", "股息(元/股)"]:
                if c in div_df.columns:
                    div_col = c
                    break
            date_col = None
            for c in ["公告日期", "除权除息日", "登记日"]:
                if c in div_df.columns:
                    date_col = c
                    break
            if div_col and date_col:
                div_df = div_df.sort_values(date_col, ascending=False).head(3)
                total_dps = 0.0
                for _, dr in div_df.iterrows():
                    v = _safe_float(dr.get(div_col))
                    if v is not None:
                        # 若是"元/10股"单位需 ÷10
                        if "10股" in div_col:
                            v = v / 10
                        total_dps += v
                if total_dps > 0:
                    _spot_dps = round(total_dps, 4)
        logger.info("[/api/stock_financials] 每股分红 stock_code=%s dps=%s", stock_code, _spot_dps)
    except Exception as _e:
        logger.warning("[/api/stock_financials] 每股分红拉取失败 stock_code=%s err=%s", stock_code, _e)

    # 将各新浪/实时字段回填到 annual/quarterly（按报告期末日匹配）
    # THS 年度报告期格式为 "2024"（整年），对应资产负债表 "20241231"
    # THS 季度报告期格式为 "2024-03-31"，对应 "20240331"
    def _fill_sina_fields(rows: list):
        for row in rows:
            period = row.get("period", "")
            if not period:
                continue
            # 年度：4位年份 → YYYY1231
            key = (period + "1231") if (len(period) == 4 and period.isdigit()) else period.replace("-", "")
            entry = _sina_map.get(key, {})
            row["monetary_funds"]  = entry.get("monetary_funds")
            row["current_assets"]  = entry.get("current_assets")
            row["current_liab"]    = entry.get("current_liab")
            row["noncurrent_liab"] = entry.get("noncurrent_liab")
            # 新增字段
            row["accounts_receivable"]     = entry.get("accounts_receivable")
            row["other_receivable"]        = entry.get("other_receivable")
            row["total_assets"]            = entry.get("total_assets")
            row["short_term_debt"]         = entry.get("short_term_debt")
            row["long_term_debt"]          = entry.get("long_term_debt")
            row["bonds_payable"]           = entry.get("bonds_payable")
            row["current_portion_long_debt"] = entry.get("current_portion_long_debt")
            row["op_cashflow_total"] = _cf_map.get(key)
            # 利润表字段
            inc = _income_map.get(key, {})
            row["gross_profit"]    = inc.get("gross_profit")
            row["interest_exp"]    = inc.get("interest_exp")
            row["net_profit_ex"]   = inc.get("net_profit_ex") or row.get("net_profit_ex")  # 优先 sina，其次 THS
        # PE + 分红写到第一行（最新期，全局唯一）
        if rows:
            rows[0]["pe_ttm"] = _spot_pe
            rows[0]["dps"]    = _spot_dps or rows[0].get("dps")  # 优先历史分红，其次 THS

    _fill_sina_fields(annual)
    _fill_sina_fields(quarterly)

    # ── 写入 DB 缓存（仅财务数据非空时才缓存，避免 stale empty cache）────────
    if annual or quarterly:
        try:
            upsert_stock_financials(
                stock_code,
                json.dumps(profile,   ensure_ascii=False),
                json.dumps(annual,    ensure_ascii=False),
                json.dumps(quarterly, ensure_ascii=False),
            )
            logger.info("[/api/stock_financials] 已写入 DB 缓存 stock_code=%s", stock_code)
        except Exception as _e:
            logger.warning("[/api/stock_financials] 写入 DB 缓存失败 stock_code=%s err=%s", stock_code, _e)
    else:
        logger.info("[/api/stock_financials] 财务数据为空，跳过 DB 缓存 stock_code=%s", stock_code)

    return jsonify({"success": True, "data": {"profile": profile, "annual": annual, "quarterly": quarterly}})


@app.route("/api/stock_news")
def api_stock_news():
    """
    查询正股最新公告/新闻（用于详情页「公告/热点」Tab）
    参数：stock_code - 正股代码（6位纯数字）
    返回：[{title, date, url, source}, ...]，最多30条
    """
    stock_code = request.args.get("stock_code", "").strip()
    if not stock_code:
        return jsonify({"success": False, "message": "请传入正股代码"}), 400

    results = []

    # ── 东方财富个股新闻（stock_news_em） ─────────────────────────────────────
    try:
        news_df = ak.stock_news_em(symbol=stock_code)
        if not news_df.empty:
            # 列名可能是：新闻标题 / 新闻内容 / 发布时间 / 文章来源 / 新闻链接
            for _, r in news_df.head(20).iterrows():
                title  = str(r.get("新闻标题") or r.get("title") or "").strip()
                date   = str(r.get("发布时间") or r.get("date") or "").strip()
                url    = str(r.get("新闻链接") or r.get("url") or "").strip()
                source = str(r.get("文章来源") or r.get("source") or "东方财富").strip()
                if title:
                    results.append({"title": title, "date": date, "url": url, "source": source, "type": "news"})
        logger.info("[/api/stock_news] EM新闻 stock_code=%s count=%d", stock_code, len(results))
    except Exception as _e:
        logger.warning("[/api/stock_news] EM新闻拉取失败 stock_code=%s err=%s", stock_code, _e)

    # ── 同花顺个股公告（stock_notice_report） ────────────────────────────────
    try:
        notice_df = ak.stock_notice_report(symbol=stock_code)
        if not notice_df.empty:
            # 列名参考：标题 / 公告时间 / 链接
            for _, r in notice_df.head(15).iterrows():
                title  = str(r.get("标题") or r.get("title") or "").strip()
                date   = str(r.get("公告时间") or r.get("date") or "").strip()
                url    = str(r.get("链接") or r.get("url") or "").strip()
                source = "公告"
                if title:
                    results.append({"title": title, "date": date, "url": url, "source": source, "type": "notice"})
        logger.info("[/api/stock_news] 公告 stock_code=%s extra=%d total=%d", stock_code, len(results), len(results))
    except Exception as _e:
        logger.warning("[/api/stock_news] 公告拉取失败 stock_code=%s err=%s", stock_code, _e)

    # 按日期倒序排序（字符串比较，YYYY-MM-DD / YYYY-MM-DD HH:MM:SS 均兼容）
    results.sort(key=lambda x: x.get("date", ""), reverse=True)

    return jsonify({"success": True, "data": results[:30]})


# ── 持仓接口 ──────────────────────────────────────────────────────────────────

@app.route("/api/positions", methods=["GET"])
def api_positions():
    """查询所有持仓"""
    try:
        positions = query_positions()
        return jsonify({"success": True, "data": positions})
    except Exception as e:
        logger.warning("[/api/positions] 查询失败 err=%s", e)
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/position", methods=["POST"])
def api_position_post():
    """添加/更新持仓"""
    data = request.get_json() or {}
    bond_code = data.get("bond_code")
    bond_name = data.get("bond_name", "")
    cost_price = data.get("cost_price")
    quantity = data.get("quantity")
    if not bond_code or cost_price is None or quantity is None:
        return jsonify({"success": False, "message": "请传入 bond_code, cost_price, quantity"}), 400
    try:
        upsert_position(int(bond_code), str(bond_name), float(cost_price), int(quantity))
        return jsonify({"success": True})
    except Exception as e:
        logger.warning("[/api/position] 写入失败 bond_code=%s err=%s", bond_code, e)
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/position", methods=["DELETE"])
def api_position_delete():
    """删除持仓"""
    bond_code = request.args.get("bond_code", "").strip()
    if not bond_code:
        return jsonify({"success": False, "message": "请传入 bond_code"}), 400
    try:
        delete_position(int(bond_code))
        return jsonify({"success": True})
    except Exception as e:
        logger.warning("[/api/position] 删除失败 bond_code=%s err=%s", bond_code, e)
        return jsonify({"success": False, "message": str(e)}), 500


# ── 预警接口 ──────────────────────────────────────────────────────────────────

@app.route("/api/alerts", methods=["GET"])
def api_alerts():
    """查询预警规则，可指定 bond_code"""
    bond_code = request.args.get("bond_code", "").strip()
    try:
        alerts = query_alerts(int(bond_code) if bond_code else None)
        return jsonify({"success": True, "data": alerts})
    except Exception as e:
        logger.warning("[/api/alerts] 查询失败 err=%s", e)
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/alert", methods=["POST"])
def api_alert_post():
    """添加预警规则"""
    data = request.get_json() or {}
    bond_code = data.get("bond_code")
    alert_type = data.get("alert_type")
    operator = data.get("operator")
    threshold = data.get("threshold")
    if not bond_code or not alert_type or not operator or threshold is None:
        return jsonify({"success": False, "message": "请传入 bond_code, alert_type, operator, threshold"}), 400
    try:
        alert_id = add_alert(int(bond_code), str(alert_type), str(operator), float(threshold))
        return jsonify({"success": True, "data": {"id": alert_id}})
    except Exception as e:
        logger.warning("[/api/alert] 写入失败 err=%s", e)
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/alert", methods=["DELETE"])
def api_alert_delete():
    """删除预警规则"""
    alert_id = request.args.get("id", "").strip()
    if not alert_id:
        return jsonify({"success": False, "message": "请传入 id"}), 400
    try:
        delete_alert(int(alert_id))
        return jsonify({"success": True})
    except Exception as e:
        logger.warning("[/api/alert] 删除失败 id=%s err=%s", alert_id, e)
        return jsonify({"success": False, "message": str(e)}), 500


# ── 笔记接口 ──────────────────────────────────────────────────────────────────

@app.route("/api/note", methods=["GET"])
def api_note_get():
    """查询单只债券笔记"""
    bond_code = request.args.get("bond_code", "").strip()
    if not bond_code:
        return jsonify({"success": False, "message": "请传入 bond_code"}), 400
    try:
        note = query_note(int(bond_code))
        return jsonify({"success": True, "data": note})
    except Exception as e:
        logger.warning("[/api/note] 查询失败 bond_code=%s err=%s", bond_code, e)
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/note", methods=["POST"])
def api_note_post():
    """保存笔记"""
    data = request.get_json() or {}
    bond_code = data.get("bond_code")
    content = data.get("content", "")
    if not bond_code:
        return jsonify({"success": False, "message": "请传入 bond_code"}), 400
    try:
        upsert_note(int(bond_code), str(content))
        return jsonify({"success": True})
    except Exception as e:
        logger.warning("[/api/note] 写入失败 bond_code=%s err=%s", bond_code, e)
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/note", methods=["DELETE"])
def api_note_delete():
    """删除笔记"""
    bond_code = request.args.get("bond_code", "").strip()
    if not bond_code:
        return jsonify({"success": False, "message": "请传入 bond_code"}), 400
    try:
        delete_note(int(bond_code))
        return jsonify({"success": True})
    except Exception as e:
        logger.warning("[/api/note] 删除失败 bond_code=%s err=%s", bond_code, e)
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
