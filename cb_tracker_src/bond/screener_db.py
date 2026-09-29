"""
选股数据缓存数据库（screener.db）。

存储全量 A 股 / 港股的 PE、PB、市值、净资产、行业分类数据，
避免每次运行策略都实时拉取接口（A 股约 5000 只 × 行业查询需 30-60 秒）。

更新触发时机：
  - 应用首次启动且 DB 为空 → 自动后台更新
  - 用户手动点击"更新"按钮 → 触发 /api/stock_screener/update
"""
import os
import sqlite3
import threading
import logging
from typing import Optional

logger = logging.getLogger("bond_history")

_SCREENER_DB_FILENAME = "screener.db"
_screener_conn: Optional[sqlite3.Connection] = None
_screener_lock = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS t_stock_cache (
    market       TEXT    NOT NULL,
    code         TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    pe           REAL,
    pb           REAL,
    market_cap   REAL,
    net_assets   REAL,
    price        REAL,
    industry     TEXT    DEFAULT '',
    PRIMARY KEY (market, code)
);

CREATE TABLE IF NOT EXISTS t_screener_meta (
    market       TEXT    PRIMARY KEY,
    last_update  TEXT,
    stock_count  INTEGER DEFAULT 0,
    updating     INTEGER DEFAULT 0
);
"""


def init_screener_db(db_dir: str) -> None:
    """初始化选股缓存数据库（应用启动时调用一次）"""
    global _screener_conn
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, _SCREENER_DB_FILENAME)
    _screener_conn = sqlite3.connect(db_path, check_same_thread=False)
    _screener_conn.row_factory = sqlite3.Row
    _screener_conn.execute("PRAGMA journal_mode=WAL")
    _screener_conn.executescript(_DDL)
    # 在线迁移：老库补 price 列（幂等）
    _migrate_add_price_col()
    logger.info("[screener_db] 数据库已初始化：%s", db_path)


def _migrate_add_price_col() -> None:
    """老库可能没有 price 列，检测并补充（幂等，可重复调用）"""
    try:
        cols = [r[1] for r in _conn().execute("PRAGMA table_info(t_stock_cache)").fetchall()]
        if cols and "price" not in cols:
            _conn().execute("ALTER TABLE t_stock_cache ADD COLUMN price REAL")
            _conn().commit()
            logger.info("[screener_db] 迁移：t_stock_cache 补充 price 列")
    except Exception as e:
        logger.warning("[screener_db] 迁移 price 列失败：%s", e)


def _conn() -> sqlite3.Connection:
    if _screener_conn is None:
        raise RuntimeError("screener_db 未初始化，请先调用 init_screener_db()")
    return _screener_conn


def get_all_stocks(market: str) -> list:
    """从 DB 读取指定市场的全量股票数据，返回 dict 列表"""
    with _screener_lock:
        rows = _conn().execute(
            "SELECT code, name, pe, pb, market_cap, net_assets, price, industry "
            "FROM t_stock_cache WHERE market=? ORDER BY code",
            (market,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_industries(market: str) -> list:
    """返回指定市场已缓存的非空行业名称，按名称排序。"""
    with _screener_lock:
        rows = _conn().execute(
            "SELECT DISTINCT TRIM(industry) AS industry "
            "FROM t_stock_cache "
            "WHERE market=? AND industry IS NOT NULL AND TRIM(industry)<>'' "
            "ORDER BY industry",
            (market,),
        ).fetchall()
    return [row["industry"] for row in rows]


def save_stocks(market: str, stocks: list) -> None:
    """覆盖写入指定市场的全量股票数据，同时更新 meta"""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _screener_lock:
        conn = _conn()
        # 删除旧快照、写入新快照和更新元数据必须原子完成，避免更新中断后留下空库。
        with conn:
            conn.execute("DELETE FROM t_stock_cache WHERE market=?", (market,))
            conn.executemany(
                "INSERT INTO t_stock_cache (market, code, name, pe, pb, market_cap, net_assets, price, industry) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(market, s.get("code", ""), s.get("name", ""), s.get("pe"),
                  s.get("pb"), s.get("market_cap"), s.get("net_assets"), s.get("price"), s.get("industry", ""))
                 for s in stocks]
            )
            conn.execute(
                "INSERT OR REPLACE INTO t_screener_meta (market, last_update, stock_count, updating) "
                "VALUES (?, ?, ?, 0)",
                (market, now, len(stocks))
            )
    logger.info("[screener_db] 已写入 %s 股票 %d 只", market, len(stocks))


def get_meta(market: str) -> dict:
    """返回 {last_update, stock_count, updating}，若无记录则返回默认空值"""
    with _screener_lock:
        row = _conn().execute(
            "SELECT last_update, stock_count, updating FROM t_screener_meta WHERE market=?",
            (market,)
        ).fetchone()
    if row:
        return {"last_update": row["last_update"], "stock_count": row["stock_count"],
                "updating": bool(row["updating"])}
    return {"last_update": None, "stock_count": 0, "updating": False}


def set_updating(market: str, flag: bool) -> None:
    """标记某市场正在更新中（用于前端轮询状态）"""
    with _screener_lock:
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO t_screener_meta (market, last_update, stock_count, updating) "
            "VALUES (?, "
            "COALESCE((SELECT last_update FROM t_screener_meta WHERE market=?), NULL), "
            "COALESCE((SELECT stock_count FROM t_screener_meta WHERE market=?), 0), ?)",
            (market, market, market, 1 if flag else 0)
        )
        conn.commit()
