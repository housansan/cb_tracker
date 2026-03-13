import re
import math
import logging

import akshare as ak
import pandas as pd

logger = logging.getLogger("bond_history")


def parse_coupon_rates(interest_rate_explain: str) -> list:
    """
    解析利率说明文本，返回各年票息率列表（百分比，如 [0.20, 0.40, 0.70, 1.20, 1.70, 2.00]）。
    支持格式：'第一年0.20%、第二年0.40%...' 或 '第1年0.20%、第2年0.40%...'
    """
    rates = re.findall(r'(\d+(?:\.\d+)?)%', interest_rate_explain)
    return [float(r) for r in rates]


def calc_ytm(price: float, cashflows: list, times: list) -> float:
    """
    用二分法计算到期收益率（年化，%）。
    cashflows: 各期现金流（元），times: 各期距今年数（浮点）
    """
    def npv(r):
        if r <= -1:
            return float('inf')
        return sum(cf / (1 + r) ** t for cf, t in zip(cashflows, times)) - price

    lo, hi = -0.9999, 10.0
    if npv(lo) * npv(hi) > 0:
        return float('nan')
    for _ in range(100):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-8:
            break
    return round((lo + hi) / 2 * 100, 4)


def calc_ytm_series(df: pd.DataFrame, bond_code: str) -> pd.Series:
    """
    为历史数据 DataFrame 的每一行计算到期收益率（%）。
    需要债券的票息结构、到期日、到期赎回价等信息。

    :param df: 历史数据 DataFrame，必须含 '日期'（YYYYMMDD）和 '收盘价' 列
    :param bond_code: 纯数字代码
    :return: 与 df 等长的 Series，值为 YTM（%），无法计算时为 NaN
    """
    try:
        info_df = ak.bond_zh_cov_info(symbol=bond_code)
        if info_df.empty:
            return pd.Series([float('nan')] * len(df))
        info = info_df.iloc[0]

        # 解析票息率
        rate_explain = str(info.get("INTEREST_RATE_EXPLAIN", ""))
        coupon_rates = parse_coupon_rates(rate_explain)
        if not coupon_rates:
            return pd.Series([float('nan')] * len(df))

        # 起息日 & 到期日
        value_date  = pd.to_datetime(info.get("VALUE_DATE"))
        expire_date = pd.to_datetime(info.get("EXPIRE_DATE"))
        if pd.isna(value_date) or pd.isna(expire_date):
            return pd.Series([float('nan')] * len(df))

        # 到期赎回价（从 REDEEM_CLAUSE 中提取赎回比例，如 "107%"）
        redeem_clause = str(info.get("REDEEM_CLAUSE", ""))
        m = re.search(r'面值的(\d+(?:\.\d+)?)%', redeem_clause)
        redeem_ratio = float(m.group(1)) / 100 if m else (1 + coupon_rates[-1] / 100)
        redeem_price = 100 * redeem_ratio  # 含最后一期利息的赎回价

        # 付息日列表（每年付息一次，付息日为 value_date 的月日）
        pay_dates = []
        for i in range(1, len(coupon_rates) + 1):
            try:
                pd_date = value_date.replace(year=value_date.year + i)
            except ValueError:
                pd_date = value_date.replace(year=value_date.year + i, day=28)
            if pd_date <= expire_date:
                pay_dates.append(pd_date)
        # 确保最后一个付息日是到期日
        if not pay_dates or pay_dates[-1] != expire_date:
            pay_dates.append(expire_date)
        # 对应的票息率（取前 len(pay_dates) 个，不足时用最后一个补齐）
        coupon_rates = coupon_rates[:len(pay_dates)]
        while len(coupon_rates) < len(pay_dates):
            coupon_rates.append(coupon_rates[-1])

        ytm_list = []
        for _, row in df.iterrows():
            try:
                price = float(row["收盘价"])
                if price <= 0 or math.isnan(price):
                    ytm_list.append(float('nan'))
                    continue
                today = pd.to_datetime(str(row["日期"]), format="%Y%m%d")
                # 过滤出未来的付息日
                future_idx = [i for i, d in enumerate(pay_dates) if d > today]
                if not future_idx:
                    ytm_list.append(float('nan'))
                    continue
                cashflows = []
                times = []
                for i in future_idx:
                    t = (pay_dates[i] - today).days / 365.0
                    if t <= 0:
                        continue
                    # 当期利息 = 面值 * 当年票息率
                    coupon = 100 * coupon_rates[i] / 100
                    # 最后一期：利息已含在赎回价中，不重复计算
                    if i == len(pay_dates) - 1:
                        cashflows.append(redeem_price)
                    else:
                        cashflows.append(coupon)
                    times.append(t)
                if not cashflows:
                    ytm_list.append(float('nan'))
                    continue
                ytm = calc_ytm(price, cashflows, times)
                ytm_list.append(ytm)
            except Exception:
                ytm_list.append(float('nan'))
        return pd.Series(ytm_list, index=df.index)
    except Exception as e:
        logger.error("计算YTM序列失败 bond_code=%s：%s", bond_code, e)
        return pd.Series([float('nan')] * len(df))
