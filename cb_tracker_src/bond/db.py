"""
bond/db.py —— SQLite 数据库访问层

职责：
  - 初始化数据库目录和连接
  - 建表（t_bond_info）
  - 提供 upsert / query 等基础操作

使用方式：
    from bond.db import init_db, upsert_bond, query_bonds
"""

import os
import re
import sqlite3
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 数据库文件名
_DB_FILENAME = "bond.db"
_USER_DB_FILENAME = "user.db"

# 全局连接（懒初始化）
_conn: Optional[sqlite3.Connection] = None
_user_conn: Optional[sqlite3.Connection] = None


# ── 建表 DDL ──────────────────────────────────────────────────────────────────

_DDL_BOND_INFO = """
CREATE TABLE IF NOT EXISTS t_bond_info (
    id               INTEGER           NOT NULL PRIMARY KEY AUTOINCREMENT,  -- 自增主键
    bond_code        INTEGER UNSIGNED  NOT NULL UNIQUE,                     -- 债券代码（如113050）
    bond_name        VARCHAR(20)       NOT NULL,                            -- 债券简称
    stock_code       INTEGER UNSIGNED,                                      -- 正股代码（如600519）
    stock_name       VARCHAR(20),                                           -- 正股简称
    conv_price       INTEGER UNSIGNED,                                      -- 转股价×100（元），如10.25→1025
    issue_size       INTEGER UNSIGNED,                                      -- 剩余规模×100（亿元），如8.00→800
    issue_size_original INTEGER UNSIGNED,                                   -- 发行规模×100（亿元）
    credit_rating    SMALLINT UNSIGNED,                                     -- 信用评级编码，AAA=700，C=50，未知=0
    listing_date     TIMESTAMP,                                             -- 上市日期
    delist_date      TIMESTAMP,                                             -- 退市日期，NULL=在途
    value_date       TIMESTAMP,                                             -- 起息日
    expire_date      TIMESTAMP,                                             -- 到期日
    redeem_clause    VARCHAR(500),                                          -- 赎回条款原文（用于实时解析赎回价，不存储解析结果）
    coupon_rate_desc VARCHAR(500),                                          -- 利率说明原文
    coupon_rates     VARCHAR(200),                                          -- 各年票息率 JSON数组字符串
    coupon_pay_dates VARCHAR(500),                                          -- 付息日列表 JSON数组字符串
    strong_redeem_status VARCHAR(20),                                      -- 强赎状态：强赎中/临近强赎/无
    putback_status  VARCHAR(20),                                          -- 回售状态：回售中/临近回售/无
    created_at       TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- 首次入库时间
    updated_at       TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP   -- 最后更新时间
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_bond_code ON t_bond_info (bond_code);
"""

# ── 正股财务 DDL ──────────────────────────────────────────────────────────────

_DDL_STOCK_FINANCIALS = """
CREATE TABLE IF NOT EXISTS t_stock_financials (
    id            INTEGER           NOT NULL PRIMARY KEY AUTOINCREMENT,
    stock_code    VARCHAR(10)       NOT NULL UNIQUE,   -- 正股代码（如 600519）
    profile_json  TEXT,                                -- 公司档案 JSON
    annual_json   TEXT,                                -- 年度财务 JSON（近4年）
    quarterly_json TEXT,                               -- 季度财务 JSON（近4季度）
    created_at    TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# ── 信用评级编码映射 ──────────────────────────────────────────────────────────

CREDIT_RATING_MAP: dict[str, int] = {
    "AAA":  700,
    "AA+":  650,
    "AA":   600,
    "AA-":  550,
    "A+":   500,
    "A":    450,
    "A-":   400,
    "BBB+": 350,
    "BBB":  300,
    "BBB-": 250,
    "BB+":  230,
    "BB":   210,
    "BB-":  190,
    "B+":   170,
    "B":    150,
    "B-":   130,
    "CCC":  100,
    "CC":    70,
    "C":     50,
}

# 反向映射：整数 → 评级字符串
CREDIT_RATING_REVERSE: dict[int, str] = {v: k for k, v in CREDIT_RATING_MAP.items()}


def encode_credit_rating(rating: Optional[str]) -> int:
    """将评级字符串转为整数编码，未知/空返回 0"""
    if not rating:
        return 0
    return CREDIT_RATING_MAP.get(rating.strip().upper(), 0)


def decode_credit_rating(code: Optional[int]) -> str:
    """将整数编码还原为评级字符串，未知返回空字符串"""
    if code is None:
        return ""
    return CREDIT_RATING_REVERSE.get(code, "")


# ── 连接管理 ──────────────────────────────────────────────────────────────────

def init_db(db_dir: str) -> None:
    """
    初始化数据库：
      1. 确保目录存在
      2. 建立连接
      3. 执行建表 DDL
    应在应用启动时调用一次。
    """
    global _conn

    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, _DB_FILENAME)

    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row          # 查询结果支持按列名访问
    _conn.execute("PRAGMA journal_mode=WAL")  # 提升并发读性能
    _conn.execute("PRAGMA foreign_keys=ON")

    _conn.executescript(_DDL_BOND_INFO)
    _conn.executescript(_DDL_BOND_DAILY)
    _conn.executescript(_DDL_STOCK_DAILY)
    _conn.executescript(_DDL_STOCK_FINANCIALS)
    # 用户表（持仓/预警/笔记）已迁移至独立数据库，见 init_user_db()

    # ── 在线迁移：t_bond_daily ───────────────────────────────────────────────
    daily_cols = {
        row[1]
        for row in _conn.execute("PRAGMA table_info(t_bond_daily)").fetchall()
    }
    for col_name, col_def in [
        ("issue_size", "ALTER TABLE t_bond_daily ADD COLUMN issue_size INTEGER UNSIGNED"),
    ]:
        if col_name not in daily_cols:
            try:
                _conn.execute(col_def)
            except Exception as e:
                logger.warning("[db] 添加列 %s 失败：%s", col_name, e)

    # ── 在线迁移：t_bond_info ────────────────────────────────────────────────
    bond_info_cols = {
        row[1]
        for row in _conn.execute("PRAGMA table_info(t_bond_info)").fetchall()
    }
    
    # 迁移：添加 redeem_clause 列
    if "redeem_clause" not in bond_info_cols:
        try:
            _conn.execute("ALTER TABLE t_bond_info ADD COLUMN redeem_clause VARCHAR(500)")
            logger.info("[db] 迁移：t_bond_info 新增 redeem_clause 列")
        except Exception as e:
            logger.warning("[db] 添加列 redeem_clause 失败：%s", e)
    
    # 迁移：添加 t_bond_info 静态字段列
    _BOND_INFO_NEW_COLUMNS = [
        ("issue_size_original", "INTEGER UNSIGNED"),
        ("strong_redeem_status", "VARCHAR(20)"),
        ("putback_status", "VARCHAR(20)"),
    ]
    
    for col_name, col_def in _BOND_INFO_NEW_COLUMNS:
        if col_name not in bond_info_cols:
            try:
                _conn.execute(f"ALTER TABLE t_bond_info ADD COLUMN {col_name} {col_def}")
                logger.info("[db] 迁移：t_bond_info 新增 %s 列", col_name)
            except Exception as e:
                logger.warning("[db] 添加列 %s 失败：%s", col_name, e)
    
    # 清理：t_bond_daily 中已移除的正股字段（如果旧库还有，忽略即可）
    # stock_close / stock_pb / stock_market_cap 已从 DDL 移除，
    # 旧表若仍有这些列不影响运行，后续可用 VACUUM 重建
    
    # ── 在线迁移：t_stock_daily ──────────────────────────────────────────────
    stock_daily_cols = {
        row[1]
        for row in _conn.execute("PRAGMA table_info(t_stock_daily)").fetchall()
    }
    _STOCK_DAILY_NEW_COLUMNS = [
        ("stock_close", "INTEGER UNSIGNED"),
        ("stock_pb", "DECIMAL(10,4)"),
        ("stock_market_cap", "DECIMAL(16,4)"),
    ]
    for col_name, col_def in _STOCK_DAILY_NEW_COLUMNS:
        if col_name not in stock_daily_cols:
            try:
                _conn.execute(f"ALTER TABLE t_stock_daily ADD COLUMN {col_name} {col_def}")
                logger.info("[db] 迁移：t_stock_daily 新增 %s 列", col_name)
            except Exception as e:
                logger.warning("[db] 添加列 %s 失败：%s", col_name, e)
    
    _conn.commit()

    logger.info(f"[db] 数据库已初始化：{db_path}")


def get_conn() -> sqlite3.Connection:
    """获取全局连接，未初始化时抛出异常"""
    if _conn is None:
        raise RuntimeError("数据库未初始化，请先调用 init_db()")
    return _conn


def init_user_db(db_dir: str) -> None:
    """初始化用户数据库（持仓 / 预警 / 笔记）"""
    global _user_conn

    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, _USER_DB_FILENAME)

    _user_conn = sqlite3.connect(db_path, check_same_thread=False)
    _user_conn.row_factory = sqlite3.Row
    _user_conn.execute("PRAGMA journal_mode=WAL")
    _user_conn.execute("PRAGMA foreign_keys=ON")

    _user_conn.executescript(_DDL_USER_BOND)
    _user_conn.executescript(_DDL_ALERT)
    _user_conn.commit()

    logger.info(f"[db] 用户数据库已初始化：{db_path}")


def get_user_conn() -> sqlite3.Connection:
    """获取用户数据库连接"""
    if _user_conn is None:
        raise RuntimeError("用户数据库未初始化，请先调用 init_user_db()")
    return _user_conn


def close_db() -> None:
    """关闭数据库连接"""
    global _conn
    if _conn:
        _conn.close()
        _conn = None
        logger.info("[db] 数据库连接已关闭")


# ── CRUD ──────────────────────────────────────────────────────────────────────

# upsert 时需要更新的字段（排除 id / bond_code / created_at）
_UPSERT_FIELDS = [
    "bond_name", "stock_code", "stock_name",
    "conv_price", "issue_size", "issue_size_original", "credit_rating",
    "listing_date", "delist_date", "value_date", "expire_date",
    "redeem_clause", "coupon_rate_desc", "coupon_rates", "coupon_pay_dates",
    "strong_redeem_status", "putback_status",
    "updated_at",
]

_INSERT_FIELDS = ["bond_code"] + _UPSERT_FIELDS


def upsert_bond(data: dict) -> None:
    """
    插入或更新一条债券记录（以 bond_code 为唯一键）。

    data 字段说明（均为已编码的整数/字符串，调用方负责转换）：
      bond_code        int   债券代码
      bond_name        str   债券简称
      stock_code       int   正股代码
      stock_name       str   正股简称
      conv_price       int   转股价×100
    issue_size       int   剩余规模×100
      credit_rating    int   信用评级编码（使用 encode_credit_rating 转换）
      listing_date     str   上市日期，ISO 格式 "YYYY-MM-DD HH:MM:SS" 或 None
      delist_date      str   退市日期或 None
      value_date       str   起息日或 None
      expire_date      str   到期日或 None
      redeem_clause    str   赎回条款原文
      coupon_rate_desc str   利率说明原文
      coupon_rates     str   各年票息率 JSON 字符串
      coupon_pay_dates str   付息日列表 JSON 字符串
    """
    conn = get_conn()

    # 自动填充 updated_at
    import datetime
    data = dict(data)
    data.setdefault("updated_at", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    placeholders = ", ".join(f":{f}" for f in _INSERT_FIELDS)
    # 名称字段：只在新值非空字符串时才覆盖，避免空值清空已有名称
    _NAME_FIELDS = {"bond_name", "stock_name"}
    # 详细字段 + 规模字段：单只补全（fetch_bond_detail_only）常不带这些值，
    # 用 COALESCE 在新值为 NULL 时保留旧值，避免把列表接口写好的数据冲成 NULL。
    _COALESCE_FIELDS = {
        "listing_date", "delist_date", "value_date", "expire_date",
        "redeem_clause", "coupon_rate_desc", "coupon_rates", "coupon_pay_dates",
        "issue_size", "issue_size_original", "credit_rating",
    }
    update_parts = []
    for f in _UPSERT_FIELDS:
        if f in _NAME_FIELDS:
            update_parts.append(f"{f}=CASE WHEN excluded.{f} != '' THEN excluded.{f} ELSE {f} END")
        elif f in _COALESCE_FIELDS:
            update_parts.append(f"{f}=COALESCE(excluded.{f}, {f})")
        else:
            update_parts.append(f"{f}=excluded.{f}")
    update_clause = ", ".join(update_parts)

    sql = f"""
        INSERT INTO t_bond_info ({', '.join(_INSERT_FIELDS)})
        VALUES ({placeholders})
        ON CONFLICT(bond_code) DO UPDATE SET {update_clause}
    """
    conn.execute(sql, data)
    conn.commit()


def upsert_bonds(data_list: list[dict]) -> int:
    """
    批量 upsert，返回处理条数。
    """
    conn = get_conn()

    import datetime
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 列表接口不提供的详细字段：只在新值非 NULL 时才覆盖，避免清空已有详细信息
    _DETAIL_FIELDS = {
        "listing_date", "delist_date", "value_date", "expire_date",
        "redeem_clause", "coupon_rate_desc", "coupon_rates", "coupon_pay_dates",
    }
    # 名称字段：只在新值非空字符串时才覆盖，避免已退市债券名称被空值清空
    _NAME_FIELDS = {"bond_name", "stock_name"}
    placeholders = ", ".join(f":{f}" for f in _INSERT_FIELDS)
    update_parts = []
    for f in _UPSERT_FIELDS:
        if f in _DETAIL_FIELDS:
            update_parts.append(f"{f}=COALESCE(excluded.{f}, {f})")
        elif f in _NAME_FIELDS:
            # 新值非空才覆盖，否则保留原值
            update_parts.append(f"{f}=CASE WHEN excluded.{f} != '' THEN excluded.{f} ELSE {f} END")
        else:
            update_parts.append(f"{f}=excluded.{f}")
    update_clause = ", ".join(update_parts)
    sql = f"""
        INSERT INTO t_bond_info ({', '.join(_INSERT_FIELDS)})
        VALUES ({placeholders})
        ON CONFLICT(bond_code) DO UPDATE SET {update_clause}
    """

    rows = []
    for d in data_list:
        row = dict(d)
        row.setdefault("updated_at", now)
        rows.append(row)

    conn.executemany(sql, rows)
    conn.commit()
    logger.info(f"[db] upsert_bonds: 处理 {len(rows)} 条记录")
    return len(rows)


def query_bond(bond_code: int) -> Optional[dict]:
    """
    按 bond_code 查询单条记录，不存在返回 None。
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM t_bond_info WHERE bond_code = ?", (bond_code,)
    ).fetchone()
    return dict(row) if row else None


def query_bonds(
    *,
    listing: Optional[bool] = None,
    min_rating: Optional[int] = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    """
    查询债券列表，支持简单过滤。

    参数：
      listing    True=在途（delist_date IS NULL），False=已退市，None=全部
      min_rating 最低信用评级编码（含），None=不过滤
      limit      最多返回条数
      offset     分页偏移
    """
    conn = get_conn()

    conditions = []
    params: list = []

    if listing is True:
        conditions.append("delist_date IS NULL")
    elif listing is False:
        conditions.append("delist_date IS NOT NULL")

    if min_rating is not None:
        conditions.append("credit_rating >= ?")
        params.append(min_rating)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""
        SELECT * FROM t_bond_info
        {where}
        ORDER BY bond_code
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def count_bonds() -> int:
    """返回 t_bond_info 总记录数"""
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) FROM t_bond_info").fetchone()
    return row[0] if row else 0


# ── 数据转换工具 ──────────────────────────────────────────────────────────────

import json


def bond_info_to_db_row(info: dict) -> Optional[dict]:
    """
    将 get_bond_info() 返回的字典转换为 upsert_bond() 所需的 db row。
    若 bond_code 缺失则返回 None。
    """
    bond_code_str = str(info.get("债券代码", "")).strip()
    if not bond_code_str:
        return None

    def _to_int_price(val) -> Optional[int]:
        """浮点价格 × 100 取整，None/0 返回 None"""
        try:
            v = float(val)
            return int(round(v * 100)) if v else None
        except (TypeError, ValueError):
            return None

    def _to_int_code(val) -> Optional[int]:
        try:
            return int(str(val).strip()) if val else None
        except (TypeError, ValueError):
            return None

    def _date_to_ts(val: str) -> Optional[str]:
        """将 YYYYMMDD 格式转为 'YYYY-MM-DD 00:00:00'，空则返回 None"""
        if not val:
            return None
        try:
            import datetime as _dt
            d = _dt.datetime.strptime(str(val).strip(), "%Y%m%d")
            return d.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    coupon_info = info.get("付息信息") or {}

    # 剩余规模：get_bond_info 返回的是剩余规模（亿元浮点），×100 存为整数
    issue_size = _to_int_price(info.get("剩余规模"))

    def _to_decimal(val) -> Optional[float]:
        """将浮点值转换为 DECIMAL，None/0 返回 None"""
        try:
            v = float(val)
            return round(v, 4) if v else None
        except (TypeError, ValueError):
            return None

    return {
        "bond_code":        int(bond_code_str),
        "bond_name":        str(info.get("债券简称") or ""),
        "stock_code":       _to_int_code(info.get("正股代码")),
        "stock_name":       str(info.get("正股简称") or ""),
        "conv_price":       _to_int_price(info.get("转股价")),
        "issue_size":       issue_size,
        "issue_size_original": _to_int_price(info.get("发行规模")),
        "credit_rating":    encode_credit_rating(info.get("信用评级")),
        "listing_date":     _date_to_ts(info.get("上市日期")),
        "delist_date":      _date_to_ts(info.get("退市日期")),
        "value_date":       _date_to_ts(coupon_info.get("起息日")),
        "expire_date":      _date_to_ts(coupon_info.get("到期日")),
        "redeem_clause":    coupon_info.get("赎回条款") or "",
        "coupon_rate_desc": coupon_info.get("利率说明") or "",
        "coupon_rates":     json.dumps(coupon_info.get("票息率列表") or [], ensure_ascii=False),
        "coupon_pay_dates": json.dumps(coupon_info.get("付息日列表") or [], ensure_ascii=False),
        "strong_redeem_status": info.get("强赎状态") or "",
        "putback_status":  info.get("回售状态") or "",
    }


def bond_list_row_to_db_row(row: dict) -> Optional[dict]:
    """
    将 get_all_convertible_bonds() 返回的 DataFrame 行（dict）转换为 db row。
    仅包含列表接口能提供的字段，其余字段留空。
    """
    bond_code_str = str(row.get("债券代码", "")).strip()
    if not bond_code_str:
        return None

    def _to_int_price(val) -> Optional[int]:
        try:
            v = float(val)
            return int(round(v * 100)) if v else None
        except (TypeError, ValueError):
            return None

    def _to_int_code(val) -> Optional[int]:
        try:
            return int(str(val).strip()) if val else None
        except (TypeError, ValueError):
            return None

    def _to_decimal(val) -> Optional[float]:
        """将浮点值转换为 DECIMAL，None/0 返回 None"""
        try:
            v = float(val)
            return round(v, 4) if v else None
        except (TypeError, ValueError):
            return None

    return {
        "bond_code":        int(bond_code_str),
        "bond_name":        str(row.get("债券简称") or ""),
        "stock_code":       _to_int_code(row.get("正股代码")),
        "stock_name":       str(row.get("正股简称") or ""),
        "conv_price":       _to_int_price(row.get("转股价")),
        "issue_size":       _to_int_price(row.get("剩余规模")),
        "issue_size_original": _to_int_price(row.get("发行规模")),
        "credit_rating":    encode_credit_rating(row.get("信用评级")),
        # 以下字段列表接口不提供，填 None（upsert 时不覆盖已有值）
        "listing_date":     None,
        "delist_date":      None,
        "value_date":       None,
        "expire_date":      None,
        "redeem_clause":    None,
        "coupon_rate_desc": None,
        "coupon_rates":     None,
        "coupon_pay_dates": None,
        "strong_redeem_status": row.get("强赎状态") or "",
        "putback_status":  row.get("回售状态") or "",
    }


def _parse_redeem_price(redeem_clause: str, rate_desc: str, coupon_rates: list) -> float:
    """
    从赎回条款原文和利率说明中解析到期赎回价（元）。
    优先顺序：赎回条款 → 利率说明 → 末期票息公式回退。
    """
    m = (re.search(r'面值[的]?(\d+(?:\.\d+)?)%', redeem_clause) or
         re.search(r'面值[的]?(\d+(?:\.\d+)?)%', rate_desc) or
         re.search(r'(\d+(?:\.\d+)?)元[（(]含最后', rate_desc))
    if m:
        return round(float(m.group(1)), 4)
    # 回退：100 × (1 + 末期票息率/100)
    last_rate = coupon_rates[-1] if coupon_rates else 0
    return round(100 * (1 + last_rate / 100), 4)


def db_row_to_bond_info(row: dict) -> dict:
    """
    将 t_bond_info 的 db row 还原为 get_bond_info() 格式的字典。
    """
    def _from_int_price(val) -> Optional[float]:
        return round(val / 100, 4) if val else None

    def _ts_to_date(val: Optional[str]) -> str:
        """将 'YYYY-MM-DD HH:MM:SS' 转回 'YYYYMMDD'"""
        if not val:
            return ""
        try:
            return val[:10].replace("-", "")
        except Exception:
            return ""

    coupon_rates = []
    coupon_pay_dates = []
    try:
        coupon_rates = json.loads(row.get("coupon_rates") or "[]")
    except Exception:
        pass
    try:
        coupon_pay_dates = json.loads(row.get("coupon_pay_dates") or "[]")
    except Exception:
        pass

    # 实时解析赎回价，避免缓存旧的错误解析结果
    redeem_clause = row.get("redeem_clause") or ""
    rate_desc     = row.get("coupon_rate_desc") or ""
    redeem_price  = _parse_redeem_price(redeem_clause, rate_desc, coupon_rates)

    coupon_info = {
        "起息日":     _ts_to_date(row.get("value_date")),
        "到期日":     _ts_to_date(row.get("expire_date")),
        "赎回价":     redeem_price,
        "赎回条款":   redeem_clause,
        "利率说明":   rate_desc,
        "票息率列表": coupon_rates,
        "付息日列表": coupon_pay_dates,
    }

    # 计算剩余年限
    remaining_years = None
    try:
        expire_date_str = coupon_info.get("到期日", "")
        if expire_date_str:
            from datetime import datetime as _dt
            expire_dt = _dt.strptime(expire_date_str, "%Y%m%d")
            delta_days = (expire_dt - _dt.today()).days
            remaining_years = round(max(delta_days, 0) / 365, 2)
    except Exception:
        pass

    return {
        "债券代码":   str(row.get("bond_code", "")),
        "债券简称":   row.get("bond_name") or "",
        "债现价":     None,   # 实时价格不存 DB
        "正股代码":   str(row.get("stock_code") or "").zfill(6) if row.get("stock_code") else "",
        "正股简称":   row.get("stock_name") or "",
        "正股价":     None,   # 实时价格不存 DB
        "转股溢价率": None,   # 实时数据不存 DB
        "转股价":     _from_int_price(row.get("conv_price")),
        "转股价值":   None,   # 实时计算：正股价 / 转股价 * 100
        "信用评级":   decode_credit_rating(row.get("credit_rating")),
        "剩余规模":   _from_int_price(row.get("issue_size")),
        "发行规模":   _from_int_price(row.get("issue_size_original")),
        "剩余年限":   remaining_years,
        "上市日期":   _ts_to_date(row.get("listing_date")),
        "退市日期":   _ts_to_date(row.get("delist_date")),
        "正股PB":     None,   # 高频变动字段，从 t_bond_daily 读取
        "正股市值":   None,   # 高频变动字段，从 t_bond_daily 读取
        "强赎状态":   row.get("strong_redeem_status") or "",
        "回售状态":   row.get("putback_status") or "",
        "付息信息":   coupon_info,
    }


def query_bond_updated_at(bond_code: int) -> Optional[str]:
    """返回指定 bond_code 的 updated_at 字符串，不存在返回 None"""
    conn = get_conn()
    row = conn.execute(
        "SELECT updated_at FROM t_bond_info WHERE bond_code = ?", (bond_code,)
    ).fetchone()
    return row["updated_at"] if row else None


def query_latest_updated_at() -> Optional[str]:
    """返回 t_bond_info 中最新的 updated_at，表为空时返回 None"""
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(updated_at) AS latest FROM t_bond_info"
    ).fetchone()
    return row["latest"] if row else None


# ── t_bond_daily DDL ──────────────────────────────────────────────────────────

_DDL_BOND_DAILY = """
CREATE TABLE IF NOT EXISTS t_bond_daily (
    id                   INTEGER   NOT NULL PRIMARY KEY AUTOINCREMENT,  -- 自增主键
    bond_code            INTEGER UNSIGNED NOT NULL,                     -- 债券代码（关联 t_bond_info.bond_code）
    trade_date           TIMESTAMP NOT NULL,                            -- 交易日期，格式 YYYY-MM-DD 00:00:00

    -- ── 日K线行情字段 ──────────────────────────────────────────────
    open                 INTEGER UNSIGNED,   -- 开盘价×100（元）
    high                 INTEGER UNSIGNED,   -- 最高价×100（元）
    low                  INTEGER UNSIGNED,   -- 最低价×100（元）
    close                INTEGER UNSIGNED,   -- 收盘价×100（元）
    volume               INTEGER UNSIGNED,   -- 成交量（手）
    amount               INTEGER UNSIGNED,   -- 成交额×100（元）

    -- ── 日度分析指标字段 ───────────────────────────────────────────
    conv_premium_rate    INTEGER,            -- 转股溢价率×10000（如 5.23% → 523），可为负
    ytm                  INTEGER,            -- 到期收益率×10000（如 2.10% → 210），可为负
    conv_value           INTEGER UNSIGNED,   -- 转股价值×100（元）
    double_low           INTEGER UNSIGNED,   -- 双低值×100（= 收盘价 + 溢价率×100）
    issue_size           INTEGER UNSIGNED,   -- 剩余规模×100（亿元）

    -- ── 元数据 ────────────────────────────────────────────────────
    created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (bond_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_bond_daily_bond_code  ON t_bond_daily (bond_code);
CREATE INDEX IF NOT EXISTS idx_bond_daily_trade_date ON t_bond_daily (trade_date);

-- SQLite 不支持 ON UPDATE CURRENT_TIMESTAMP，用触发器模拟
CREATE TRIGGER IF NOT EXISTS trg_bond_daily_updated_at
AFTER UPDATE ON t_bond_daily
FOR EACH ROW
BEGIN
    UPDATE t_bond_daily SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;
"""


# ── t_stock_daily DDL ─────────────────────────────────────────────────────────

_DDL_STOCK_DAILY = """
CREATE TABLE IF NOT EXISTS t_stock_daily (
    id                   INTEGER   NOT NULL PRIMARY KEY AUTOINCREMENT,  -- 自增主键
    stock_code           INTEGER UNSIGNED NOT NULL,                     -- 正股代码
    trade_date           TIMESTAMP NOT NULL,                            -- 交易日期

    stock_close          INTEGER UNSIGNED,   -- 正股收盘价×100（元）
    stock_pb             DECIMAL(10,4),      -- 正股PB
    stock_market_cap     DECIMAL(16,4),      -- 正股市值（亿元）

    created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (stock_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_stock_daily_stock_code  ON t_stock_daily (stock_code);
CREATE INDEX IF NOT EXISTS idx_stock_daily_trade_date ON t_stock_daily (trade_date);

CREATE TRIGGER IF NOT EXISTS trg_stock_daily_updated_at
AFTER UPDATE ON t_stock_daily
FOR EACH ROW
BEGIN
    UPDATE t_stock_daily SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;
"""


# ── t_bond_daily CRUD ─────────────────────────────────────────────────────────

_DAILY_UPSERT_FIELDS = [
    "open", "high", "low", "close", "volume", "amount",
    "conv_premium_rate", "ytm", "conv_value", "double_low",
    "issue_size",
    "updated_at",
]

# ── t_stock_daily CRUD ────────────────────────────────────────────────────────

_STOCK_DAILY_UPSERT_FIELDS = [
    "stock_close", "stock_pb", "stock_market_cap",
    "updated_at",
]
_STOCK_DAILY_INSERT_FIELDS = ["stock_code", "trade_date"] + _STOCK_DAILY_UPSERT_FIELDS
_DAILY_INSERT_FIELDS = ["bond_code", "trade_date"] + _DAILY_UPSERT_FIELDS


def upsert_daily(data: dict) -> None:
    """
    插入或更新一条日线记录（以 bond_code + trade_date 为唯一键）。

    data 字段说明（调用方负责转换为整数编码）：
      bond_code          int   债券代码
      trade_date         str   交易日期，格式 'YYYY-MM-DD 00:00:00'
      open               int   开盘价×100
      high               int   最高价×100
      low                int   最低价×100
      close              int   收盘价×100
      volume             int   成交量（手）
      amount             int   成交额×100
      conv_premium_rate  int   转股溢价率×10000，可为负
      ytm                int   到期收益率×10000，可为负
      conv_value         int   转股价值×100
      double_low         int   双低值×100
    """
    import datetime
    conn = get_conn()
    data = dict(data)
    data["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    placeholders = ", ".join(f":{f}" for f in _DAILY_INSERT_FIELDS)
    update_clause = ", ".join(f"{f}=excluded.{f}" for f in _DAILY_UPSERT_FIELDS)
    sql = f"""
        INSERT INTO t_bond_daily ({', '.join(_DAILY_INSERT_FIELDS)})
        VALUES ({placeholders})
        ON CONFLICT(bond_code, trade_date) DO UPDATE SET {update_clause}
    """
    conn.execute(sql, data)
    conn.commit()


def upsert_daily_batch(data_list: list[dict]) -> int:
    """
    批量 upsert 日线记录，返回处理条数。
    """
    import datetime
    conn = get_conn()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    placeholders = ", ".join(f":{f}" for f in _DAILY_INSERT_FIELDS)
    update_clause = ", ".join(f"{f}=excluded.{f}" for f in _DAILY_UPSERT_FIELDS)
    sql = f"""
        INSERT INTO t_bond_daily ({', '.join(_DAILY_INSERT_FIELDS)})
        VALUES ({placeholders})
        ON CONFLICT(bond_code, trade_date) DO UPDATE SET {update_clause}
    """

    rows = []
    for d in data_list:
        row = dict(d)
        row["updated_at"] = now  # 批量时统一用同一时间戳
        rows.append(row)

    conn.executemany(sql, rows)
    conn.commit()
    logger.info(f"[db] upsert_daily_batch: 处理 {len(rows)} 条记录")
    return len(rows)


def query_daily(
    bond_code: int,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict]:
    """
    查询指定债券的日线记录，按 trade_date 升序排列。

    参数：
      bond_code   债券代码
      start_date  起始日期，格式 'YYYY-MM-DD 00:00:00' 或 'YYYY-MM-DD'，None=不限
      end_date    结束日期，同上，None=不限
      limit       最多返回条数
      offset      分页偏移
    """
    conn = get_conn()
    conditions = ["bond_code = ?"]
    params: list = [bond_code]

    if start_date:
        conditions.append("trade_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("trade_date <= ?")
        params.append(end_date)

    where = "WHERE " + " AND ".join(conditions)
    sql = f"""
        SELECT * FROM t_bond_daily
        {where}
        ORDER BY trade_date ASC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_daily_by_date(
    trade_date: str,
    *,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict]:
    """
    查询指定交易日的全市场日线截面数据。

    参数：
      trade_date  交易日期，格式 'YYYY-MM-DD 00:00:00' 或 'YYYY-MM-DD'
    """
    conn = get_conn()
    sql = """
        SELECT * FROM t_bond_daily
        WHERE trade_date = ?
        ORDER BY bond_code
        LIMIT ? OFFSET ?
    """
    rows = conn.execute(sql, (trade_date, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def query_daily_latest_date(bond_code: int) -> Optional[str]:
    """返回指定债券最新的 trade_date，无记录返回 None"""
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(trade_date) AS latest FROM t_bond_daily WHERE bond_code = ?",
        (bond_code,)
    ).fetchone()
    return row["latest"] if row else None


def count_daily(bond_code: Optional[int] = None) -> int:
    """返回 t_bond_daily 记录数，指定 bond_code 则只统计该债券"""
    conn = get_conn()
    if bond_code is not None:
        row = conn.execute(
            "SELECT COUNT(*) FROM t_bond_daily WHERE bond_code = ?", (bond_code,)
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM t_bond_daily").fetchone()
    return row[0] if row else 0


# ── t_stock_daily CRUD ────────────────────────────────────────────────────────

def upsert_stock_daily(data: dict) -> None:
    """插入或更新一条正股日线记录（以 stock_code + trade_date 为唯一键）。"""
    import datetime
    conn = get_conn()
    data = dict(data)
    data["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    placeholders = ", ".join(f":{f}" for f in _STOCK_DAILY_INSERT_FIELDS)
    update_clause = ", ".join(f"{f}=excluded.{f}" for f in _STOCK_DAILY_UPSERT_FIELDS)
    sql = f"""
        INSERT INTO t_stock_daily ({', '.join(_STOCK_DAILY_INSERT_FIELDS)})
        VALUES ({placeholders})
        ON CONFLICT(stock_code, trade_date) DO UPDATE SET {update_clause}
    """
    conn.execute(sql, data)
    conn.commit()


def upsert_stock_daily_batch(data_list: list[dict]) -> int:
    """批量 upsert 正股日线记录，返回处理条数。"""
    import datetime
    conn = get_conn()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    placeholders = ", ".join(f":{f}" for f in _STOCK_DAILY_INSERT_FIELDS)
    update_clause = ", ".join(f"{f}=excluded.{f}" for f in _STOCK_DAILY_UPSERT_FIELDS)
    sql = f"""
        INSERT INTO t_stock_daily ({', '.join(_STOCK_DAILY_INSERT_FIELDS)})
        VALUES ({placeholders})
        ON CONFLICT(stock_code, trade_date) DO UPDATE SET {update_clause}
    """

    rows = []
    for d in data_list:
        row = dict(d)
        row["updated_at"] = now
        rows.append(row)

    conn.executemany(sql, rows)
    conn.commit()
    logger.info(f"[db] upsert_stock_daily_batch: 处理 {len(rows)} 条记录")
    return len(rows)


def query_latest_stock_snapshot() -> dict[int, dict]:
    """
    查询每只正股的最新日度数据（以 trade_date 最新为准）。
    返回: {stock_code: {stock_pb, stock_market_cap, stock_close, ...}}
    """
    conn = get_conn()
    sql = """
        SELECT s.*
        FROM t_stock_daily s
        INNER JOIN (
            SELECT stock_code, MAX(trade_date) AS max_date
            FROM t_stock_daily
            GROUP BY stock_code
        ) latest ON s.stock_code = latest.stock_code AND s.trade_date = latest.max_date
    """
    rows = conn.execute(sql).fetchall()
    result = {}
    for r in rows:
        result[r["stock_code"]] = dict(r)
    return result


def query_latest_daily_snapshot() -> dict[int, dict]:
    """
    查询每只债券的最新日度数据（以 trade_date 最新为准）。
    返回: {bond_code: {stock_pb, stock_market_cap, ...}}
    """
    conn = get_conn()
    sql = """
        SELECT d.*
        FROM t_bond_daily d
        INNER JOIN (
            SELECT bond_code, MAX(trade_date) AS max_date
            FROM t_bond_daily
            GROUP BY bond_code
        ) latest ON d.bond_code = latest.bond_code AND d.trade_date = latest.max_date
    """
    rows = conn.execute(sql).fetchall()
    result = {}
    for r in rows:
        result[r["bond_code"]] = dict(r)
    return result


def history_df_to_daily_rows(bond_code: str, df) -> list:
    """
    将 get_convertible_bond_history() 返回的 DataFrame 转换为
    upsert_daily_batch() 所需的 dict list。

    DataFrame 预期列（部分可选）：
      日期、开盘价、最高价、最低价、收盘价、成交量
      转股溢价率、到期收益率、转股价值、剩余规模、正股收盘价

    bond_code: 纯数字字符串，如 "113050"
    """
    import math as _math

    def _to_int_safe(val, multiplier: int = 100, signed: bool = False) -> Optional[int]:
        """浮点值 × multiplier 取整；None/NaN/Inf 返回 None；负值若 signed=False 则取 None"""
        if val is None:
            return None
        try:
            v = float(val)
            if _math.isnan(v) or _math.isinf(v):
                return None
            result = int(round(v * multiplier))
            if not signed and result < 0:
                return None
            return result
        except (TypeError, ValueError):
            return None

    pure_code = int(bond_code.replace("sh", "").replace("sz", "").strip())
    rows = []
    for _, r in df.iterrows():
        date_str = str(r.get("日期", "")).strip()
        if not date_str or len(date_str) != 8:
            continue
        trade_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} 00:00:00"

        close_raw   = _to_int_safe(r.get("收盘价"), 100)
        premium_raw = _to_int_safe(r.get("转股溢价率"), 10000, signed=True)

        # double_low = (收盘价 + 溢价率%) × 100
        # 即 close/100 + premium/10000*100 全部再×100
        # = close + premium/100
        double_low: Optional[int] = None
        if close_raw is not None and premium_raw is not None:
            double_low = close_raw + int(round(premium_raw / 100))

        rows.append({
            "bond_code":         pure_code,
            "trade_date":        trade_date,
            "open":              _to_int_safe(r.get("开盘价"), 100),
            "high":              _to_int_safe(r.get("最高价"), 100),
            "low":               _to_int_safe(r.get("最低价"), 100),
            "close":             close_raw,
            "volume":            _to_int_safe(r.get("成交量"), 1),
            "amount":            _to_int_safe(r.get("成交量"), 1),
            "conv_premium_rate": premium_raw,
            "ytm":               _to_int_safe(r.get("到期收益率"), 10000, signed=True),
            "conv_value":        _to_int_safe(r.get("转股价值"), 100),
            "double_low":        double_low,
            "issue_size":        _to_int_safe(r.get("剩余规模"), 100),
        })
    return rows


# ── 正股财务 CRUD ──────────────────────────────────────────────────────────────

def upsert_stock_financials(stock_code: str, profile_json: str, annual_json: str, quarterly_json: str) -> None:
    """
    插入或更新正股财务数据（以 stock_code 为唯一键）。
    """
    import datetime as _dt
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    conn.execute("""
        INSERT INTO t_stock_financials (stock_code, profile_json, annual_json, quarterly_json, updated_at)
        VALUES (:stock_code, :profile_json, :annual_json, :quarterly_json, :updated_at)
        ON CONFLICT(stock_code) DO UPDATE SET
            profile_json   = excluded.profile_json,
            annual_json    = excluded.annual_json,
            quarterly_json = excluded.quarterly_json,
            updated_at     = excluded.updated_at
    """, {
        "stock_code":    stock_code,
        "profile_json":  profile_json,
        "annual_json":   annual_json,
        "quarterly_json": quarterly_json,
        "updated_at":    now,
    })
    conn.commit()


def query_stock_financials(stock_code: str) -> Optional[dict]:
    """
    查询正股财务缓存，返回 {profile_json, annual_json, quarterly_json, updated_at} 或 None。
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT profile_json, annual_json, quarterly_json, updated_at "
        "FROM t_stock_financials WHERE stock_code = ?",
        (stock_code,)
    ).fetchone()
    if row is None:
        return None
    return dict(row)


# ── t_user_bond DDL（持仓 + 笔记）──────────────────────────────────────────────

_DDL_USER_BOND = """
CREATE TABLE IF NOT EXISTS t_user_bond (
    id            INTEGER   NOT NULL PRIMARY KEY AUTOINCREMENT,
    bond_code     INTEGER UNSIGNED NOT NULL UNIQUE,
    bond_name     VARCHAR(20),
    cost_price    DECIMAL(10,4),
    quantity      INTEGER UNSIGNED,
    note_content  TEXT,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS trg_user_bond_updated_at
AFTER UPDATE ON t_user_bond
FOR EACH ROW
BEGIN
    UPDATE t_user_bond SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;
"""

# ── t_alert DDL ───────────────────────────────────────────────────────────────

_DDL_ALERT = """
CREATE TABLE IF NOT EXISTS t_alert (
    id            INTEGER   NOT NULL PRIMARY KEY AUTOINCREMENT,
    bond_code     INTEGER UNSIGNED NOT NULL,
    alert_type    VARCHAR(20) NOT NULL,
    operator      VARCHAR(10) NOT NULL,
    threshold     DECIMAL(10,4) NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS trg_alert_updated_at
AFTER UPDATE ON t_alert
FOR EACH ROW
BEGIN
    UPDATE t_alert SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;
"""

# ── t_user_bond CRUD（持仓）────────────────────────────────────────────────────

def upsert_position(bond_code: int, bond_name: str, cost_price: float, quantity: int) -> None:
    import datetime as _dt
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_user_conn()
    conn.execute("""
        INSERT INTO t_user_bond (bond_code, bond_name, cost_price, quantity, updated_at)
        VALUES (:bond_code, :bond_name, :cost_price, :quantity, :updated_at)
        ON CONFLICT(bond_code) DO UPDATE SET
            bond_name  = excluded.bond_name,
            cost_price = excluded.cost_price,
            quantity   = excluded.quantity,
            updated_at = excluded.updated_at
    """, {"bond_code": bond_code, "bond_name": bond_name, "cost_price": cost_price,
          "quantity": quantity, "updated_at": now})
    conn.commit()


def delete_position(bond_code: int) -> None:
    conn = get_user_conn()
    # 无笔记则删整条，有笔记则只清持仓字段
    conn.execute("""
        DELETE FROM t_user_bond
        WHERE bond_code = ? AND (note_content IS NULL OR note_content = '')
    """, (bond_code,))
    conn.execute("""
        UPDATE t_user_bond
        SET cost_price = NULL, quantity = NULL, bond_name = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE bond_code = ?
    """, (bond_code,))
    conn.commit()


def query_positions() -> list:
    conn = get_user_conn()
    rows = conn.execute(
        "SELECT * FROM t_user_bond WHERE cost_price IS NOT NULL ORDER BY bond_code"
    ).fetchall()
    return [dict(r) for r in rows]


# ── t_alert CRUD ──────────────────────────────────────────────────────────────

def add_alert(bond_code: int, alert_type: str, operator: str, threshold: float) -> int:
    import datetime as _dt
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_user_conn()
    cur = conn.execute("""
        INSERT INTO t_alert (bond_code, alert_type, operator, threshold, enabled, updated_at)
        VALUES (:bond_code, :alert_type, :operator, :threshold, 1, :updated_at)
    """, {"bond_code": bond_code, "alert_type": alert_type, "operator": operator,
          "threshold": threshold, "updated_at": now})
    conn.commit()
    return cur.lastrowid


def delete_alert(alert_id: int) -> None:
    conn = get_user_conn()
    conn.execute("DELETE FROM t_alert WHERE id = ?", (alert_id,))
    conn.commit()


def query_alerts(bond_code: int = None) -> list:
    conn = get_user_conn()
    if bond_code is not None:
        rows = conn.execute("SELECT * FROM t_alert WHERE bond_code = ? ORDER BY created_at", (bond_code,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM t_alert ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


# ── t_user_bond CRUD（笔记）────────────────────────────────────────────────────

def upsert_note(bond_code: int, content: str) -> None:
    import datetime as _dt
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_user_conn()
    conn.execute("""
        INSERT INTO t_user_bond (bond_code, note_content, updated_at)
        VALUES (:bond_code, :content, :updated_at)
        ON CONFLICT(bond_code) DO UPDATE SET
            note_content = excluded.note_content,
            updated_at   = excluded.updated_at
    """, {"bond_code": bond_code, "content": content, "updated_at": now})
    conn.commit()


def delete_note(bond_code: int) -> None:
    conn = get_user_conn()
    # 无持仓则删整条，有持仓则只清笔记字段
    conn.execute("""
        DELETE FROM t_user_bond
        WHERE bond_code = ? AND (cost_price IS NULL AND quantity IS NULL)
    """, (bond_code,))
    conn.execute("""
        UPDATE t_user_bond
        SET note_content = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE bond_code = ?
    """, (bond_code,))
    conn.commit()


def query_note(bond_code: int):
    conn = get_user_conn()
    row = conn.execute(
        "SELECT bond_code, note_content AS content, created_at, updated_at "
        "FROM t_user_bond WHERE bond_code = ?",
        (bond_code,)
    ).fetchone()
    return dict(row) if row else None


def update_bond_region(bond_code: int, region: str) -> None:
    """更新指定债券的地区字段（不影响 updated_at）"""
    conn = get_conn()
    conn.execute(
        "UPDATE t_bond_info SET region = ? WHERE bond_code = ?",
        (region, bond_code),
    )
    conn.commit()
