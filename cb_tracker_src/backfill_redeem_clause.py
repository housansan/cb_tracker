"""
backfill_redeem_clause.py — 批量回填 t_bond_info.redeem_clause

对所有 redeem_clause 为空的债券，重新调用 fetch_bond_detail_only() 获取赎回条款，
并通过 upsert_bond() + bond_info_to_db_row() 写回数据库。

运行方式（在项目根目录或 cb_tracker_src 目录下均可）：
    python cb_tracker_src/backfill_redeem_clause.py
    python backfill_redeem_clause.py  # 若已 cd 进 cb_tracker_src

选项（环境变量）：
    SLEEP=0.5          每次请求间隔秒数（默认 0.5）
    ACTIVE_ONLY=1      只回填在市债券（默认 0，即全量）
    DRY_RUN=1          只打印不写库（默认 0）
"""

import os
import sys
import time
import logging

# ── 路径补丁，确保能找到 bond/config 等模块 ──────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── 日志配置 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill")

# ── 导入业务模块 ──────────────────────────────────────────────────────────────
from config import DB_CONFIG
from bond.db import init_db, get_conn, upsert_bond, bond_info_to_db_row
from bond.history import fetch_bond_detail_only

# ── 环境变量参数 ─────────────────────────────────────────────────────────────
SLEEP       = float(os.getenv("SLEEP",       "0.5"))
ACTIVE_ONLY = os.getenv("ACTIVE_ONLY", "0") == "1"
DRY_RUN     = os.getenv("DRY_RUN",     "0") == "1"


def fetch_bond_codes() -> list[str]:
    """
    从 DB 读取需要回填的债券代码列表。
    排序：在市债券优先（delist_date IS NULL），其次按 bond_code 升序。
    """
    conn = get_conn()
    if ACTIVE_ONLY:
        rows = conn.execute(
            "SELECT bond_code FROM t_bond_info "
            "WHERE (redeem_clause IS NULL OR length(redeem_clause) = 0) "
            "AND delist_date IS NULL "
            "ORDER BY bond_code"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT bond_code FROM t_bond_info "
            "WHERE (redeem_clause IS NULL OR length(redeem_clause) = 0) "
            "ORDER BY delist_date IS NULL DESC, bond_code"
        ).fetchall()
    return [str(r["bond_code"]).zfill(6) for r in rows]


def backfill_one(bond_code: str) -> bool:
    """
    回填单只债券的 redeem_clause。
    :return: True = 成功写入（或 dry-run 跳过），False = 失败
    """
    info = fetch_bond_detail_only(bond_code)
    if not info:
        logger.warning("[%s] fetch_bond_detail_only 返回空", bond_code)
        return False

    coupon_info   = info.get("付息信息") or {}
    redeem_clause = coupon_info.get("赎回条款", "")

    if DRY_RUN:
        logger.info("[%s] DRY_RUN redeem_clause=%r", bond_code, redeem_clause[:80] if redeem_clause else "")
        return True

    db_row = bond_info_to_db_row(info)
    if not db_row:
        logger.warning("[%s] bond_info_to_db_row 返回 None", bond_code)
        return False

    upsert_bond(db_row)
    logger.info("[%s] ✓ redeem_clause=%r", bond_code,
                (redeem_clause[:60] + "…") if len(redeem_clause) > 60 else redeem_clause)
    return True


def main():
    logger.info("初始化数据库 dir=%s", DB_CONFIG["dir"])
    init_db(DB_CONFIG["dir"])

    codes = fetch_bond_codes()
    total = len(codes)
    logger.info("共需回填 %d 条（ACTIVE_ONLY=%s DRY_RUN=%s SLEEP=%.1fs）",
                total, ACTIVE_ONLY, DRY_RUN, SLEEP)

    if total == 0:
        logger.info("无需回填，退出。")
        return

    ok = fail = 0
    for i, code in enumerate(codes, 1):
        try:
            success = backfill_one(code)
        except Exception as e:
            logger.error("[%s] 异常: %s", code, e)
            success = False

        if success:
            ok += 1
        else:
            fail += 1

        if i % 50 == 0 or i == total:
            logger.info("进度 %d/%d  成功=%d  失败=%d", i, total, ok, fail)

        if i < total:
            time.sleep(SLEEP)

    logger.info("回填完成。总计 %d  成功 %d  失败 %d", total, ok, fail)


if __name__ == "__main__":
    main()
