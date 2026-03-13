# 可转债历史数据查询工具

基于 Flask + AKShare 构建的可转债历史数据查询 Web 应用，支持查看 K 线走势、价值分析、转股价调整记录等信息。

## 功能特性

- � 全市场可转债列表浏览（债现价、溢价率、信用评级等）
- �📈 查询可转债历史成交数据（价格、成交量、溢价率、正股收盘价、到期收益率等）
- 🔍 查询可转债基础信息（上市/退市日期、转股价、付息结构等）
- 📋 查询转股价格调整记录
- ⚡ 多级缓存（内存缓存 + 本地文件缓存），避免重复请求

## 目录结构

```
fund/
├── app.py                  # Flask 应用入口，定义 API 路由
├── bond_history.py         # 核心数据获取与缓存逻辑
├── requirements.txt        # Python 依赖
├── app.log                 # 运行日志
├── templates/
│   └── index.html          # 前端页面
└── data/                   # 数据目录
    ├── bond_cache/         # 本地文件缓存（按类型分子目录）
    │   ├── kline/          # K 线全量历史（hist_full_*.json）
    │   ├── merged/         # 合并后完整历史（full_hist_*.json）
    │   ├── cov_value/      # 价值分析数据（cov_value_*.json）
    │   ├── iss_amt/        # 剩余规模历史（iss_amt_hist_*.json）
    │   ├── adj_logs/       # 转股价调整记录（adj_logs_*.json）
    │   └── misc/           # 其他缓存
    └── exports/            # 手动导出的历史数据文件
        └── *.csv           # 按债券代码命名，如 bond_113050_history.csv
```

## 安装与运行

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务

```bash
python app.py
```

服务默认运行在 `http://localhost:5000`。

## API 接口

### 获取全市场可转债列表

```
GET /api/bond_list
```

返回字段：`债券代码`、`债券简称`、`债现价`、`正股代码`、`正股简称`、`正股价`、`转股溢价率`、`信用评级`、`剩余规模`

---

### 查询历史成交数据

```
GET /api/history?bond_code=113050&start_date=20230101&end_date=20231231
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `bond_code` | ✅ | 可转债代码，如 `113050` |
| `start_date` | ❌ | 开始日期 `YYYYMMDD`，默认取上市日期 |
| `end_date` | ❌ | 结束日期 `YYYYMMDD`，默认取退市日期或今日 |

返回字段：`日期`、`开盘价`、`收盘价`、`最高价`、`最低价`、`成交量`、`转股溢价率`、`转股价值`、`剩余规模`、`正股收盘价`、`到期收益率`

---

### 查询基础信息

```
GET /api/bond_info?bond_code=113050
```

返回字段：`债券代码`、`债券简称`、`债现价`、`正股代码`、`正股简称`、`正股价`、`转股溢价率`、`转股价`、`转股价值`、`信用评级`、`剩余规模`、`上市日期`、`退市日期`、`付息信息`（含起息日、到期日、赎回价、票息率列表、付息日列表）

---

### 查询转股价调整记录

```
GET /api/bond_adj_logs?bond_code=127099
```

## 缓存说明

数据采用两级缓存策略：

| 级别 | 存储位置 | 有效期 | 适用场景 |
|------|---------|--------|---------|
| 内存缓存 | 进程内存 | 当天有效（次日自动失效） | 所有数据，进程内重复查询直接命中 |
| 本地文件缓存 | `data/bond_cache/` | 永久（退市债数据不再变化） | 已退市可转债，服务重启后仍可直接读取 |

**全市场列表**（`/api/bond_list`）使用独立的内存缓存，有效期 **1 天**，每日首次请求时刷新。

## 依赖

| 包 | 版本要求 | 用途 |
|----|---------|------|
| [AKShare](https://akshare.akfamily.xyz/) | `>=1.12.0` | 可转债数据源 |
| [pandas](https://pandas.pydata.org/) | `>=2.0.0` | 数据处理 |
| [Flask](https://flask.palletsprojects.com/) | 最新 | Web 框架 |

## 数据库

### 信用评级编码表

| 评级 | 整数值 | 评级 | 整数值 |
|------|--------|------|--------|
| AAA  | 700    | BB+  | 230    |
| AA+  | 650    | BB   | 210    |
| AA   | 600    | BB-  | 190    |
| AA-  | 550    | B+   | 170    |
| A+   | 500    | B    | 150    |
| A    | 450    | B-   | 130    |
| A-   | 400    | CCC  | 100    |
| BBB+ | 350    | CC   | 70     |
| BBB  | 300    | C    | 50     |
| BBB- | 250    | 其他/未知 | 0 |

### 建表 SQL

**SQLite 版本：**

```sql
CREATE TABLE IF NOT EXISTS t_bond_info (
    id               INTEGER           NOT NULL PRIMARY KEY AUTOINCREMENT,  -- 自增主键
    bond_code        INTEGER UNSIGNED  NOT NULL UNIQUE,                     -- 债券代码（如113050）
    bond_name        VARCHAR(20)       NOT NULL,                            -- 债券简称
    stock_code       INTEGER UNSIGNED,                                      -- 正股代码（如600519）
    stock_name       VARCHAR(20),                                           -- 正股简称
    conv_price       INTEGER UNSIGNED,                                      -- 转股价×100（元），如10.25→1025
    issue_size       INTEGER UNSIGNED,                                      -- 发行规模×100（亿元），如8.00→800
    credit_rating    SMALLINT UNSIGNED,                                     -- 信用评级编码，AAA=700，C=50，未知=0
    listing_date     TIMESTAMP,                                             -- 上市日期
    delist_date      TIMESTAMP,                                             -- 退市日期，NULL=在途
    value_date       TIMESTAMP,                                             -- 起息日
    expire_date      TIMESTAMP,                                             -- 到期日
    redeem_price     INTEGER UNSIGNED,                                      -- 到期赎回价×100（元），如110.00→11000
    coupon_rate_desc VARCHAR(500),                                          -- 利率说明原文
    coupon_rates     VARCHAR(200),                                          -- 各年票息率 JSON数组字符串
    coupon_pay_dates VARCHAR(500),                                          -- 付息日列表 JSON数组字符串
    created_at       TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- 首次入库时间
    updated_at       TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP   -- 最后更新时间
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_bond_code ON t_bond_info (bond_code);
```

**MySQL 版本：**

```sql
CREATE TABLE IF NOT EXISTS t_bond_info (
    id               INT UNSIGNED      NOT NULL AUTO_INCREMENT              COMMENT '自增主键',
    bond_code        INT UNSIGNED      NOT NULL                             COMMENT '债券代码（如113050）',
    bond_name        VARCHAR(20)       NOT NULL                             COMMENT '债券简称',
    stock_code       INT UNSIGNED                                           COMMENT '正股代码（如600519）',
    stock_name       VARCHAR(20)                                            COMMENT '正股简称',
    conv_price       INT UNSIGNED                                           COMMENT '转股价×100（元），如10.25→1025',
    issue_size       INT UNSIGNED                                           COMMENT '发行规模×100（亿元），如8.00→800',
    credit_rating    SMALLINT UNSIGNED                                      COMMENT '信用评级编码，AAA=700，C=50，未知=0',
    listing_date     TIMESTAMP         NULL                                 COMMENT '上市日期',
    delist_date      TIMESTAMP         NULL                                 COMMENT '退市日期，NULL=在途',
    value_date       TIMESTAMP         NULL                                 COMMENT '起息日',
    expire_date      TIMESTAMP         NULL                                 COMMENT '到期日',
    redeem_price     INT UNSIGNED                                           COMMENT '到期赎回价×100（元），如110.00→11000',
    coupon_rate_desc VARCHAR(500)                                           COMMENT '利率说明原文',
    coupon_rates     VARCHAR(200)                                           COMMENT '各年票息率 JSON数组字符串',
    coupon_pay_dates VARCHAR(500)                                           COMMENT '付息日列表 JSON数组字符串',
    created_at       TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP   COMMENT '首次入库时间',
    updated_at       TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP
                     ON UPDATE CURRENT_TIMESTAMP                            COMMENT '最后更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_bond_code (bond_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='可转债基础信息表';
```

---

#### t_bond_daily（日K线行情 + 日度分析指标合并表）

**SQLite 版本：**

```sql
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

    -- ── 元数据 ────────────────────────────────────────────────────
    created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (bond_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_bond_daily_bond_code  ON t_bond_daily (bond_code);
CREATE INDEX IF NOT EXISTS idx_bond_daily_trade_date ON t_bond_daily (trade_date);
```

**MySQL 版本：**

```sql
CREATE TABLE IF NOT EXISTS t_bond_daily (
    id                   INT UNSIGNED  NOT NULL AUTO_INCREMENT              COMMENT '自增主键',
    bond_code            INT UNSIGNED  NOT NULL                             COMMENT '债券代码（关联 t_bond_info.bond_code）',
    trade_date           TIMESTAMP     NOT NULL                             COMMENT '交易日期',

    open                 INT UNSIGNED                                       COMMENT '开盘价×100（元）',
    high                 INT UNSIGNED                                       COMMENT '最高价×100（元）',
    low                  INT UNSIGNED                                       COMMENT '最低价×100（元）',
    close                INT UNSIGNED                                       COMMENT '收盘价×100（元）',
    volume               INT UNSIGNED                                       COMMENT '成交量（手）',
    amount               INT UNSIGNED                                       COMMENT '成交额×100（元）',

    conv_premium_rate    INT                                                COMMENT '转股溢价率×10000，可为负',
    ytm                  INT                                                COMMENT '到期收益率×10000，可为负',
    conv_value           INT UNSIGNED                                       COMMENT '转股价值×100（元）',
    double_low           INT UNSIGNED                                       COMMENT '双低值×100',

    created_at           TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP   COMMENT '首次入库时间',
    updated_at           TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
                         ON UPDATE CURRENT_TIMESTAMP                        COMMENT '最后更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_bond_daily (bond_code, trade_date),
    KEY idx_bond_daily_bond_code  (bond_code),
    KEY idx_bond_daily_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='可转债日K线行情+日度分析指标合并表';
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `open/high/low/close` | `INTEGER ×100` | 价格×100存整数，避免浮点精度问题 |
| `volume` | `INTEGER` | 成交量，单位：手 |
| `amount` | `INTEGER ×100` | 成交额×100（元） |
| `conv_premium_rate` | `INTEGER ×10000` | 溢价率精度高，×10000（如 5.23% → 523），可为负 |
| `ytm` | `INTEGER ×10000` | 到期收益率同上，可为负 |
| `conv_value` | `INTEGER ×100` | 转股价值×100 |
| `double_low` | `INTEGER ×100` | 双低值 = 收盘价 + 溢价率×100，×100存储 |
