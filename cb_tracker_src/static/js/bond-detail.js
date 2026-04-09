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
    _couponLoaded = false;
    _adjLoaded    = false;
    // 若当前有激活的 tab，重新加载对应数据
    if (_activeTab === 'coupon') loadCouponInfo(bondCode);
    else if (_activeTab === 'adj') loadAdjLogs(bondCode);
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

  const items = [
    { label: "债券名称",   value: d["债券简称"] || "-",                      sub: d["债券代码"] || "" },
    { label: "债券代码",   value: d["债券代码"] || "-",                      sub: "" },
    { label: "债券现价",   value: parseFloat(d["债现价"]    || 0).toFixed(3), sub: "元",  cls: "price" },
    { label: "正股代码",   value: d["正股代码"] || "-",                      sub: d["正股简称"] || "" },
    { label: "正股价格",   value: parseFloat(d["正股价"]    || 0).toFixed(3), sub: "元",  cls: "stock-price" },
    { label: "转股溢价率", value: premiumText,                                sub: "溢价率越低越安全", cls: premiumClass },
    { label: "转股价",     value: parseFloat(d["转股价"]    || 0).toFixed(3), sub: "元" },
    { label: "转股价值",   value: parseFloat(d["转股价值"]  || 0).toFixed(3), sub: "元" },
    { label: "信用评级",   value: d["信用评级"] || "-",                      sub: "" },
    { label: "剩余规模",   value: d["剩余规模"] != null ? parseFloat(d["剩余规模"]).toFixed(2) + " 亿" : "-", sub: "" },
    { label: "上市日期",   value: d["上市日期"] ? d["上市日期"].replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3") : "-", sub: "" },
    { label: "退市日期",   value: d["退市日期"] ? d["退市日期"].replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3") : "在市中", sub: "" },
  ];

  document.getElementById("infoContent").innerHTML =
    `<div class="info-grid">${
      items.map(it => `
        <div class="info-item">
          <span class="info-item-label">${it.label}</span>
          <span class="info-item-value ${it.cls || ''}">${it.value}</span>
          ${it.sub ? `<span class="info-item-sub">${it.sub}</span>` : ""}
        </div>`).join("")
    }</div>` + renderTargetPriceCalc(d);
}

// ── 详情 Tab 切换 ─────────────────────────────────────────
let _adjLoaded    = false;  // 转股价调整记录是否已加载
let _adjBondCode  = "";     // 已加载的债券代码
let _couponLoaded = false;  // 付息信息是否已加载
let _couponBondCode = "";   // 已加载付息信息的债券代码
let _activeTab    = "";     // 当前激活的 tab

function switchDetailTab(tab) {
  document.getElementById('tabBtnCoupon').classList.toggle('active', tab === 'coupon');
  document.getElementById('tabBtnAdj').classList.toggle('active', tab === 'adj');
  document.getElementById('panelCoupon').classList.toggle('active', tab === 'coupon');
  document.getElementById('panelAdj').classList.toggle('active', tab === 'adj');
  _activeTab = tab;
  const bondCode = document.getElementById('bond_code').value.trim();
  if (tab === 'coupon' && (!_couponLoaded || _couponBondCode !== bondCode)) {
    loadCouponInfo(bondCode);
  }
  if (tab === 'adj' && (!_adjLoaded || _adjBondCode !== bondCode)) {
    loadAdjLogs(bondCode);
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
    body.innerHTML = '<div class="adj-empty">暂无转股价调整记录</div>';
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

  const metaHtml = `
    <div class="coupon-meta">
      <div class="coupon-meta-item"><span class="coupon-meta-label">起息日</span><span class="coupon-meta-value">${fmtD(ci['起息日'])}</span></div>
      <div class="coupon-meta-item"><span class="coupon-meta-label">到期日</span><span class="coupon-meta-value">${fmtD(ci['到期日'])}</span></div>
      <div class="coupon-meta-item"><span class="coupon-meta-label">到期赎回价</span><span class="coupon-meta-value" style="color:#e53935">${redeemPrice} 元</span></div>
      <div class="coupon-meta-item"><span class="coupon-meta-label">付息期数</span><span class="coupon-meta-value">${payDates.length} 期</span></div>
    </div>`;

  const rows = payDates.map((d, i) => {
    const rate   = rates[i] != null ? rates[i].toFixed(2) + '%' : '-';
    const coupon = rates[i] != null ? rates[i].toFixed(2) + ' 元' : '-';
    const isLast = i === lastIdx;
    const isPast = d < today;
    let rowCls   = '';
    if (isLast)       rowCls = 'last-row';
    else if (isPast)  rowCls = 'past-row';
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
