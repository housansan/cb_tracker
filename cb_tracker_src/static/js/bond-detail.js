// ── 基础信息 ─────────────────────────────────────────────
async function loadBondInfo(bondCode) {
  if (!bondCode) return;
  lastLoadedBondCode = bondCode;
  document.getElementById("infoContent").innerHTML = '<div class="info-loading">正在加载基础信息...</div>';
  document.getElementById("infoUpdateTime").textContent = "";
  try {
    const res  = await fetch(`/api/bond_info?bond_code=${encodeURIComponent(bondCode)}`);
    const json = await res.json();
    if (!json.success) {
      document.getElementById("infoContent").innerHTML = `<div class="info-error">⚠ ${json.message}</div>`;
      return;
    }
    renderBondInfo(json.data);
    // 显示 Tab 区，重置加载状态
    document.getElementById('detailTabsSection').style.display = '';
    // 保存正股代码到 data attribute 供 Tab 切换时使用
    document.getElementById('detailTabsSection').dataset.stockCode  = json.data["正股代码"] || "";
    document.getElementById('detailTabsSection').dataset.stockPrice = json.data["正股价"]   || "";
    _couponLoaded = false;
    _adjLoaded    = false;
    _stockLoaded  = false;
    _newsLoaded   = false;
    _noteLoaded   = false;
    // 若当前有激活的 tab，重新加载对应数据
    if (_activeTab === 'coupon') loadCouponInfo(bondCode);
    else if (_activeTab === 'adj') loadAdjLogs(bondCode);
    else if (_activeTab === 'stock') {
      const sc = json.data["正股代码"] || "";
      if (sc) loadStockFinancials(sc);
    }
    else if (_activeTab === 'news') {
      const sc = json.data["正股代码"] || "";
      if (sc) loadStockNews(sc);
    }
    else if (_activeTab === 'note') loadNote(bondCode);
    const now = new Date();
    document.getElementById("infoUpdateTime").textContent =
      `更新于 ${now.getHours().toString().padStart(2,'0')}:${now.getMinutes().toString().padStart(2,'0')}`;
    // 用上市/退市日期自动填充日期框
    const listingDate = json.data["上市日期"] || "";
    const delistDate  = json.data["退市日期"] || "";
    if (listingDate) {
      document.getElementById("start_date").value = listingDate.replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3");
    }
    document.getElementById("end_date").value = delistDate
      ? delistDate.replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3")
      : fmtDate(new Date());
  } catch (e) {
    document.getElementById("infoContent").innerHTML = `<div class="info-error">⚠ 加载失败：${e.message}</div>`;
  }
}

function renderBondInfo(d) {
  const premium      = parseFloat(d["转股溢价率"] || 0);
  const premiumClass = premium >= 0 ? "premium-pos" : "premium-neg";
  const premiumText  = (premium >= 0 ? "+" : "") + premium.toFixed(2) + "%";
  const fmtDate8     = s => s ? s.replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3") : "-";
  const expireDate   = (d["付息信息"] || {})["到期日"] || "";
  const rating       = d["信用评级"] || "";

  // ① Header：名称 + 代码 + 评级 badge
  const headerHtml = `
    <div class="bdi-header">
      <span class="bdi-name">${d["债券简称"] || "-"}</span>
      <span class="bdi-code">${d["债券代码"] || ""}</span>
      ${rating ? `<span class="bdi-rating">${rating}</span>` : ""}
    </div>`;

  // ② KPI 横排：4 个核心指标大字号显示
  const kpis = [
    { label: "债券现价",   val: parseFloat(d["债现价"]   || 0).toFixed(3), unit: "元",  cls: "price" },
    { label: "转股溢价率", val: premiumText,                                unit: "",   cls: premiumClass },
    { label: "转股价值",   val: parseFloat(d["转股价值"] || 0).toFixed(3), unit: "元",  cls: "" },
    { label: "剩余规模",   val: d["剩余规模"] != null ? parseFloat(d["剩余规模"]).toFixed(2) : "-", unit: "亿", cls: "" },
  ];
  const kpiHtml = `
    <div class="bdi-kpi-row">
      ${kpis.map(k => `
        <div class="bdi-kpi">
          <div class="bdi-kpi-val ${k.cls}">${k.val}<span class="bdi-kpi-unit">${k.unit}</span></div>
          <div class="bdi-kpi-label">${k.label}</div>
        </div>`).join("")}
    </div>`;

  // ③ 三列详情分组：正股 / 转股 / 时间
  const cols = [
    {
      title: "正股",
      items: [
        ["代码",   `${d["正股代码"] || "-"}<span class="bdi-sub">${d["正股简称"] || ""}</span>`],
        ["正股价", `<span style="color:#2e7d32;font-weight:700">${parseFloat(d["正股价"] || 0).toFixed(3)}</span> 元`],
      ],
    },
    {
      title: "转股",
      items: [
        ["转股价", parseFloat(d["转股价"] || 0).toFixed(3) + " 元"],
        ["溢价率", `<span class="${premiumClass}">${premiumText}</span>`],
      ],
    },
    {
      title: "时间",
      items: [
        ["上市", fmtDate8(d["上市日期"])],
        ["到期", fmtDate8(expireDate) + `<span class="bdi-sub"> 剩${d["剩余年限"] != null ? d["剩余年限"] + "年" : "-"}</span>`],
        ["退市", d["退市日期"] ? fmtDate8(d["退市日期"]) : "在市中"],
      ],
    },
  ];
  const detailHtml = `
    <div class="bdi-detail-row">
      ${cols.map(col => `
        <div class="bdi-detail-col">
          <div class="bdi-detail-title">${col.title}</div>
          ${col.items.map(([l, v]) => `
            <div class="bdi-detail-item">
              <span class="bdi-detail-label">${l}</span>
              <span class="bdi-detail-value">${v}</span>
            </div>`).join("")}
        </div>`).join("")}
    </div>`;

  document.getElementById("infoContent").innerHTML =
    headerHtml + kpiHtml + detailHtml + renderTargetPriceCalc(d);
}

// ── 详情 Tab 切换 ─────────────────────────────────────────
let _adjLoaded    = false;  // 转股价调整记录是否已加载
let _adjBondCode  = "";     // 已加载的债券代码
let _couponLoaded = false;  // 付息信息是否已加载
let _couponBondCode = "";   // 已加载付息信息的债券代码
let _stockLoaded  = false;  // 正股财务是否已加载
let _stockCode    = "";     // 已加载财务的正股代码
let _newsLoaded   = false;  // 公告/热点是否已加载
let _newsStockCode = "";    // 已加载新闻的正股代码
let _noteLoaded   = false;  // 笔记是否已加载
let _noteBondCode = "";     // 已加载笔记的债券代码
let _activeTab    = "";     // 当前激活的 tab

function switchDetailTab(tab) {
  document.getElementById('tabBtnCoupon').classList.toggle('active', tab === 'coupon');
  document.getElementById('tabBtnAdj').classList.toggle('active', tab === 'adj');
  document.getElementById('tabBtnStock').classList.toggle('active', tab === 'stock');
  document.getElementById('tabBtnNews').classList.toggle('active', tab === 'news');
  document.getElementById('tabBtnNote').classList.toggle('active', tab === 'note');
  document.getElementById('panelCoupon').classList.toggle('active', tab === 'coupon');
  document.getElementById('panelAdj').classList.toggle('active', tab === 'adj');
  document.getElementById('panelStock').classList.toggle('active', tab === 'stock');
  document.getElementById('panelNews').classList.toggle('active', tab === 'news');
  document.getElementById('panelNote').classList.toggle('active', tab === 'note');
  _activeTab = tab;
  const bondCode = document.getElementById('bond_code').value.trim();
  if (tab === 'coupon' && (!_couponLoaded || _couponBondCode !== bondCode)) {
    loadCouponInfo(bondCode);
  }
  if (tab === 'adj' && (!_adjLoaded || _adjBondCode !== bondCode)) {
    loadAdjLogs(bondCode);
  }
  if (tab === 'stock') {
    // 正股代码从 data attribute 中取（由 loadBondInfo 写入）
    const sc = document.getElementById('detailTabsSection').dataset.stockCode || "";
    if (sc && (!_stockLoaded || _stockCode !== sc)) {
      loadStockFinancials(sc);
    }
  }
  if (tab === 'news') {
    const sc = document.getElementById('detailTabsSection').dataset.stockCode || "";
    if (sc && (!_newsLoaded || _newsStockCode !== sc)) {
      loadStockNews(sc);
    }
  }
  if (tab === 'note' && (!_noteLoaded || _noteBondCode !== bondCode)) {
    loadNote(bondCode);
  }
}

async function loadCouponInfo(bondCode) {
  if (!bondCode) return;
  _couponBondCode = bondCode;
  _couponLoaded   = false;
  document.getElementById('couponLoading').style.display  = 'block';
  document.getElementById('couponLoading').textContent    = '正在加载付息信息...';
  document.getElementById('couponBody').innerHTML         = '';
  try {
    const res  = await fetch(`/api/bond_info?bond_code=${encodeURIComponent(bondCode)}`);
    const json = await res.json();
    if (!json.success) {
      document.getElementById('couponLoading').textContent = `⚠ ${json.message}`;
      return;
    }
    document.getElementById('couponLoading').style.display = 'none';
    renderCouponInfo(json.data['付息信息'] || {});
    _couponLoaded = true;
  } catch (e) {
    document.getElementById('couponLoading').textContent = `⚠ 加载失败：${e.message}`;
  }
}

async function loadAdjLogs(bondCode) {
  if (!bondCode) return;
  _adjBondCode = bondCode;
  _adjLoaded   = false;
  document.getElementById('adjLoading').style.display  = 'block';
  document.getElementById('adjLoading').textContent    = '正在加载转股价调整记录...';
  document.getElementById('adjBody').innerHTML         = '';
  try {
    const res  = await fetch(`/api/bond_adj_logs?bond_code=${encodeURIComponent(bondCode)}`);
    const json = await res.json();
    if (!json.success) {
      document.getElementById('adjLoading').textContent = `⚠ ${json.message}`;
      return;
    }
    document.getElementById('adjLoading').style.display = 'none';
    renderAdjLogs(json.data);
    _adjLoaded = true;
  } catch (e) {
    document.getElementById('adjLoading').textContent = `⚠ 加载失败：${e.message}`;
  }
}

function renderAdjLogs(records) {
  const body = document.getElementById('adjBody');
  if (!records || !records.length) {
    body.innerHTML = '<div class="adj-empty">📭 暂无转股价调整记录</div>';
    return;
  }
  const cols  = Object.keys(records[0]);
  const thead = `<tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr>`;
  const tbody = records.map(row => {
    const tds = cols.map(c => {
      let v = row[c];
      if (v == null) v = '-';
      return `<td>${v}</td>`;
    }).join('');
    return `<tr>${tds}</tr>`;
  }).join('');
  body.innerHTML = `
    <div class="adj-table-wrap">
      <table class="adj-table">
        <thead>${thead}</thead>
        <tbody>${tbody}</tbody>
      </table>
    </div>`;
}

function renderCouponInfo(ci) {
  if (!ci || !ci['付息日列表'] || !ci['付息日列表'].length) {
    document.getElementById('couponBody').innerHTML = '<div style="color:#aaa;font-size:.83rem;padding:8px 0">暂无付息信息</div>';
    return;
  }

  const fmtD  = s => s ? s.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3') : '-';
  const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');

  const payDates    = ci['付息日列表'];
  const rates       = ci['票息率列表'] || [];
  const lastIdx     = payDates.length - 1;
  const redeemPrice = ci['赎回价'] || '-';

  const rateDesc = ci['利率说明'] || '';
  const metaHtml = `
    <div class="coupon-meta">
      <div class="coupon-meta-item"><span class="coupon-meta-label">起息日</span><span class="coupon-meta-value">${fmtD(ci['起息日'])}</span></div>
      <div class="coupon-meta-item"><span class="coupon-meta-label">到期日</span><span class="coupon-meta-value">${fmtD(ci['到期日'])}</span></div>
      <div class="coupon-meta-item"><span class="coupon-meta-label">到期赎回价</span><span class="coupon-meta-value" style="color:#e53935">${redeemPrice} 元</span></div>
      <div class="coupon-meta-item"><span class="coupon-meta-label">付息期数</span><span class="coupon-meta-value">${payDates.length} 期</span></div>
      ${rateDesc ? `<div class="coupon-meta-item coupon-meta-wide"><span class="coupon-meta-label">利率说明</span><span class="coupon-meta-value coupon-rate-desc">${rateDesc}</span></div>` : ''}
    </div>`;

  let nextFound = false;
  const rows = payDates.map((d, i) => {
    const rate   = rates[i] != null ? rates[i].toFixed(2) + '%' : '-';
    const coupon = rates[i] != null ? rates[i].toFixed(2) + ' 元' : '-';
    const isLast = i === lastIdx;
    const isPast = d < today;
    let rowCls   = '';
    if (isLast)       rowCls = 'last-row';
    else if (isPast)  rowCls = 'past-row';
    else if (!nextFound && d !== today) { nextFound = true; rowCls = 'next-row'; }
    const cashflow = isLast
      ? `<span style="color:#e53935;font-weight:600">${redeemPrice} 元（含利息）</span>`
      : coupon;
    const status      = isPast ? '已付' : (d === today ? '今日' : '待付');
    const statusStyle = isPast ? 'color:#bbb' : (d === today ? 'color:#2e7d32;font-weight:600' : 'color:#3949ab');
    return `<tr class="${rowCls}">
      <td>${i + 1}</td>
      <td>${fmtD(d)}</td>
      <td>${rate}</td>
      <td>${cashflow}</td>
      <td style="${statusStyle}">${status}</td>
    </tr>`;
  }).join('');

  document.getElementById('couponBody').innerHTML = metaHtml + `
    <div class="coupon-table-wrap">
      <table class="coupon-table">
        <thead><tr><th>期次</th><th>付息日</th><th>票息率</th><th>每百元现金流</th><th>状态</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  _couponLoaded = true;
}

// ── 正股财务 ─────────────────────────────────────────────────────

// 年度/季度切换状态（模块级，不与债券绑定）
let _sfMode = 'annual';  // 'annual' | 'quarterly'

async function loadStockFinancials(stockCode) {
  if (!stockCode) return;
  _stockCode   = stockCode;
  _stockLoaded = false;
  const loadEl = document.getElementById('sfLoading');
  const bodyEl = document.getElementById('sfBody');
  loadEl.style.display = 'block';
  loadEl.textContent   = '正在加载正股财务数据...';
  bodyEl.innerHTML     = '';
  try {
    const res  = await fetch(`/api/stock_financials?stock_code=${encodeURIComponent(stockCode)}`);
    const json = await res.json();
    if (!json.success) {
      loadEl.textContent = `⚠ ${json.message}`;
      return;
    }
    loadEl.style.display = 'none';
    renderStockFinancials(json.data, stockCode);
    _stockLoaded = true;
  } catch (e) {
    loadEl.textContent = `⚠ 加载失败：${e.message}`;
  }
}

function renderStockFinancials(data, stockCode) {
  const profile   = data.profile   || {};
  const annual    = data.annual    || [];
  const quarterly = data.quarterly || [];

  // THS 财务摘要接口无货币资金字段，覆盖率分析不显示
  const coverageHtml = '';

  // ② 公司档案横排
  const soeType = profile.soe_type || '';
  const soeCls  = soeType.includes('国') ? 'sf-badge-soe' : (soeType ? 'sf-badge-private' : 'sf-badge-unknown');
  const soeHtml = soeType ? `<span class="sf-badge ${soeCls}">${soeType}</span>` : '';
  // PB = 正股价 ÷ 最新每股净资产
  const stockPrice = parseFloat(document.getElementById('detailTabsSection').dataset.stockPrice || '0');
  const bvps0 = annual.length && annual[0].bvps != null ? annual[0].bvps : null;
  const pb = (stockPrice > 0 && bvps0 != null && bvps0 !== 0) ? stockPrice / bvps0 : null;
  const pbHtml = pb != null ? `<span class="sf-badge sf-badge-pb${pb < 1 ? ' pb-low' : pb >= 3 ? ' pb-high' : ''}">PB ${pb.toFixed(2)}</span>` : '';

  // 缺钱风险评分（基于最新年度数据，综合现金比率 + 经营现金流 + 资产负债率）
  let _stressScore = 0;
  let _stressDetail = [];
  if (annual.length > 0) {
    const latest = annual[0];
    // 现金比率
    if (latest.monetary_funds != null && latest.current_liab != null && latest.current_liab > 0) {
      const cr = latest.monetary_funds / latest.current_liab;
      if (cr < 0.2)      { _stressScore += 2; _stressDetail.push('现金比率<0.2'); }
      else if (cr < 0.5) { _stressScore += 1; _stressDetail.push('现金比率<0.5'); }
    }
    // 经营现金流净额
    if (latest.op_cashflow_total != null) {
      if (latest.op_cashflow_total < 0)    { _stressScore += 2; _stressDetail.push('经营现金流为负'); }
      else if (latest.op_cashflow_total < 1) { _stressScore += 1; _stressDetail.push('经营现金流偏低'); }
    }
    // 资产负债率
    if (latest.debt_ratio != null) {
      if (latest.debt_ratio >= 85)      { _stressScore += 2; _stressDetail.push('负债率≥85%'); }
      else if (latest.debt_ratio >= 70) { _stressScore += 1; _stressDetail.push('负债率≥70%'); }
    }
    // 利息覆盖率（经营利润÷财务费用）—— 利息费用为负时表示支出，取绝对值
    if (latest.gross_profit != null && latest.interest_exp != null && Math.abs(latest.interest_exp) > 0) {
      const icr = latest.gross_profit / Math.abs(latest.interest_exp);
      if (icr < 2) { _stressScore += 2; _stressDetail.push(`利息覆盖率${icr.toFixed(1)}x`); }
      else if (icr < 4) { _stressScore += 1; _stressDetail.push(`利息覆盖率${icr.toFixed(1)}x`); }
    }
  }
  let stressHtml = '';
  if (_stressScore > 0) {
    const stressLevel = _stressScore >= 4 ? 'high' : _stressScore >= 2 ? 'mid' : 'low';
    const stressLabel = _stressScore >= 4 ? '融资压力：高' : _stressScore >= 2 ? '融资压力：中' : '融资压力：低';
    const stressTip   = _stressDetail.join('、');
    stressHtml = `<span class="sf-badge sf-badge-stress sf-badge-stress-${stressLevel}" title="${stressTip}">⚠ ${stressLabel}</span>`;
  }

  // PE(TTM) badge
  const pe0 = annual.length && annual[0].pe_ttm != null ? annual[0].pe_ttm : null;
  const peHtml = pe0 != null ? `<span class="sf-badge sf-badge-pe${pe0 < 15 ? ' pe-low' : pe0 >= 40 ? ' pe-high' : ''}">PE(TTM) ${pe0.toFixed(1)}</span>` : '';

  // 每股分红 badge
  const dps0 = annual.length && annual[0].dps != null ? annual[0].dps : null;
  const dpsHtml = dps0 != null && dps0 > 0 ? `<span class="sf-badge sf-badge-dps">分红 ${dps0.toFixed(3)}元/股</span>` : '';
  const profileHtml = `
    <div class="sf-profile">
      ${profile.name ? `<span class="sf-badge sf-badge-name">${profile.name}</span>` : ''}
      ${stockCode    ? `<span class="sf-badge sf-badge-code">${stockCode}</span>` : ''}
      ${profile.region   ? `<span class="sf-badge sf-badge-region">📍 ${profile.region}</span>` : ''}
      ${profile.industry ? `<span class="sf-badge sf-badge-industry">🏭 ${profile.industry}</span>` : ''}
      ${soeHtml}
      ${profile.list_date ? `<span class="sf-badge sf-badge-date">上市 ${profile.list_date}</span>` : ''}
      ${pbHtml}
      ${peHtml}
      ${dpsHtml}
      ${stressHtml}
    </div>`;

  // ③ 年度/季度切换
  const toggleHtml = `
    <div class="sf-toggle-row">
      <button class="sf-toggle-btn${_sfMode === 'annual' ? ' active' : ''}" onclick="sfSwitchMode('annual')">年度</button>
      <button class="sf-toggle-btn${_sfMode === 'quarterly' ? ' active' : ''}" onclick="sfSwitchMode('quarterly')">单季度</button>
    </div>`;

  // ④ 财务表（渲染后更新）
  document.getElementById('sfBody').innerHTML =
    coverageHtml + profileHtml + toggleHtml +
    `<div id="sfTableWrap">${buildSfTable(_sfMode === 'annual' ? annual : quarterly, _sfMode)}</div>`;
}

function sfSwitchMode(mode) {
  _sfMode = mode;
  document.querySelectorAll('.sf-toggle-btn').forEach(b => {
    b.classList.toggle('active', b.textContent.trim() === (mode === 'annual' ? '年度' : '单季度'));
  });
  // 重新获取已缓存的数据并重渲染表格
  const loadEl = document.getElementById('sfLoading');
  const wrap   = document.getElementById('sfTableWrap');
  if (!wrap) return;
  // 触发重新请求（数据已缓存，很快）
  const sc = document.getElementById('detailTabsSection').dataset.stockCode || "";
  if (!sc) return;
  fetch(`/api/stock_financials?stock_code=${encodeURIComponent(sc)}`)
    .then(r => r.json())
    .then(json => {
      if (!json.success) return;
      const rows = mode === 'annual' ? (json.data.annual || []) : (json.data.quarterly || []);
      wrap.innerHTML = buildSfTable(rows, mode);
    })
    .catch(() => {});
}

function fmtBillion(v) {
  if (v == null) return '-';
  // 同花顺财务摘要中数值单位为"万"，需要÷10000转为亿
  // 实际单位根据数据量级判断：若 v > 1e8 则是元，÷1e8；若 v > 1e4 则是万元，÷1e4；否则可能就是亿
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2);   // 万元→亿
  return v.toFixed(2);  // 已是亿
}

function fmtPct(v) {
  if (v == null) return '-';
  return v.toFixed(2) + '%';
}

function buildSfTable(rows, mode) {
  if (!rows || !rows.length) {
    return '<div class="sf-empty">暂无财务数据</div>';
  }

  // 同比步长：年度对比前1条，季度对比前4条
  const yoyStep = mode === 'annual' ? 1 : 4;

  // 行定义
  const metrics = [
    { key: 'revenue',        label: '营业收入',         fmt: v => fmtBillion(v) + ' 亿', cls: '' },
    { key: 'net_profit',     label: '净利润',           fmt: v => fmtBillion(v) + ' 亿', cls: row => row.net_profit != null && row.net_profit < 0 ? 'sf-neg' : 'sf-pos' },
    { key: '_margin',        label: '净利润率',         fmt: (v, row) => {
        if (row.revenue && row.net_profit != null && row.revenue !== 0) {
          const m = (row.net_profit / row.revenue) * 100;
          return `<span class="${m < 0 ? 'sf-neg' : ''}">${m.toFixed(2)}%</span>`;
        }
        return '-';
      }, isHtml: true },
    { key: 'roe',            label: 'ROE',              fmt: v => fmtPct(v), cls: v => v != null && v < 0 ? 'sf-neg' : '' },
    { key: 'debt_ratio',     label: '资产负债率',       fmt: v => fmtPct(v), cls: v => v != null ? (v >= 85 ? 'sf-danger' : v >= 70 ? 'sf-warn' : '') : '' },
    // THS摘要无现金流总额，显示每股经营现金流（元）
    { key: 'op_cashflow',    label: '每股经营现金流',   fmt: v => v != null ? (v >= 0 ? '+' : '') + v.toFixed(2) + ' 元' : '-', cls: v => v != null ? (v < 0 ? 'sf-neg' : 'sf-pos') : '' },
    // 以下三行来自新浪资产负债表（单位：亿，已在后端换算）
    { key: 'monetary_funds',  label: '货币资金',         fmt: v => v != null ? fmtBillion(v) + ' 亿' : '-', cls: '' },
    { key: 'current_liab',    label: '流动负债',         fmt: v => v != null ? fmtBillion(v) + ' 亿' : '-', cls: 'sf-liab-current' },
    { key: 'noncurrent_liab', label: '非流动负债',       fmt: v => v != null ? fmtBillion(v) + ' 亿' : '-', cls: 'sf-liab-noncurrent' },
    // 营收同比增长率（前端计算，annual: rows[i] vs rows[i+1]，quarterly: rows[i] vs rows[i+4]）
    { key: '_revenue_yoy', label: '营收同比',
      fmt: (v, row, idx) => {
        const prev = rows[idx + yoyStep];
        if (prev == null || prev.revenue == null || prev.revenue === 0 || row.revenue == null) return '-';
        const yoy = (row.revenue - prev.revenue) / Math.abs(prev.revenue) * 100;
        return `<span class="${yoy < 0 ? 'sf-neg' : 'sf-pos'}">${yoy >= 0 ? '+' : ''}${yoy.toFixed(2)}%</span>`;
      }, isHtml: true, needsIdx: true },
    // 净利润同比增长率
    { key: '_profit_yoy', label: '净利润同比',
      fmt: (v, row, idx) => {
        const prev = rows[idx + yoyStep];
        if (prev == null || prev.net_profit == null || prev.net_profit === 0 || row.net_profit == null) return '-';
        const yoy = (row.net_profit - prev.net_profit) / Math.abs(prev.net_profit) * 100;
        return `<span class="${yoy < 0 ? 'sf-neg' : 'sf-pos'}">${yoy >= 0 ? '+' : ''}${yoy.toFixed(2)}%</span>`;
      }, isHtml: true, needsIdx: true },
    // 经营活动现金流净额（来自新浪现金流量表，单位亿）
    { key: 'op_cashflow_total', label: '经营现金流净额',
      fmt: v => v != null ? fmtBillion(v) + ' 亿' : '-',
      cls: v => v != null ? (v < 0 ? 'sf-neg' : 'sf-pos') : '' },
    // 现金比率 = 货币资金 ÷ 流动负债（前端计算）
    { key: '_cash_ratio', label: '现金比率',
      fmt: (v, row) => {
        if (row.monetary_funds == null || row.current_liab == null || row.current_liab === 0) return '-';
        const r = row.monetary_funds / row.current_liab;
        return `<span class="${r < 0.2 ? 'sf-danger' : r < 0.5 ? 'sf-warn' : ''}">${r.toFixed(2)}</span>`;
      }, isHtml: true },
    // 每股净资产（THS 摘要字段，元/股）
    { key: 'bvps', label: '每股净资产',
      fmt: v => v != null ? v.toFixed(2) + ' 元' : '-',
      cls: v => v != null && v < 0 ? 'sf-danger' : '' },
    // ── 新增 6 项 ──────────────────────────────────────────────────────────────
    // 毛利润（sina 利润表，营业收入-营业成本，亿）
    { key: 'gross_profit', label: '毛利润',
      fmt: v => v != null ? fmtBillion(v) + ' 亿' : '-',
      cls: v => v != null && v < 0 ? 'sf-neg' : '' },
    // 毛利率（前端计算：gross_profit ÷ revenue，都是亿但量级一致可直接除）
    { key: '_gross_margin', label: '毛利率',
      fmt: (v, row) => {
        if (row.gross_profit == null || row.revenue == null || row.revenue === 0) return '-';
        // revenue 来自 THS，可能是万元；gross_profit 来自 sina，是亿；统一用亿来算
        const rev_bn = Math.abs(row.revenue) >= 1e4 ? row.revenue / 1e4 : row.revenue;
        const m = (row.gross_profit / rev_bn) * 100;
        return `<span class="${m < 0 ? 'sf-neg' : m >= 50 ? 'sf-pos' : ''}">${m.toFixed(2)}%</span>`;
      }, isHtml: true },
    // 扣非净利润（sina 利润表，亿）
    { key: 'net_profit_ex', label: '扣非净利润',
      fmt: v => v != null ? fmtBillion(v) + ' 亿' : '-',
      cls: v => v != null && v < 0 ? 'sf-neg' : (v != null ? 'sf-pos' : '') },
    // 利润含金量 = 经营现金流净额 ÷ 净利润（前端计算，无单位）
    { key: '_cashflow_quality', label: '利润含金量',
      fmt: (v, row) => {
        if (row.op_cashflow_total == null || row.net_profit == null || row.net_profit === 0) return '-';
        const np_bn = Math.abs(row.net_profit) >= 1e4 ? row.net_profit / 1e4 : row.net_profit;
        const q = row.op_cashflow_total / np_bn;
        const cls = q < 0 ? 'sf-danger' : q < 0.5 ? 'sf-warn' : 'sf-pos';
        return `<span class="${cls}">${q.toFixed(2)}x</span>`;
      }, isHtml: true },
    // 流动比率 = 流动资产 ÷ 流动负债（前端计算）
    { key: '_current_ratio', label: '流动比率',
      fmt: (v, row) => {
        if (row.current_assets == null || row.current_liab == null || row.current_liab === 0) return '-';
        const cr = row.current_assets / row.current_liab;
        return `<span class="${cr < 1 ? 'sf-danger' : cr < 1.5 ? 'sf-warn' : 'sf-pos'}">${cr.toFixed(2)}x</span>`;
      }, isHtml: true },
    // 利息覆盖率 = 毛利润 ÷ |财务费用|（前端计算）
    { key: '_icr', label: '利息覆盖率',
      fmt: (v, row) => {
        if (row.gross_profit == null || row.interest_exp == null || Math.abs(row.interest_exp) === 0) return '-';
        const icr = row.gross_profit / Math.abs(row.interest_exp);
        return `<span class="${icr < 2 ? 'sf-danger' : icr < 4 ? 'sf-warn' : 'sf-pos'}">${icr.toFixed(1)}x</span>`;
      }, isHtml: true },
    // 自由现金流 = 经营现金流净额 - 资本支出（暂无capex字段，用经营现金流代替；后续可扩展）
    // 每股分红（THS 摘要字段 or 分红历史，元/股，仅第一列有值）
    { key: 'dps', label: '每股分红',
      fmt: v => v != null && v > 0 ? v.toFixed(3) + ' 元/股' : '-',
      cls: v => v != null && v > 0 ? 'sf-pos' : '' },
  ];

  const headerCells = rows.map(r => `<th>${r.period || '-'}</th>`).join('');
  const bodyRows = metrics.map(m => {
    const cells = rows.map((row, idx) => {
      let val, cls = '';
      // 前端计算行（key以_开头）：直接调用 fmt(null, row, idx)
      if (m.key.startsWith('_')) {
        val = m.fmt(null, row, idx);
        return `<td class="sf-num">${val}</td>`;
      }
      if (m.needsIdx) {
        val = m.fmt(null, row, idx);
        return `<td class="sf-num">${val}</td>`;
      }
      val = row[m.key];
      const formatted = m.fmt(val, row);
      if (typeof m.cls === 'function') cls = m.cls(val, row) || '';
      else cls = m.cls || '';
      return `<td class="sf-num ${cls}">${formatted}</td>`;
    }).join('');
    return `<tr><td class="sf-metric-label">${m.label}</td>${cells}</tr>`;
  }).join('');

  return `
    <div class="sf-table-wrap">
      <table class="sf-table">
        <thead><tr><th></th>${headerCells}</tr></thead>
        <tbody>${bodyRows}</tbody>
      </table>
      <div class="sf-table-note">数据来源：同花顺财务摘要 + 新浪资产负债表/利润表/现金流量表 + AKShare实时行情；收入/利润/现金流/负债单位为亿元 | <span style="color:#f57c00">■ 流动负债</span>（1年内到期）<span style="color:#8e24aa;margin-left:8px">■ 非流动负债</span>（1年以上）</div>
    </div>`;
}

// ── 公告/热点 ─────────────────────────────────────────────────────

async function loadStockNews(stockCode) {
  if (!stockCode) return;
  _newsStockCode = stockCode;
  _newsLoaded    = false;
  const loadEl = document.getElementById('newsLoading');
  const bodyEl = document.getElementById('newsBody');
  loadEl.style.display = 'block';
  loadEl.textContent   = '正在加载公告/热点...';
  bodyEl.innerHTML     = '';
  try {
    const res  = await fetch(`/api/stock_news?stock_code=${encodeURIComponent(stockCode)}`);
    const json = await res.json();
    if (!json.success) {
      loadEl.textContent = `⚠ ${json.message}`;
      return;
    }
    loadEl.style.display = 'none';
    renderStockNews(json.data || []);
    _newsLoaded = true;
  } catch (e) {
    loadEl.textContent = `⚠ 加载失败：${e.message}`;
  }
}

function renderStockNews(items) {
  const bodyEl = document.getElementById('newsBody');
  if (!items.length) {
    bodyEl.innerHTML = '<div class="news-empty">暂无公告/新闻数据</div>';
    return;
  }
  const rows = items.map(item => {
    const typeCls  = item.type === 'notice' ? 'news-tag-notice' : 'news-tag-news';
    const typeLabel = item.type === 'notice' ? '公告' : '新闻';
    const dateStr  = (item.date || '').slice(0, 16);  // 最多显示 YYYY-MM-DD HH:MM
    const titleHtml = item.url
      ? `<a class="news-title-link" href="${item.url}" target="_blank" rel="noopener">${item.title}</a>`
      : `<span class="news-title-text">${item.title}</span>`;
    return `<div class="news-item">
      <span class="news-tag ${typeCls}">${typeLabel}</span>
      <div class="news-title">${titleHtml}</div>
      <div class="news-meta">
        <span class="news-source">${item.source || ''}</span>
        <span class="news-date">${dateStr}</span>
      </div>
    </div>`;
  }).join('');
  bodyEl.innerHTML = `<div class="news-list">${rows}</div>
    <div class="news-footer">数据来源：东方财富资讯 / 公告系统 | 仅供参考，不构成投资建议</div>`;
}

// ── 目标买入价计算器 ───────────────────────────────────────────
let _targetCashflows = [];
let _targetTimes     = [];
let _targetCurPrice  = 0;

const TARGET_PRESETS = [
  { label: "保本",  ytm: 0   },
  { label: "+1%",   ytm: 1   },
  { label: "+2%",   ytm: 2   },
  { label: "-1%",   ytm: -1  },
  { label: "-2%",   ytm: -2  },
];

// 计算目标买入价（纯前端，无需请求接口）
function calcTargetPrice(ytmPct) {
  if (!_targetCashflows.length) return null;
  const r = ytmPct / 100;
  if (Math.abs(r + 1) < 1e-9) return null;
  const price = _targetCashflows.reduce((sum, cf, i) => sum + cf / Math.pow(1 + r, _targetTimes[i]), 0);
  return Math.round(price * 100) / 100;
}

// 统一更新结果展示
function _updateTargetResult(ytmPct) {
  const resultEl = document.getElementById("targetPriceResult");
  const gapEl    = document.getElementById("targetPriceGap");
  if (!resultEl) return;
  if (ytmPct === null || isNaN(ytmPct)) {
    resultEl.textContent = "—";
    gapEl.textContent = ""; gapEl.className = "target-price-gap";
    return;
  }
  const p = calcTargetPrice(ytmPct);
  if (p == null) { resultEl.textContent = "无法计算"; gapEl.textContent = ""; return; }
  resultEl.textContent = p.toFixed(2) + " 元";
  const gap = p - _targetCurPrice;
  const isGood = gap > 0;
  gapEl.textContent = (gap >= 0 ? "▲ +" : "▼ ") + gap.toFixed(2) + " 元  " + (isGood ? "✓ 当前价低于目标价" : "当前价高于目标价");
  gapEl.className = "target-price-gap " + (isGood ? "gap-good" : "gap-over");
}

// 点击预设按钮
function onPresetClick(ytm, el) {
  document.querySelectorAll(".target-preset-btn").forEach(b => b.classList.remove("active"));
  el.classList.add("active");
  document.getElementById("targetYtmInput").value = ytm;
  _updateTargetResult(ytm);
}

// 用户自由输入
function onTargetYtmInput() {
  document.querySelectorAll(".target-preset-btn").forEach(b => b.classList.remove("active"));
  const val = parseFloat(document.getElementById("targetYtmInput").value);
  _updateTargetResult(isNaN(val) ? null : val);
}

// 渲染目标价计算器区块，附加到 infoContent 之后
function renderTargetPriceCalc(d) {
  if (d["退市日期"]) return "";   // 已退市不显示
  const cashflows = d["cashflows"] || [];
  const times     = d["times"]     || [];
  if (!cashflows.length) return "";  // 无未来现金流

  _targetCashflows = cashflows;
  _targetTimes     = times;
  _targetCurPrice  = parseFloat(d["债现价"] || 0);

  const presetBtns = TARGET_PRESETS.map(p =>
    `<button class="target-preset-btn" onclick="onPresetClick(${p.ytm}, this)">${p.label}</button>`
  ).join("");

  return `<div class="target-calc-section">
    <div class="target-calc-header">
      <span class="target-calc-title">📍 目标买入价</span>
      <span class="target-calc-hint">当前价 <strong>${_targetCurPrice.toFixed(3)}</strong> 元</span>
    </div>
    <div class="target-preset-row">${presetBtns}</div>
    <div class="target-calc-body">
      <label class="target-calc-label">自定义收益率</label>
      <div class="target-calc-input-row">
        <input id="targetYtmInput" class="target-ytm-input" type="number" step="0.1"
               placeholder="如 1.5" oninput="onTargetYtmInput()" />
        <span class="target-ytm-unit">%</span>
      </div>
      <div class="target-calc-result-row">
        <span class="target-calc-result-label">对应买入价</span>
        <span id="targetPriceResult" class="target-calc-result">—</span>
      </div>
      <div id="targetPriceGap" class="target-price-gap"></div>
    </div>
    <div class="target-calc-note">基于剩余现金流折现反推，仅供参考</div>
  </div>`;
}

// ── 笔记 ───────────────────────────────────────────────────────────

async function loadNote(bondCode) {
  _noteBondCode = bondCode;
  _noteLoaded   = false;
  const textarea = document.getElementById('noteContent');
  textarea.value = '正在加载...';
  try {
    const res  = await fetch(`/api/note?bond_code=${encodeURIComponent(bondCode)}`);
    const json = await res.json();
    if (json.success && json.data) {
      textarea.value = json.data.content || '';
    } else {
      textarea.value = '';
    }
    _noteLoaded = true;
  } catch (e) {
    textarea.value = '';
  }
}

async function saveNote() {
  const bondCode = document.getElementById('bond_code').value.trim();
  const content  = document.getElementById('noteContent').value;
  if (!bondCode) return;
  try {
    const res = await fetch('/api/note', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({bond_code: bondCode, content: content}),
    });
    const json = await res.json();
    if (json.success) {
      alert('笔记已保存');
    } else {
      alert('保存失败：' + (json.message || ''));
    }
  } catch (e) {
    alert('保存失败：' + e.message);
  }
}

async function deleteNote() {
  const bondCode = document.getElementById('bond_code').value.trim();
  if (!bondCode) return;
  if (!confirm('确定删除这条笔记？')) return;
  try {
    const res = await fetch(`/api/note?bond_code=${encodeURIComponent(bondCode)}`, {method: 'DELETE'});
    const json = await res.json();
    if (json.success) {
      document.getElementById('noteContent').value = '';
    }
  } catch (e) {
    alert('删除失败：' + e.message);
  }
}
