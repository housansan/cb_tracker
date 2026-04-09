// ── 列表视图 ─────────────────────────────────────────────────────
let _allBonds = [];
let _filteredBonds = [];
let _selectedRatings = new Set(); // 已选中的信用评级
let _sortKey = null;   // 当前排序字段
let _sortDir = 'asc';  // 'asc' | 'desc'
let _delistFilter = 'active'; // 'active' | 'delisted' | 'pending' | 'all'
let _freezeCols = 2; // 冻结前几列：0 | 1 | 2
async function loadBondList() {
  document.getElementById('listContent').innerHTML = '<div class="list-loading">正在加载可转债列表...</div>';
  try {
    const res  = await fetch('/api/bond_list');
    const json = await res.json();
    if (!json.success) {
      document.getElementById('listContent').innerHTML = `<div style="color:#c62828;padding:24px">⚠ ${json.message}</div>`;
      return;
    }
    _allBonds = json.data;
    _buildRatingOptions(_allBonds);
    filterList(); // 应用默认过滤（在市）后再渲染
  } catch(e) {
    document.getElementById('listContent').innerHTML = `<div style="color:#c62828;padding:24px">⚠ 加载失败：${e.message}</div>`;
  }
}

// 去掉评级后缀（如 AA+sti → AA+），只保留纯评级字母和符号
function _pureRating(r) {
  if (!r || r === '-') return r;
  return r.replace(/[a-z]+$/, '').trim();
}

// 构建信用评级选项
function _buildRatingOptions(bonds) {
  // 收集所有评级并排序（AAA > AA+ > AA > AA- > A+ ...）
  const ratingOrder = ['AAA', 'AA+', 'AA', 'AA-', 'A+', 'A', 'A-', 'BBB+', 'BBB', 'BBB-'];
  const ratings = [...new Set(bonds.map(b => _pureRating(b['信用评级'])).filter(r => r && r !== '-'))];
  ratings.sort((a, b) => {
    const ia = ratingOrder.indexOf(a);
    const ib = ratingOrder.indexOf(b);
    if (ia === -1 && ib === -1) return a.localeCompare(b);
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });

  const container = document.getElementById('ratingOptions');
  container.innerHTML = ratings.map(r => `
    <label class="rating-option">
      <input type="checkbox" value="${r}" onchange="onRatingChange(this)" />
      <span class="rating-tag">${r}</span>
    </label>
  `).join('');
}

// 评级复选框变化
function onRatingChange(checkbox) {
  if (checkbox.checked) {
    _selectedRatings.add(checkbox.value);
  } else {
    _selectedRatings.delete(checkbox.value);
  }
  _updateRatingTriggerText();
  filterList();
}

// 更新触发按钮文字
function _updateRatingTriggerText() {
  const el = document.getElementById('ratingTriggerText');
  if (_selectedRatings.size === 0) {
    el.textContent = '信用评级';
  } else {
    el.textContent = [..._selectedRatings].join('、');
  }
}

// 清除评级筛选
function clearRatingFilter() {
  _selectedRatings.clear();
  document.querySelectorAll('#ratingOptions input[type=checkbox]').forEach(cb => cb.checked = false);
  _updateRatingTriggerText();
  filterList();
}

// 切换下拉显示
function toggleRatingDropdown() {
  const dropdown = document.getElementById('ratingDropdown');
  const isOpen = dropdown.classList.toggle('open');
  if (isOpen) {
    // 点击外部关闭
    setTimeout(() => {
      document.addEventListener('click', _closeRatingOnOutside, { once: true });
    }, 0);
  }
}

function _closeRatingOnOutside(e) {
  const filter = document.getElementById('ratingFilter');
  if (!filter.contains(e.target)) {
    document.getElementById('ratingDropdown').classList.remove('open');
  } else {
    // 点击的是内部，重新监听
    document.addEventListener('click', _closeRatingOnOutside, { once: true });
  }
}

// 冻结列选择变化（保留备用）
function onFreezeColChange(radio) {
  _freezeCols = parseInt(radio.value);
  renderBondList(_filteredBonds);
}

// 点击列头图钉切换冻结
function toggleFreezeCol(colIdx) {
  if (colIdx < _freezeCols) {
    _freezeCols = colIdx;      // 取消冻结此列及之后
  } else {
    _freezeCols = colIdx + 1;  // 冻结到此列（含）
  }
  renderBondList(_filteredBonds);
}

// 在市/已退市 单选变化
function onDelistFilterChange(radio) {
  _delistFilter = radio.value;
  filterList();
}

function filterList() {
  const kw = document.getElementById('listSearch').value.trim().toLowerCase();
  _filteredBonds = _allBonds.filter(b => {
    // 关键词筛选
    if (kw) {
      const match =
        (b['债券代码'] || '').toLowerCase().includes(kw) ||
        (b['债券简称'] || '').toLowerCase().includes(kw) ||
        (b['正股代码'] || '').toLowerCase().includes(kw) ||
        (b['正股简称'] || '').toLowerCase().includes(kw);
      if (!match) return false;
    }
    // 信用评级筛选
    if (_selectedRatings.size > 0) {
      if (!_selectedRatings.has(_pureRating(b['信用评级']))) return false;
    }
    // 债现价区间筛选
    const priceMin = parseFloat(document.getElementById('priceMin').value);
    const priceMax = parseFloat(document.getElementById('priceMax').value);
    if (!isNaN(priceMin) || !isNaN(priceMax)) {
      const price = parseFloat(b['债现价']);
      if (isNaN(price)) return false;
      if (!isNaN(priceMin) && price < priceMin) return false;
      if (!isNaN(priceMax) && price > priceMax) return false;
    }
    // 转股溢价率区间筛选
    const premiumMin = parseFloat(document.getElementById('premiumMin').value);
    const premiumMax = parseFloat(document.getElementById('premiumMax').value);
    if (!isNaN(premiumMin) || !isNaN(premiumMax)) {
      const premium = parseFloat(b['转股溢价率']);
      if (isNaN(premium)) return false;
      if (!isNaN(premiumMin) && premium < premiumMin) return false;
      if (!isNaN(premiumMax) && premium > premiumMax) return false;
    }
    // 根据 _delistFilter 过滤状态
    // 名称含"退"字（如"普利退债"）视为已退市，即使接口未返回退市日期
    const nameHasRetired = (b['债券简称'] || '').includes('退');
    // 到期日期已过 → 视为已退市
    const ed = b['到期日期'] || '';
    const isExpired = ed.length === 8 && new Date(`${ed.slice(0,4)}-${ed.slice(4,6)}-${ed.slice(6,8)}`).getTime() < Date.now();
    const isDelisted = !!b['退市日期'] || nameHasRetired || isExpired; // 退市日期有值 或 名称含"退" 或 已到期 → 已退市
    const isPending  = !b['上市日期'] && !isDelisted;     // 上市日期为空且非退市 → 待上市
    const isActive   = !isPending && !isDelisted;         // 已上市且未退市 → 在市
    if (_delistFilter === 'active'   && !isActive)   return false;
    if (_delistFilter === 'delisted' && !isDelisted)  return false;
    if (_delistFilter === 'pending'  && !isPending)   return false;
    return true;
  });
  renderBondList(_filteredBonds);
}

// 排序函数
function sortList(key) {
  if (_sortKey === key) {
    _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
  } else {
    _sortKey = key;
    _sortDir = 'asc';
  }
  renderBondList(_filteredBonds);
}

// 评级排序权重
const _ratingOrder = ['AAA', 'AA+', 'AA', 'AA-', 'A+', 'A', 'A-', 'BBB+', 'BBB', 'BBB-'];
function _ratingWeight(r) {
  const pure = _pureRating(r);
  const idx = _ratingOrder.indexOf(pure);
  return idx === -1 ? 999 : idx;
}

function renderBondList(bonds) {
  // 排序
  let sorted = [...bonds];
  if (_sortKey) {
    sorted.sort((a, b) => {
      let va, vb;
      if (_sortKey === '信用评级') {
        va = _ratingWeight(a['信用评级']);
        vb = _ratingWeight(b['信用评级']);
      } else {
        va = parseFloat(a[_sortKey]);
        vb = parseFloat(b[_sortKey]);
        if (isNaN(va)) va = _sortDir === 'asc' ? Infinity : -Infinity;
        if (isNaN(vb)) vb = _sortDir === 'asc' ? Infinity : -Infinity;
      }
      return _sortDir === 'asc' ? va - vb : vb - va;
    });
  }

  document.getElementById('listCount').textContent = `共 ${bonds.length} 只`;
  if (!bonds.length) {
    document.getElementById('listContent').innerHTML = '<div style="color:#aaa;padding:24px;text-align:center">暂无数据</div>';
    return;
  }
  const sample = bonds[0];
  const hasPremium    = '转股溢价率' in sample;
  const hasPrice      = '债现价' in sample;
  const hasRating     = '信用评级' in sample;
const hasRemain     = '剩余规模' in sample;
  const hasStockPrice = '正股价' in sample;
  const hasListingDate = '上市日期' in sample;
  const hasDelistDate  = '退市日期' in sample;
  const hasExpireDate  = '到期日期' in sample;
  // 仅在市状态下显示剩余年限
  const showRemainYears = hasExpireDate && _delistFilter === 'active';

  // 生成排序箭头
  function _sortIcon(key) {
    if (_sortKey !== key) return '<span class="sort-icon">⇅</span>';
    return _sortDir === 'asc'
      ? '<span class="sort-icon active">↑</span>'
      : '<span class="sort-icon active">↓</span>';
  }

  // 根据冻结列数生成 td 的 sticky 属性
  // 列宽：债券代码=100px，债券名称=100px，正股代码=100px，正股名称=100px
  const COL_WIDTHS = [100, 100, 100, 100]; // 前四列宽度
  function _frozenTdStyle(colIdx) {
    if (colIdx >= _freezeCols) return ' class="num"';
    const left = COL_WIDTHS.slice(0, colIdx).reduce((a, b) => a + b, 0);
    const isLast = colIdx === _freezeCols - 1;
    return ` class="num col-frozen${isLast ? ' col-frozen-last' : ''}" style="left:${left}px"`;
  }
  function _frozenTdTextStyle(colIdx) {
    if (colIdx >= _freezeCols) return '';
    const left = COL_WIDTHS.slice(0, colIdx).reduce((a, b) => a + b, 0);
    const isLast = colIdx === _freezeCols - 1;
    return ` class="col-frozen${isLast ? ' col-frozen-last' : ''}" style="left:${left}px"`;
  }

  // 图钉图标：已冻结高亮竖直，未冻结灰色斜放
  function _pinIcon(colIdx) {
    const frozen = colIdx < _freezeCols;
    return `<span class="col-pin-btn${frozen ? ' pinned' : ''}" onclick="event.stopPropagation();toggleFreezeCol(${colIdx})" title="${frozen ? '点击取消固定' : '点击固定此列'}">📌</span>`;
  }

  // 生成前4列可冻结列的 <th>
  function _thWrap(colIdx, label, extraClass = '') {
    const frozen = colIdx < _freezeCols;
    const isLast = frozen && colIdx === _freezeCols - 1;
    const left = COL_WIDTHS.slice(0, colIdx).reduce((a, b) => a + b, 0);
    const classes = [extraClass, frozen ? 'col-frozen' : '', isLast ? 'col-frozen-last' : ''].filter(Boolean).join(' ');
    const style = frozen ? ` style="left:${left}px"` : '';
    return `<th class="${classes || 'th-freezable'}"${style}>${label}${_pinIcon(colIdx)}</th>`;
  }

  let thead = `<tr>
    ${_thWrap(0, '债券代码')}
    ${_thWrap(1, '债券名称')}
    ${_thWrap(2, '正股代码', 'num')}
    ${_thWrap(3, '正股名称')}`;
  if (hasPrice)       thead += `<th class="num sortable" onclick="sortList('债现价')">债现价${_sortIcon('债现价')}</th>`;
  if (showRemainYears) thead += `<th class="num sortable" onclick="sortList('到期日期')">剩余年限${_sortIcon('到期日期')}</th>`;
  if (hasStockPrice)  thead += '<th class="num">正股价</th>';
  if (hasPremium)     thead += `<th class="num sortable" onclick="sortList('转股溢价率')">转股溢价率${_sortIcon('转股溢价率')}</th>`;
  if (hasRating)      thead += `<th class="center sortable" onclick="sortList('信用评级')">信用评级${_sortIcon('信用评级')}</th>`;
  if (hasRemain)      thead += `<th class="num sortable" onclick="sortList('剩余规模')">剩余规模(亿)${_sortIcon('剩余规模')}</th>`;
  if (hasListingDate) thead += `<th class="center sortable" onclick="sortList('上市日期')">上市日期${_sortIcon('上市日期')}</th>`;
  if (hasDelistDate)  thead += `<th class="center sortable" onclick="sortList('退市日期')">退市日期${_sortIcon('退市日期')}</th>`;
  thead += '</tr>';

  const tbody = sorted.map(b => {
    const code = b['债券代码'] || '-';
    const name = b['债券简称'] || '-';
    const sc   = b['正股代码'] || '-';
    const sn   = b['正股简称'] || '-';
    let row = `<tr>
      <td${_frozenTdTextStyle(0)}><a class="bond-code-link" onclick="openDetail('${code}')">${code}</a></td>
      <td${_frozenTdTextStyle(1)}>${name}</td>
      <td${_frozenTdStyle(2)}>${sc}</td>
      <td${_frozenTdTextStyle(3)}>${sn}</td>`;
    if (hasPrice) {
      const p = b['债现价'] != null ? parseFloat(b['债现价']).toFixed(3) : '-';
      row += `<td class="num">${p}</td>`;
    }
    if (showRemainYears) {
      const ed = b['到期日期'] || '';
      if (ed.length === 8) {
        const expireMs = new Date(`${ed.slice(0,4)}-${ed.slice(4,6)}-${ed.slice(6,8)}`).getTime();
        const nowMs = Date.now();
        const diffDays = Math.ceil((expireMs - nowMs) / (1000 * 60 * 60 * 24));
        if (diffDays > 0) {
          let label, cls;
          if (diffDays < 180) {
            label = `${diffDays}天`;
            cls = 'neg';
          } else {
            const years = (diffDays / 365.25).toFixed(2);
            cls = diffDays < 365 * 2 ? 'pos' : '';
            label = `${years}年`;
          }
          row += `<td class="num"><span class="${cls}">${label}</span></td>`;
        } else {
          row += `<td class="num"><span class="neg">已到期</span></td>`;
        }
      } else {
        row += `<td class="num">-</td>`;
      }
    }
    if (hasStockPrice) {
      const sp = b['正股价'] != null ? parseFloat(b['正股价']).toFixed(3) : '-';
      row += `<td class="num">${sp}</td>`;
    }
    if (hasPremium) {
      const pv = b['转股溢价率'];
      if (pv != null) {
        const pn  = parseFloat(pv);
        const cls = pn >= 0 ? 'pos' : 'neg';
        const txt = (pn >= 0 ? '+' : '') + pn.toFixed(2) + '%';
        row += `<td class="num"><span class="premium-tag ${cls}">${txt}</span></td>`;
      } else {
        row += '<td class="num">-</td>';
      }
    }
    if (hasRating) {
      const r = _pureRating(b['信用评级']) || '-';
      row += `<td class="center"><span class="rating-tag">${r}</span></td>`;
    }
    if (hasRemain) {
const rv = b['剩余规模'] != null ? parseFloat(b['剩余规模']).toFixed(2) : '-';
      row += `<td class="num">${rv}</td>`;
    }
    if (hasListingDate) {
      const ld = b['上市日期'] || '-';
      // YYYYMMDD → YYYY-MM-DD
      const ldFmt = ld.length === 8 ? `${ld.slice(0,4)}-${ld.slice(4,6)}-${ld.slice(6,8)}` : ld;
      row += `<td class="center">${ldFmt}</td>`;
    }
    if (hasDelistDate) {
      const dd = b['退市日期'] || '';
      const ddFmt = dd.length === 8 ? `${dd.slice(0,4)}-${dd.slice(4,6)}-${dd.slice(6,8)}` : (dd || '-');
      row += `<td class="center">${ddFmt}</td>`;
    }
    row += '</tr>';
    return row;
  }).join('');

  document.getElementById('listContent').innerHTML = `
    <div class="bond-list-table-wrap">
      <table class="bond-list-table">
        <thead>${thead}</thead>
        <tbody>${tbody}</tbody>
      </table>
    </div>`;
}
