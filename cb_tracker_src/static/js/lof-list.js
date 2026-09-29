// ── LOF 基金列表视图 ─────────────────────────────────────────────
let _allLofs = [];
let _filteredLofs = [];
let _lofSortKey = '溢价率';
let _lofSortDir = 'desc';   // 默认按溢价率降序（高溢价在前）
let _lofQuickFilter = null; // 'premium' | 'discount' | 'arbitrage' | 'active'

// 金额格式化：元 → 万/亿，便于阅读
function _fmtAmount(yuan) {
  const v = parseFloat(yuan);
  if (isNaN(v) || v <= 0) return '-';
  if (v >= 1e8) return (v / 1e8).toFixed(2).replace(/\.?0+$/, '') + '亿';
  if (v >= 1e4) return (v / 1e4).toFixed(2).replace(/\.?0+$/, '') + '万';
  return v.toFixed(0) + '元';
}

// HTML 转义（赎回费期限原文来自外部接口，防止意外注入）
function _escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

async function loadLofList() {
  const el = document.getElementById('lofContent');
  el.innerHTML = '<div class="list-loading">正在加载 LOF 基金列表...</div>';
  try {
    const res = await fetch('/api/lof_list');
    const json = await res.json();
    if (!json.success) {
      el.innerHTML = `<div style="color:#c62828;padding:24px">⚠ ${json.message}</div>`;
      return;
    }
    _allLofs = json.data;
    filterLofList();
    // 赎回费后台抓取中：分批静默刷新，补上「免赎费天数」等数据
    if (json.pending_fee) {
      [30000, 90000, 180000].forEach(delay => {
        setTimeout(() => {
          fetch('/api/lof_list?_bust=' + Date.now())
            .then(r => r.json())
            .then(j => { if (j.success && j.data) { _allLofs = j.data; filterLofList(); } })
            .catch(() => {});
        }, delay);
      });
    }
  } catch (e) {
    el.innerHTML = `<div style="color:#c62828;padding:24px">⚠ 加载失败：${e.message}</div>`;
  }
}

// 搜索 + 快捷策略筛选
function filterLofList() {
  const kw = (document.getElementById('lofSearch').value || '').trim().toLowerCase();
  _filteredLofs = _allLofs.filter(f => {
    if (kw) {
      const match = (f['代码'] || '').toLowerCase().includes(kw) ||
                    (f['名称'] || '').toLowerCase().includes(kw);
      if (!match) return false;
    }
    const pr = f['溢价率'];
    const amt = f['成交额'];
    switch (_lofQuickFilter) {
      case 'premium':
        if (pr == null || pr <= 0.3) return false;
        break;
      case 'discount':
        if (pr == null || pr >= -0.7) return false;
        break;
      case 'active':
        if (amt == null || amt < 500000) return false;
        break;
      case 'limit':
        // 限大额：申购状态为「限大额」
        if ((f['申购状态'] || '') !== '限大额') return false;
        break;
      case 'net_premium':
        // 净溢价套利：净溢价(溢价率−申购费)>0.5% 且 成交额≥50万 且 可申购
        if (f['净溢价'] == null || f['净溢价'] <= 0.5) return false;
        if (amt == null || amt < 500000) return false;
        const buyable = (f['申购状态'] || '').includes('开放') || (f['申购状态'] || '') === '限大额';
        if (!buyable) return false;
        break;
      case 'net_discount':
        // 净折价套利：净折价(|折价率|−短线赎回费)>0.5% 且 成交额≥50万 且 可赎回
        if (f['净折价'] == null || f['净折价'] <= 0.5) return false;
        if (amt == null || amt < 500000) return false;
        if (!(f['赎回状态'] || '').includes('开放')) return false;
        break;
      case 'low_free_days':
        // 短期免赎费：免赎费天数 ≤ 30 天（持有一个月内即可 0 费赎回）
        if (f['免赎费天数'] == null || f['免赎费天数'] > 30) return false;
        break;
      case 'arbitrage':
        // 参考仓库口径：成交额≥50万 且 (溢价>0.3%且可申购 或 折价<-0.7%且可赎回)
        if (amt == null || amt < 500000 || pr == null) return false;
        const canBuy = (f['申购状态'] || '').includes('开放') || (f['申购状态'] || '').includes('限');
        const canSell = (f['赎回状态'] || '').includes('开放');
        const premOk = pr > 0.3 && canBuy;
        const discOk = pr < -0.7 && canSell;
        if (!premOk && !discOk) return false;
        break;
    }
    return true;
  });
  renderLofList(_filteredLofs);
}

function setLofQuickFilter(preset) {
  _lofQuickFilter = (_lofQuickFilter === preset) ? null : preset;
  document.querySelectorAll('#lofView .quick-filter-btn:not(.reset-btn)').forEach(b => b.classList.remove('active'));
  if (_lofQuickFilter && event && event.currentTarget) event.currentTarget.classList.add('active');
  filterLofList();
}

function resetLofFilter() {
  _lofQuickFilter = null;
  document.getElementById('lofSearch').value = '';
  document.querySelectorAll('#lofView .quick-filter-btn').forEach(b => b.classList.remove('active'));
  filterLofList();
}

function sortLofList(key) {
  if (_lofSortKey === key) {
    _lofSortDir = _lofSortDir === 'asc' ? 'desc' : 'asc';
  } else {
    _lofSortKey = key;
    _lofSortDir = 'desc';
  }
  renderLofList(_filteredLofs);
}

function renderLofList(lofs) {
  const el = document.getElementById('lofContent');
  document.getElementById('lofCount').textContent = `共 ${lofs.length} 只`;
  if (!lofs.length) {
    el.innerHTML = '<div style="color:#aaa;padding:24px;text-align:center">暂无数据</div>';
    return;
  }

  // 排序（文本字段按 localeCompare，其余按数值）
  const TEXT_KEYS = new Set(['代码', '名称', '净值来源', '基金类型', '下一开放日']);
  // 申购/赎回状态：按「可交易程度」定义业务权重，而非字符串顺序。
  // 权重越大越「开放」。升序时开放在前。
  const _buyStatusRank = {
    '开放申购': 5, '限大额': 4, '场内交易': 3, '暂停申购': 2, '认购期': 1, '封闭期': 0,
  };
  const _sellStatusRank = {
    '开放赎回': 4, '场内交易': 3, '暂停赎回': 2, '认购期': 1, '封闭期': 0,
  };
  function _buyWeight(f) {
    const base = _buyStatusRank[f['申购状态']] ?? -1;
    // 限大额内部：单日限额越大越开放，用限额做小数位次级排序
    if (f['申购状态'] === '限大额' && f['日累计限定金额'] > 0) {
      // 限额归一到 [0,1)，5亿封顶，避免跨档串位
      const frac = Math.min(f['日累计限定金额'] / 5e8, 0.999);
      return base + frac;
    }
    return base;
  }
  let sorted = [...lofs];
  if (_lofSortKey) {
    sorted.sort((a, b) => {
      // 申购状态：按开放程度排序
      if (_lofSortKey === '申购状态') {
        const va = _buyWeight(a), vb = _buyWeight(b);
        return _lofSortDir === 'asc' ? va - vb : vb - va;
      }
      if (_lofSortKey === '赎回状态') {
        const va = _sellStatusRank[a['赎回状态']] ?? -1;
        const vb = _sellStatusRank[b['赎回状态']] ?? -1;
        return _lofSortDir === 'asc' ? va - vb : vb - va;
      }
      if (TEXT_KEYS.has(_lofSortKey)) {
        const va = a[_lofSortKey] || '', vb = b[_lofSortKey] || '';
        return _lofSortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
      }
      let va = parseFloat(a[_lofSortKey]), vb = parseFloat(b[_lofSortKey]);
      if (isNaN(va)) va = _lofSortDir === 'asc' ? Infinity : -Infinity;
      if (isNaN(vb)) vb = _lofSortDir === 'asc' ? Infinity : -Infinity;
      return _lofSortDir === 'asc' ? va - vb : vb - va;
    });
  }

  function _icon(key) {
    if (_lofSortKey !== key) return '<span class="sort-icon">⇅</span>';
    return _lofSortDir === 'asc' ? '<span class="sort-icon active">↑</span>' : '<span class="sort-icon active">↓</span>';
  }
  function _th(label, key, cls = 'num') {
    return `<th class="${cls} sortable" onclick="sortLofList('${key}')">${label}${_icon(key)}</th>`;
  }

  const thead = `<tr>
    ${_th('代码', '代码', '')}
    ${_th('名称', '名称', '')}
    ${_th('最新价', '最新价')}
    ${_th('参考净值', '参考净值')}
    ${_th('净值来源', '净值来源', 'center')}
    ${_th('溢价率', '溢价率')}
    ${_th('申购费', '申购费率')}
    ${_th('净溢价', '净溢价')}
    ${_th('净折价', '净折价')}
    ${_th('涨跌幅', '涨跌幅')}
    ${_th('成交额(万)', '成交额')}
    ${_th('申购状态', '申购状态', 'center')}
    ${_th('赎回状态', '赎回状态', 'center')}
    ${_th('赎回费', '赎回费短线')}
    ${_th('免赎费天数', '免赎费天数')}
    <th class="lof-fee-detail-th">赎回费分档明细</th>
    ${_th('下一开放日', '下一开放日', 'center')}
    ${_th('基金类型', '基金类型', 'center')}
  </tr>`;

  const tbody = sorted.map(f => {
    const price = f['最新价'] != null ? parseFloat(f['最新价']).toFixed(3) : '-';
    const nav = f['参考净值'] != null ? parseFloat(f['参考净值']).toFixed(4) : '-';
    const src = f['净值来源'] || '-';
    // 溢价率：>0 溢价(红/pos)，<0 折价(绿/neg)
    let prCell = '<td class="num">-</td>';
    if (f['溢价率'] != null) {
      const pr = parseFloat(f['溢价率']);
      const cls = pr >= 0 ? 'pos' : 'neg';
      const txt = (pr >= 0 ? '+' : '') + pr.toFixed(2) + '%';
      prCell = `<td class="num"><span class="premium-tag ${cls}">${txt}</span></td>`;
    }
    let chgCell = '<td class="num">-</td>';
    if (f['涨跌幅'] != null) {
      const ch = parseFloat(f['涨跌幅']);
      const cls = ch >= 0 ? 'pos' : 'neg';
      chgCell = `<td class="num"><span class="${cls}">${(ch >= 0 ? '+' : '') + ch.toFixed(2)}%</span></td>`;
    }
    // 申购费率
    const feeCell = f['申购费率'] != null
      ? `<td class="num">${parseFloat(f['申购费率']).toFixed(2)}%</td>`
      : '<td class="num">-</td>';
    // 净溢价 = 溢价率 − 申购费（真实套利空间）：>0 才可能套利
    let netCell = '<td class="num">-</td>';
    if (f['净溢价'] != null) {
      const np = parseFloat(f['净溢价']);
      const cls = np >= 0 ? 'pos' : 'neg';
      const txt = (np >= 0 ? '+' : '') + np.toFixed(2) + '%';
      netCell = `<td class="num"><span class="premium-tag ${cls}">${txt}</span></td>`;
    }
    // 净折价 = |折价率| − 短线赎回费（折价套利空间）：>0 才可能套利
    let netDiscCell = '<td class="num">-</td>';
    if (f['净折价'] != null) {
      const nd = parseFloat(f['净折价']);
      const cls = nd >= 0 ? 'pos' : 'neg';
      const txt = (nd >= 0 ? '+' : '') + nd.toFixed(2) + '%';
      netDiscCell = `<td class="num"><span class="premium-tag ${cls}">${txt}</span></td>`;
    }
    // 赎回费（短线 <7天）
    const redeemFeeCell = f['赎回费短线'] != null
      ? `<td class="num">${parseFloat(f['赎回费短线']).toFixed(2)}%</td>`
      : '<td class="num">-</td>';
    // 免赎费天数：持有满 N 天赎回费降为 0；越小越利于套利，高亮短周期
    let freeDaysCell = '<td class="num">-</td>';
    if (f['免赎费天数'] != null) {
      const fd = parseInt(f['免赎费天数']);
      const cls = fd <= 30 ? 'pos' : (fd >= 365 ? 'neg' : '');
      freeDaysCell = `<td class="num"><span class="${cls}">${fd}天</span></td>`;
    }
    // 成交额：元 → 万元
    const amt = f['成交额'] != null ? (parseFloat(f['成交额']) / 10000).toFixed(1) : '-';
    // 申购状态：限大额时附带单日限额
    let buyStatus = f['申购状态'] || '-';
    if (f['申购状态'] === '限大额' && f['日累计限定金额'] != null && f['日累计限定金额'] > 0) {
      buyStatus = `限大额(${_fmtAmount(f['日累计限定金额'])})`;
    }
    const sellStatus = f['赎回状态'] || '-';
    const nextOpen = f['下一开放日'] || '-';
    const fundType = f['基金类型'] || '-';
    // 赎回费分档明细：把每档染色（0费绿色，高费红色），便于快速识别
    let feeDetailCell = '<td class="lof-fee-detail" style="color:#bbb">-</td>';
    const tiers = f['赎回费分档'];
    if (Array.isArray(tiers) && tiers.length) {
      const segs = tiers.map(t => {
        const lo = t.min_day, hi = t.max_day, fee = t.fee;
        let label;
        if (lo == null && hi == null) label = _escapeHtml(t.term || '?');  // 无法量化（如封闭期）用原文
        else if ((lo === 0 || lo == null) && hi) label = `&lt;${hi}天`;
        else if (hi == null) label = `≥${lo}天`;
        else label = `${lo}-${hi}天`;
        const feeStr = (fee % 1 === 0 ? fee.toFixed(0) : fee.toString()) + '%';
        const cls = fee === 0 ? 'pos' : (fee >= 1 ? 'neg' : '');
        return `<span class="lof-fee-seg"><span class="lof-fee-seg-days">${label}</span> <span class="${cls}">${feeStr}</span></span>`;
      });
      feeDetailCell = `<td class="lof-fee-detail">${segs.join('<span class="lof-fee-sep">·</span>')}</td>`;
    }
    return `<tr>
      <td>${f['代码'] || '-'}</td>
      <td>${f['名称'] || '-'}</td>
      <td class="num">${price}</td>
      <td class="num">${nav}</td>
      <td class="center" style="color:#888;font-size:.85em">${src}</td>
      ${prCell}
      ${feeCell}
      ${netCell}
      ${netDiscCell}
      ${chgCell}
      <td class="num">${amt}</td>
      <td class="center">${buyStatus}</td>
      <td class="center">${sellStatus}</td>
      ${redeemFeeCell}
      ${freeDaysCell}
      ${feeDetailCell}
      <td class="center" style="font-size:.85em">${nextOpen}</td>
      <td class="center" style="font-size:.85em">${fundType}</td>
    </tr>`;
  }).join('');

  el.innerHTML = `
  <div class="bond-list-table-wrap">
    <table class="bond-list-table">
      <thead>${thead}</thead>
      <tbody>${tbody}</tbody>
    </table>
  </div>`;
}
