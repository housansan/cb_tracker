from flask import Flask, render_template, request, jsonify
from bond import get_convertible_bond_history, get_all_convertible_bonds, get_bond_info, fetch_bond_detail_only, get_bond_adj_logs
from bond.fetch import fetch_all_cb_remaining
from bond.history import build_cashflows_from_coupon_info
from config import LOG_CONFIG, EXPORT_CONFIG, DB_CONFIG, BOND_CONFIG
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
import logging
import math
import os
import threading
import time

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
    init_db, upsert_bond, upsert_bonds,
    query_bond, query_bonds, count_bonds,
    bond_info_to_db_row, bond_list_row_to_db_row,
    db_row_to_bond_info, query_bond_updated_at, query_latest_updated_at,
    decode_credit_rating,
)
init_db(DB_CONFIG["dir"])

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

# 实时价格缓存（债现价、正股价、转股溢价率），TTL 较短
_price_cache = {
    "data": None,       # dict: bond_code -> {"债现价": x, "正股价": x, "转股溢价率": x}
    "expire_at": None,
}
_PRICE_CACHE_TTL = 3600  # 1 小时


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
        for _, row in df.iterrows():
            code = str(row.get("债券代码", "")).strip()
            if code:
                price_map[code] = {
                    "债现价":     _clean(row.get("债现价")),
                    "正股价":     _clean(row.get("正股价")),
                    "转股溢价率": _clean(row.get("转股溢价率")),
                }
        _price_cache["data"] = price_map
        _price_cache["expire_at"] = now + timedelta(seconds=_PRICE_CACHE_TTL)
        logger.info("[price_map] 实时价格缓存已更新，共 %d 只", len(price_map))
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


@app.route("/")
def index():
    return render_template("index.html")


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
                    logger.info("[/api/bond_info] DB 缓存命中 bond_code=%s age=%.0fs", bond_code, age)
                    _attach_cashflows(info)
                    return jsonify({"success": True, "data": info, "from_cache": True})
    except Exception as _e:
        logger.warning("[/api/bond_info] DB 读取失败，降级请求接口 bond_code=%s err=%s", bond_code, _e)

    # ── 请求接口 ──────────────────────────────────────────────────────────────
    logger.info("[/api/bond_info] 请求基础信息 bond_code=%s", bond_code)
    info = get_bond_info(bond_code)
    if not info:
        logger.warning("[/api/bond_info] 未找到可转债 bond_code=%s", bond_code)
        return jsonify({"success": False, "message": f"未找到代码为 {bond_code} 的可转债"}), 404

    # ── 写入 DB ───────────────────────────────────────────────────────────────
    try:
        db_row = bond_info_to_db_row(info)
        if db_row:
            upsert_bond(db_row)
            logger.info("[/api/bond_info] 已写入 DB bond_code=%s", bond_code)
    except Exception as _e:
        logger.warning("[/api/bond_info] 写入 DB 失败（不影响返回）bond_code=%s err=%s", bond_code, _e)

    logger.info("[/api/bond_info] 返回基础信息 bond_code=%s name=%s", bond_code, info.get("债券简称", ""))
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

                    records = []
                    missing_detail_codes = []  # listing_date 为空的，需后台补全
                    for r in db_rows:
                        code = str(r["bond_code"])
                        prices = price_map.get(code, {})
                        records.append({
                            "债券代码":   code,
                            "债券简称":   r["bond_name"] or "",
                            "债现价":     prices.get("债现价"),
                            "正股代码":   str(r["stock_code"] or ""),
                            "正股简称":   r["stock_name"] or "",
                            "正股价":     prices.get("正股价"),
                            "转股溢价率": prices.get("转股溢价率"),
                            "转股价":     round(r["conv_price"] / 100, 4) if r["conv_price"] else None,
                            "信用评级":   decode_credit_rating(r["credit_rating"]),
                            "剩余规模":   round(r["issue_size"] / 100, 2) if r["issue_size"] else None,
                            "上市日期":   _ts_to_date(r.get("listing_date")),
                            "退市日期":   _ts_to_date(r.get("delist_date")),
                            "到期日期":   _ts_to_date(r.get("expire_date")),
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
                    return jsonify({"success": True, "data": records, "from_cache": True})
    except Exception as _e:
        logger.warning("[/api/bond_list] DB 读取失败，降级请求接口 err=%s", _e)

    # ── 请求接口 ──────────────────────────────────────────────────────────────
    logger.info("[/api/bond_list] 缓存未命中，请求全市场可转债列表")
    df = get_all_convertible_bonds()
    if df.empty:
        logger.error("[/api/bond_list] 获取可转债列表失败")
        return jsonify({"success": False, "message": "获取列表失败"}), 500

    # 返回列表页所需字段
    want_cols = ["债券代码", "债券简称", "债现价", "正股代码", "正股简称", "正股价", "转股溢价率", "信用评级", "剩余规模", "上市日期", "退市日期"]
    cols = [c for c in want_cols if c in df.columns]
    if not cols:
        cols = list(df.columns[:4])
    records = df[cols].to_dict(orient="records")
    # 清理 NaN / Inf
    for row in records:
        for k, v in row.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                row[k] = None

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
            logger.info("[/api/bond_list] 已批量写入 DB %d 条", len(db_rows))
    except Exception as _e:
        logger.warning("[/api/bond_list] 批量写入 DB 失败（不影响返回）err=%s", _e)

    # ── 后台补全详细字段（listing_date / delist_date 等）────────────────────────
    if all_bond_codes:
        _fill_bond_details_async(all_bond_codes)

    # 写入内存缓存
    _bond_list_cache["data"] = records
    _bond_list_cache["expire_at"] = now + timedelta(seconds=_BOND_LIST_CACHE_TTL)
    logger.info("[/api/bond_list] 返回 %d 只可转债，已写入内存缓存（TTL=%ds）", len(records), _BOND_LIST_CACHE_TTL)
    return jsonify({"success": True, "data": records})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
