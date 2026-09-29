"""
LOF 赎回费率数据库（独立 lof.db）。

赎回费率是固定数据（基金合同约定，极少变动），因此持久化存储，
避免每次查询都实时拉取（382 只逐个拉需 ~7 分钟且易被限流）。

核心用途：找出「持有较短天数即可免赎回费」的 LOF，用于折价套利。
不同基金差异大——有的持有满 30 天赎回费即降为 0，有的需满 2 年。
"""
import os
import re
import json
import sqlite3
import logging
from typing import Optional

logger = logging.getLogger("bond_history")

_LOF_DB_FILENAME = "lof.db"
_lof_conn: Optional[sqlite3.Connection] = None

_DDL_LOF_FEE = """
CREATE TABLE IF NOT EXISTS t_lof_fee (
    code        VARCHAR(10)  NOT NULL PRIMARY KEY,   -- 基金代码（6位）
    fund_name   VARCHAR(40),                          -- 基金简称
    fee_tiers   TEXT,                                 -- 完整赎回费分档 JSON: [{min_day, max_day, fee}]
    free_days   INTEGER,                              -- 赎回费降为 0 所需最小持有天数，NULL=无免费档
    short_fee   REAL,                                 -- 短线(<7天)赎回费率（%）
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def init_lof_db(db_dir: str) -> None:
    """初始化 LOF 数据库（应用启动时调用一次）"""
    global _lof_conn
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, _LOF_DB_FILENAME)
    _lof_conn = sqlite3.connect(db_path, check_same_thread=False)
    _lof_conn.row_factory = sqlite3.Row
    _lof_conn.execute("PRAGMA journal_mode=WAL")
    _lof_conn.executescript(_DDL_LOF_FEE)
    logger.info("[lof_db] LOF 数据库已初始化：%s", db_path)


def get_lof_conn() -> sqlite3.Connection:
    if _lof_conn is None:
        raise RuntimeError("LOF 数据库未初始化，请先调用 init_lof_db()")
    return _lof_conn


# ── 赎回费率解析 ──────────────────────────────────────────────────────────────

def _term_to_days(text: str) -> int:
    """
    将期限文本转为天数，'年' 按 365 天折算。
      '7天' → 7   '1年' → 365   '2年' → 730   '180天' → 180
    """
    text = text.strip()
    m = re.search(r'(\d+(?:\.\d+)?)\s*年', text)
    if m:
        return int(round(float(m.group(1)) * 365))
    m = re.search(r'(\d+(?:\.\d+)?)\s*天', text)
    if m:
        return int(round(float(m.group(1))))
    return 0


def parse_fee_tiers(df) -> list:
    """
    将 ak.fund_fee_em(indicator='赎回费率') 返回的 DataFrame 解析为结构化分档。

    '适用期限' 文本格式：
      '小于7天'                 → (0, 7)
      '大于等于7天，小于30天'     → (7, 30)
      '大于等于365天，小于730天'  → (365, 730)
      '大于等于730天'            → (730, None)  # 无上限
      '大于1年，小于等于2年'       → (365, 730)  # 兼容 大于/小于等于 变体
      '大于2年'                 → (730, None)
    '赎回费率' 形如 '1.50%'。

    解析不依赖具体比较词（大于/大于等于/小于/小于等于），
    而是按「区间内出现的数字边界个数 + 大于/小于语义」推断，避免边界文本变体导致误判。

    :return: list[dict]，每档 {"min_day": int|None, "max_day": int|None, "fee": float, "term": str}
             term 保留原始期限文本；无法量化为天数的（如“持有满一个封闭期”）min_day/max_day 为 None。
    """
    tiers = []
    if df is None or df.empty:
        return tiers
    for _, r in df.iterrows():
        term = str(r.get("适用期限") or "").strip()
        fee_raw = str(r.get("赎回费率") or "").replace("%", "").strip()
        try:
            fee = float(fee_raw)
        except (TypeError, ValueError):
            continue

        min_day, max_day = _parse_term_range(term)
        tiers.append({"min_day": min_day, "max_day": max_day, "fee": fee, "term": term})
    # 兜底：仅对「可量化天数」的档位排序校正；不可量化档（min_day=None）保持原序不参与。
    quant = [t for t in tiers if t["min_day"] is not None]
    quant.sort(key=lambda t: (t["min_day"], t["max_day"] if t["max_day"] is not None else 10 ** 9))
    for i in range(1, len(quant)):
        prev, cur = quant[i - 1], quant[i]
        # 当前档下界为 0 却不是首档时，用上一档上界补齐（修复个别边界缺失）
        if cur["min_day"] == 0 and prev.get("max_day"):
            cur["min_day"] = prev["max_day"]
    return tiers


def _parse_term_range(term: str) -> tuple:
    """
    将单个「适用期限」文本解析为 (min_day, max_day)。
    规则（不依赖精确比较词）：
      - 抽取文本中所有「数字+天/年」边界，按出现顺序转成天数
      - 两个边界 → (小, 大)
      - 一个边界 + 含“小于” → (0, 边界)
      - 一个边界 + 含“大于” → (边界, None)
      - 无数字边界（如“持有满一个封闭期”）→ (None, None) 表示无法量化
    """
    # 依次抽取形如 “7天” “1年” 的边界（保留顺序）
    bounds = [_term_to_days(m.group(0)) for m in re.finditer(r'\d+(?:\.\d+)?\s*[天年]', term)]
    if len(bounds) >= 2:
        lo, hi = bounds[0], bounds[1]
        return (min(lo, hi), max(lo, hi))
    if len(bounds) == 1:
        b = bounds[0]
        if "小于" in term and "大于" not in term:
            return (0, b)
        if "大于" in term:
            return (b, None)
        # 无比较词，无法判定方向，保守当作下界
        return (b, None)
    # 无任何数字边界（如“持有满一个封闭期”）→ 无法量化
    return (None, None)


def compute_free_days(tiers: list) -> Optional[int]:
    """
    从分档计算「赎回费降为 0 所需的最小持有天数」。
    取第一个 fee==0 且可量化(min_day 非 None) 档位的 min_day；
    若无 0 费档、或 0 费档无法量化天数（如封闭期），返回 None。
    """
    zero_tiers = [t for t in tiers if t["fee"] == 0 and t.get("min_day") is not None]
    if not zero_tiers:
        return None
    return min(t["min_day"] for t in zero_tiers)


def compute_short_fee(tiers: list) -> Optional[float]:
    """短线(<7天/最短持有)赎回费率：取 min_day 最小的档位费率。
    含不可量化档（min_day=None）时，视为最短档（下界 0）参与比较。"""
    if not tiers:
        return None
    return min(tiers, key=lambda t: (t["min_day"] if t.get("min_day") is not None else 0))["fee"]


# ── CRUD ──────────────────────────────────────────────────────────────────────

def upsert_lof_fee(code: str, fund_name: str, tiers: list) -> None:
    """插入/更新一只 LOF 的赎回费数据"""
    import datetime as _dt
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_lof_conn()
    conn.execute("""
        INSERT INTO t_lof_fee (code, fund_name, fee_tiers, free_days, short_fee, updated_at)
        VALUES (:code, :fund_name, :fee_tiers, :free_days, :short_fee, :updated_at)
        ON CONFLICT(code) DO UPDATE SET
            fund_name  = excluded.fund_name,
            fee_tiers  = excluded.fee_tiers,
            free_days  = excluded.free_days,
            short_fee  = excluded.short_fee,
            updated_at = excluded.updated_at
    """, {
        "code": code,
        "fund_name": fund_name,
        "fee_tiers": json.dumps(tiers, ensure_ascii=False),
        "free_days": compute_free_days(tiers),
        "short_fee": compute_short_fee(tiers),
        "updated_at": now,
    })
    conn.commit()


def query_all_lof_fees() -> dict:
    """返回全部赎回费数据 {code: {fee_tiers, free_days, short_fee}}"""
    conn = get_lof_conn()
    rows = conn.execute(
        "SELECT code, fee_tiers, free_days, short_fee FROM t_lof_fee"
    ).fetchall()
    result = {}
    for r in rows:
        tiers = []
        try:
            tiers = json.loads(r["fee_tiers"] or "[]")
        except Exception:
            pass
        result[r["code"]] = {
            "fee_tiers": tiers,
            "free_days": r["free_days"],
            "short_fee": r["short_fee"],
        }
    return result


def query_existing_lof_codes() -> set:
    """返回已入库的代码集合（供 backfill 断点续跑跳过）"""
    conn = get_lof_conn()
    rows = conn.execute("SELECT code FROM t_lof_fee").fetchall()
    return {r["code"] for r in rows}
