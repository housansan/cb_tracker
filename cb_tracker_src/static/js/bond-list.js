// ── 列表视图 ─────────────────────────────────────────────────────
let _allBonds = [];
let _filteredBonds = [];
let _selectedRatings = new Set(); // 已选中的信用评级
let _sortKey = null;   // 当前排序字段
let _sortDir = 'asc';  // 'asc' | 'desc'
let _delistFilter = 'active'; // 'active' | 'delisted' | 'pending' | 'all'
let _freezeCols = 3; // 冻结前几列：0 | 1 | 2 | 3

let _positions = [];
let _alerts = [];

// 切换筛选面板显示/隐藏
function toggleFilterPanel() {
  const panel = document.getElementById('filterPanel');
  const btn = document.querySelector('.filter-toggle-btn');
  panel.classList.toggle('open');
  btn.classList.toggle('active');
}
async function loadBondList() {
  console.log('[bond-list.js] loadBondList 开始执行');
  const listContentEl = document.getElementById('listContent');
  console.log('[bond-list.js] 获取 listContent 元素:', listContentEl);
  listContentEl.innerHTML = '<div class="list-loading">正在加载可转债列表...</div>';
  try {
    console.log('[bond-list.js] 开始请求 /api/bond_list 接口');
    const res  = await fetch('/api/bond_list');
    console.log('[bond-list.js] 接口响应状态:', res.status);
    const json = await res.json();
    console.log('[bond-list.js] 接口返回数据:', { success: json.success, dataLength: json.data?.length, pending_fill: json.pending_fill });
    if (!json.success) {
      const errorMsg = `⚠ ${json.message}`;
      console.error('[bond-list.js] 接口返回失败:', json.message);
      listContentEl.innerHTML = `<div style="color:#c62828;padding:24px">${errorMsg}</div>`;
      return;
    }
    _allBonds = json.data;
    console.log('[bond-list.js] 已加载债券数据条数:', _allBonds.length);
    console.log('[bond-list.js] 开始构建评级选项');
    _buildRatingOptions(_allBonds);
    console.log('[bond-list.js] 开始执行 filterList');
    filterList(); // 应用默认过滤（在市）后再渲染
    // 后台有补全任务时，分两次静默刷新（补全完后缓存会清除）
    if (json.pending_fill) {
      console.log('[bond-list.js] 检测到待补全数据，将定时刷新');
      // 第一次：35s 后刷（补全快的数据已写入）
      // 第二次：150s 后再刷（全部补全完）
      [35000, 150000].forEach(delay => {
        setTimeout(() => {
          console.log(`[bond-list.js] 定时刷新债券列表，延迟: ${delay}ms`);
          fetch('/api/bond_list?_bust=' + Date.now())
            .then(r => r.json())
            .then(j => {
              if (j.success && j.data) {
                console.log('[bond-list.js] 定时刷新完成，新数据条数:', j.data.length);
                _allBonds = j.data;
                _buildRatingOptions(_allBonds);
                filterList();
              }
            })
            .catch((err) => {
              console.error('[bond-list.js] 定时刷新失败:', err);
            });
        }, delay);
      });
    }
    console.log('[bond-list.js] loadBondList 执行完成');
  } catch(e) {
    console.error('[bond-list.js] 加载债券列表失败:', e);
    listContentEl.innerHTML = `<div style="color:#c62828;padding:24px">⚠ 加载失败：${e.message}</div>`;
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

  // 更新筛选计数
  _updateFilterCount();

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
    // 转股价值区间筛选
    const convertValueMin = parseFloat(document.getElementById('convertValueMin').value);
    const convertValueMax = parseFloat(document.getElementById('convertValueMax').value);
    if (!isNaN(convertValueMin) || !isNaN(convertValueMax)) {
      const convertValue = parseFloat(b['转股价值']);
      if (isNaN(convertValue)) return false;
      if (!isNaN(convertValueMin) && convertValue < convertValueMin) return false;
      if (!isNaN(convertValueMax) && convertValue > convertValueMax) return false;
    }
    // 双低值区间筛选
    const doubleLowMin = parseFloat(document.getElementById('doubleLowMin').value);
    const doubleLowMax = parseFloat(document.getElementById('doubleLowMax').value);
    if (!isNaN(doubleLowMin) || !isNaN(doubleLowMax)) {
      const dl = parseFloat(b['双低值']);
      if (isNaN(dl)) return false;
      if (!isNaN(doubleLowMin) && dl < doubleLowMin) return false;
      if (!isNaN(doubleLowMax) && dl > doubleLowMax) return false;
    }
    // 到期收益率区间筛选
    const ytmMin = parseFloat(document.getElementById('ytmMin').value);
    const ytmMax = parseFloat(document.getElementById('ytmMax').value);
    if (!isNaN(ytmMin) || !isNaN(ytmMax)) {
      const ytm = parseFloat(b['到期收益率']);
      if (isNaN(ytm)) return false;
      if (!isNaN(ytmMin) && ytm < ytmMin) return false;
      if (!isNaN(ytmMax) && ytm > ytmMax) return false;
    }
    // 剩余年限区间筛选（仅对有到期日期的债券生效）
    const remainYearsMin = parseFloat(document.getElementById('remainYearsMin').value);
    const remainYearsMax = parseFloat(document.getElementById('remainYearsMax').value);
    if (!isNaN(remainYearsMin) || !isNaN(remainYearsMax)) {
      const ed2 = b['到期日期'] || '';
      if (ed2.length !== 8) return false;
      const expireMs2 = new Date(`${ed2.slice(0,4)}-${ed2.slice(4,6)}-${ed2.slice(6,8)}`).getTime();
      const diffYears = (expireMs2 - Date.now()) / (1000 * 60 * 60 * 24 * 365.25);
      if (!isNaN(remainYearsMin) && diffYears < remainYearsMin) return false;
      if (!isNaN(remainYearsMax) && diffYears > remainYearsMax) return false;
    }
    // 剩余规模区间筛选（亿）
    const remainScaleMin = parseFloat(document.getElementById('remainScaleMin').value);
    const remainScaleMax = parseFloat(document.getElementById('remainScaleMax').value);
    if (!isNaN(remainScaleMin) || !isNaN(remainScaleMax)) {
      const scale = parseFloat(b['剩余规模']);
      if (isNaN(scale)) return false;
      if (!isNaN(remainScaleMin) && scale < remainScaleMin) return false;
      if (!isNaN(remainScaleMax) && scale > remainScaleMax) return false;
    }
    // 发行规模区间筛选（亿）
    const issueScaleMin = parseFloat(document.getElementById('issueScaleMin').value);
    const issueScaleMax = parseFloat(document.getElementById('issueScaleMax').value);
    if (!isNaN(issueScaleMin) || !isNaN(issueScaleMax)) {
      const issueScale = parseFloat(b['发行规模']);
      if (isNaN(issueScale)) return false;
      if (!isNaN(issueScaleMin) && issueScale < issueScaleMin) return false;
      if (!isNaN(issueScaleMax) && issueScale > issueScaleMax) return false;
    }
    // 正股价区间筛选
    const stockPriceMin = parseFloat(document.getElementById('stockPriceMin').value);
    const stockPriceMax = parseFloat(document.getElementById('stockPriceMax').value);
    if (!isNaN(stockPriceMin) || !isNaN(stockPriceMax)) {
      const sp = parseFloat(b['正股价']);
      if (isNaN(sp)) return false;
      if (!isNaN(stockPriceMin) && sp < stockPriceMin) return false;
      if (!isNaN(stockPriceMax) && sp > stockPriceMax) return false;
    }
    // 正股PB区间筛选
    const stockPbMin = parseFloat(document.getElementById('stockPbMin').value);
    const stockPbMax = parseFloat(document.getElementById('stockPbMax').value);
    if (!isNaN(stockPbMin) || !isNaN(stockPbMax)) {
      const stockPb = parseFloat(b['正股PB']);
      if (isNaN(stockPb)) return false;
      if (!isNaN(stockPbMin) && stockPb < stockPbMin) return false;
      if (!isNaN(stockPbMax) && stockPb > stockPbMax) return false;
    }
    // 正股市值区间筛选（亿）
    const stockMarketCapMin = parseFloat(document.getElementById('stockMarketCapMin').value);
    const stockMarketCapMax = parseFloat(document.getElementById('stockMarketCapMax').value);
    if (!isNaN(stockMarketCapMin) || !isNaN(stockMarketCapMax)) {
      const stockMarketCap = parseFloat(b['正股市值']);
      if (isNaN(stockMarketCap)) return false;
      if (!isNaN(stockMarketCapMin) && stockMarketCap < stockMarketCapMin) return false;
      if (!isNaN(stockMarketCapMax) && stockMarketCap > stockMarketCapMax) return false;
    }
    // 根据 _delistFilter 过滤状态
    // 名称含"退"字（如"普利退债"）视为已退市，即使接口未返回退市日期
    const nameHasRetired = (b['债券简称'] || '').includes('退');
    // 到期日期已过 → 视为已退市
    const ed = b['到期日期'] || '';
    const isExpired = ed.length === 8 && new Date(`${ed.slice(0,4)}-${ed.slice(4,6)}-${ed.slice(6,8)}`).getTime() < Date.now();
    const isDelisted = !!b['退市日期'] || nameHasRetired || isExpired; // 退市日期有值 或 名称含"退" 或 已到期 → 已退市
    // 待上市：没有上市日期 且 没有债现价（有债现价则必定已上市）且 非退市
    const hasPrice_  = b['债现价'] != null;
    const isPending  = !b['上市日期'] && !hasPrice_ && !isDelisted;
    const isActive   = !isPending && !isDelisted;         // 已上市且未退市 → 在市
    if (_delistFilter === 'active'   && !isActive)   return false;
    if (_delistFilter === 'delisted' && !isDelisted)  return false;
    if (_delistFilter === 'pending'  && !isPending)   return false;

    // 市场筛选（通过正股代码前缀判断）
    const marketRadio = document.querySelector('input[name="marketFilter"]:checked');
    const selectedMarket = marketRadio ? marketRadio.value : 'all';
    if (selectedMarket !== 'all') {
      const stockCode = (b['正股代码'] || '').trim();
      let market = '';
      if (/^(600|601|603|605|609)/.test(stockCode)) market = 'sh';
      else if (/^(000|001|002|003)/.test(stockCode)) market = 'sz';
      else if (/^(688|689)/.test(stockCode)) market = 'kcb';
      else if (/^(300|301)/.test(stockCode)) market = 'cyb';
      if (market !== selectedMarket) return false;
    }

    // 上市时间筛选
    const listingDateMin = document.getElementById('listingDateMin').value; // YYYY-MM-DD
    const listingDateMax = document.getElementById('listingDateMax').value;
    if (listingDateMin || listingDateMax) {
      const ld = b['上市日期'] || '';
      if (ld.length !== 8) return false;
      const ldFormatted = `${ld.slice(0,4)}-${ld.slice(4,6)}-${ld.slice(6,8)}`;
      if (listingDateMin && ldFormatted < listingDateMin) return false;
      if (listingDateMax && ldFormatted > listingDateMax) return false;
    }

    // 强赎状态筛选
    const filterStrongRedeem = document.getElementById('filterStrongRedeem');
    if (filterStrongRedeem && filterStrongRedeem.checked) {
      if (!b['强赎状态'] || b['强赎状态'] !== '强赎中') return false;
    }
    const filterNearStrongRedeem = document.getElementById('filterNearStrongRedeem');
    if (filterNearStrongRedeem && filterNearStrongRedeem.checked) {
      if (!b['强赎状态'] || b['强赎状态'] !== '临近强赎') return false;
    }

    // 回售状态筛选
    const filterPutback = document.getElementById('filterPutback');
    if (filterPutback && filterPutback.checked) {
      if (!b['回售状态'] || b['回售状态'] !== '回售中') return false;
    }
    const filterNearPutback = document.getElementById('filterNearPutback');
    if (filterNearPutback && filterNearPutback.checked) {
      if (!b['回售状态'] || b['回售状态'] !== '临近回售') return false;
    }

    return true;
  });
  renderBondList(_filteredBonds);
}

// 更新筛选计数
function _updateFilterCount() {
  let count = 0;
  // 检查所有数值区间筛选输入
  const filterIds = [
    'priceMin', 'priceMax', 'premiumMin', 'premiumMax',
    'convertValueMin', 'convertValueMax', 'ytmMin', 'ytmMax',
    'remainYearsMin', 'remainYearsMax', 'remainScaleMin', 'remainScaleMax',
    'issueScaleMin', 'issueScaleMax', 'stockPriceMin', 'stockPriceMax',
    'stockPbMin', 'stockPbMax', 'stockMarketCapMin', 'stockMarketCapMax'
  ];
  filterIds.forEach(id => {
    const el = document.getElementById(id);
    if (el && el.value) count++;
  });

  // 检查上市时间
  if (document.getElementById('listingDateMin')?.value) count++;
  if (document.getElementById('listingDateMax')?.value) count++;

  // 检查市场选择
  const marketRadio = document.querySelector('input[name="marketFilter"]:checked');
  if (marketRadio && marketRadio.value !== 'all') count++;

  // 检查信用评级
  if (_selectedRatings.size > 0) count++;

  // 检查状态
  const delistRadio = document.querySelector('input[name="delistFilter"]:checked');
  if (delistRadio && delistRadio.value !== 'active') count++;

  // 检查复选框
  ['filterStrongRedeem', 'filterNearStrongRedeem', 'filterPutback', 'filterNearPutback'].forEach(id => {
    const el = document.getElementById(id);
    if (el && el.checked) count++;
  });

  // 更新显示
  const countEl = document.getElementById('filterCount');
  if (countEl) {
    countEl.textContent = count > 0 ? count : '';
    countEl.style.display = count > 0 ? 'inline-flex' : 'none';
  }
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
  console.log('[bond-list.js] renderBondList 开始执行，传入债券条数:', bonds?.length);
  try {
    // 排序
    let sorted = [...bonds];
    console.log('[bond-list.js] 开始排序，排序键:', _sortKey, '方向:', _sortDir);
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
      console.log('[bond-list.js] 排序完成，前3条数据:', sorted.slice(0, 3));
    }

    const listCountEl = document.getElementById('listCount');
    console.log('[bond-list.js] 获取 listCount 元素:', listCountEl);
    listCountEl.textContent = `共 ${bonds.length} 只`;
    
    const listContentEl = document.getElementById('listContent');
    console.log('[bond-list.js] 获取 listContent 元素:', listContentEl);
    
    if (!bonds.length) {
      console.log('[bond-list.js] 债券列表为空，显示暂无数据');
      listContentEl.innerHTML = '<div style="color:#aaa;padding:24px;text-align:center">暂无数据</div>';
      return;
    }
    console.log('[bond-list.js] 开始生成表格 HTML');
  const sample = bonds[0];
  const hasPremium    = '转股溢价率' in sample;
  const hasPrice      = '债现价' in sample;
  const hasRating     = '信用评级' in sample;
  const hasRemain     = '剩余规模' in sample;
  const hasIssueSize  = '发行规模' in sample;
  const hasStockPrice = '正股价' in sample;
  const hasConvertValue = '转股价值' in sample;
  const hasYTM        = '到期收益率' in sample;
  const hasStockPB    = '正股PB' in sample;
  const hasStockMarketCap = '正股市值' in sample;
  const hasStrongRedeem = '强赎状态' in sample;
  const hasPutback    = '回售状态' in sample;
  const hasListingDate = '上市日期' in sample;
  const hasDelistDate  = '退市日期' in sample;
  const hasExpireDate  = '到期日期' in sample;
  const hasDoubleLow  = '双低值' in sample;
  const hasRedeemProgress = '距离强赎线' in sample;
  const hasXiuzheng   = '下修博弈' in sample;
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
  // 列宽：债券代码=88px，债券名称=90px，正股代码=130px
  const COL_WIDTHS = [88, 90, 130]; // 前三列宽度
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
  if (hasConvertValue) thead += `<th class="num sortable" onclick="sortList('转股价值')">转股价值${_sortIcon('转股价值')}</th>`;
  if (hasPremium)     thead += `<th class="num sortable" onclick="sortList('转股溢价率')">转股溢价率${_sortIcon('转股溢价率')}</th>`;
  if (hasDoubleLow)   thead += `<th class="num sortable" onclick="sortList('双低值')">双低值${_sortIcon('双低值')}</th>`;
  if (hasRedeemProgress) thead += `<th class="num">强赎进度</th>`;
  if (hasXiuzheng)    thead += `<th class="center">下修博弈</th>`;
  if (hasYTM)        thead += `<th class="num sortable" onclick="sortList('到期收益率')">到期收益率${_sortIcon('到期收益率')}</th>`;
  if (hasRating)      thead += `<th class="center sortable" onclick="sortList('信用评级')">信用评级${_sortIcon('信用评级')}</th>`;
  if (hasRemain)      thead += `<th class="num sortable" onclick="sortList('剩余规模')">剩余规模(亿)${_sortIcon('剩余规模')}</th>`;
  if (hasIssueSize)   thead += `<th class="num sortable" onclick="sortList('发行规模')">发行规模(亿)${_sortIcon('发行规模')}</th>`;
  if (hasStockPB)     thead += `<th class="num sortable" onclick="sortList('正股PB')">正股PB${_sortIcon('正股PB')}</th>`;
  if (hasStockMarketCap) thead += `<th class="num sortable" onclick="sortList('正股市值')">正股市值(亿)${_sortIcon('正股市值')}</th>`;
  if (hasStrongRedeem) thead += `<th class="center">强赎状态</th>`;
  if (hasPutback)    thead += `<th class="center">回售状态</th>`;
  if (hasListingDate) thead += `<th class="center sortable" onclick="sortList('上市日期')">上市日期${_sortIcon('上市日期')}</th>`;
  if (hasDelistDate)  thead += `<th class="center sortable" onclick="sortList('退市日期')">退市日期${_sortIcon('退市日期')}</th>`;
  thead += '</tr>';

  console.log('[bond-list.js] 开始生成 tbody HTML，排序后数据条数:', sorted.length);
  const tbody = sorted.map((b, index) => {
    console.log(`[bond-list.js] 渲染第 ${index + 1} 条债券:`, b['债券代码'], b['债券简称']);
    const code = b['债券代码'] || '-';
    const name = b['债券简称'] || '-';
    const sc   = b['正股代码'] || '-';
    const sn   = b['正股简称'] || '-';
    let row = `<tr>
      <td${_frozenTdTextStyle(0)}><a class="bond-code-link" onclick="openDetail('${code}')">${code}</a></td>
      <td${_frozenTdTextStyle(1)}>${name}${_positions.some(p => String(p.bond_code) === String(code)) ? '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#4caf50;margin-left:4px;vertical-align:middle"></span>' : ''}</td>
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
    if (hasConvertValue) {
      const cv = b['转股价值'] != null ? parseFloat(b['转股价值']).toFixed(2) : '-';
      row += `<td class="num">${cv}</td>`;
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
    if (hasDoubleLow) {
      const dl = b['双低值'] != null ? parseFloat(b['双低值']).toFixed(2) : '-';
      row += `<td class="num">${dl}</td>`;
    }
    if (hasRedeemProgress) {
      const rp = b['距离强赎线'];
      if (rp != null) {
        const rpn = parseFloat(rp);
        let rpc = '';
        if (rpn >= 100) rpc = 'neg';
        else if (rpn >= 90) rpc = 'pos';
        row += `<td class="num"><span class="${rpc}">${rpn.toFixed(1)}%</span></td>`;
      } else {
        row += '<td class="num">-</td>';
      }
    }
    if (hasXiuzheng) {
      const xz = b['下修博弈'];
      row += `<td class="center">${xz ? '<span class="pos">是</span>' : '-'}</td>`;
    }
    if (hasYTM) {
      const ytm = b['到期收益率'] != null ? parseFloat(b['到期收益率']).toFixed(2) : '-';
      row += `<td class="num">${ytm}</td>`;
    }
    if (hasRating) {
      const r = _pureRating(b['信用评级']) || '-';
      row += `<td class="center"><span class="rating-tag">${r}</span></td>`;
    }
    if (hasRemain) {
const rv = b['剩余规模'] != null ? parseFloat(b['剩余规模']).toFixed(2) : '-';
      row += `<td class="num">${rv}</td>`;
    }
    if (hasIssueSize) {
      const iv = b['发行规模'] != null ? parseFloat(b['发行规模']).toFixed(2) : '-';
      row += `<td class="num">${iv}</td>`;
    }
    if (hasStockPB) {
      const pb = b['正股PB'] != null ? parseFloat(b['正股PB']).toFixed(2) : '-';
      row += `<td class="num">${pb}</td>`;
    }
    if (hasStockMarketCap) {
      const mc = b['正股市值'] != null ? parseFloat(b['正股市值']).toFixed(2) : '-';
      row += `<td class="num">${mc}</td>`;
    }
    if (hasStrongRedeem) {
      const sr = b['强赎状态'] || '-';
      row += `<td class="center">${sr}</td>`;
    }
    if (hasPutback) {
      const pp = b['回售状态'] || '-';
      row += `<td class="center">${pp}</td>`;
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

  console.log('[bond-list.js] tbody HTML 生成完成，长度:', tbody.length);
  console.log('[bond-list.js] thead HTML:', thead.substring(0, 200) + '...');
  
  const fullHtml = `
    <div class="bond-list-table-wrap">
      <table class="bond-list-table">
        <thead>${thead}</thead>
        <tbody>${tbody}</tbody>
      </table>
    </div>`;
  
  console.log('[bond-list.js] 准备设置 listContent 的 innerHTML，HTML 总长度:', fullHtml.length);
  listContentEl.innerHTML = fullHtml;
  console.log('[bond-list.js] listContent 的 innerHTML 设置完成');
  console.log('[bond-list.js] 设置后 listContent 的子元素个数:', listContentEl.children.length);
  
  // 检查表格是否成功渲染
  const tableEl = listContentEl.querySelector('.bond-list-table');
  console.log('[bond-list.js] 查找表格元素:', tableEl);
  if (tableEl) {
    console.log('[bond-list.js] 表格行数:', tableEl.querySelectorAll('tr').length);
  }
  
  console.log('[bond-list.js] renderBondList 执行完成');
  } catch (e) {
    console.error('[bond-list.js] renderBondList 执行出错:', e);
    const listContentEl = document.getElementById('listContent');
    if (listContentEl) {
      listContentEl.innerHTML = `<div style="color:#c62828;padding:24px">渲染出错：${e.message}</div>`;
    }
  }
}

// ── 快捷策略筛选 ────────────────────────────────────────────────────
function setQuickFilter(preset) {
  // 先重置所有可变数字输入框
  ['priceMin','priceMax','premiumMin','premiumMax','convertValueMin','convertValueMax',
   'ytmMin','ytmMax','remainYearsMin','remainYearsMax',
   'remainScaleMin','remainScaleMax','issueScaleMin','issueScaleMax',
   'stockPriceMin','stockPriceMax','stockPbMin','stockPbMax',
   'stockMarketCapMin','stockMarketCapMax'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  // 重置上市时间
  const ldm = document.getElementById('listingDateMin');
  const ldx = document.getElementById('listingDateMax');
  if (ldm) ldm.value = '';
  if (ldx) ldx.value = '';
  // 重置市场为全部
  const allMarketRadio = document.querySelector('input[name="marketFilter"][value="all"]');
  if (allMarketRadio) allMarketRadio.checked = true;
  // 保证"状态"为在市（快捷策略只针对在市债券）
  const activeRadio = document.querySelector('input[name="delistFilter"][value="active"]');
  if (activeRadio) { activeRadio.checked = true; _delistFilter = 'active'; }

  switch (preset) {
    case 'sub100':
      // 破面：债现价 < 100
      document.getElementById('priceMax').value = '100';
      break;
    case 'low_price':
      // 低价债：100 ~ 110
      document.getElementById('priceMin').value = '100';
      document.getElementById('priceMax').value = '110';
      break;
    case 'low_premium':
      // 低溢价：转股溢价率 < 20%
      document.getElementById('premiumMax').value = '20';
      break;
    case 'double_low':
      // 双低策略：债现价 < 120 且 转股溢价率 < 30%
      document.getElementById('priceMax').value = '120';
      document.getElementById('premiumMax').value = '30';
      break;
    case 'expire_soon':
      // 到期≤2年
      document.getElementById('remainYearsMax').value = '2';
      break;
    case 'small_scale':
      // 小盘债：剩余规模 < 3亿
      document.getElementById('remainScaleMax').value = '3';
      break;
    case 'large_scale':
      // 大盘债：剩余规模 ≥ 10亿
      document.getElementById('remainScaleMin').value = '10';
      break;
  }
  // 高亮当前激活的快捷按钮
  document.querySelectorAll('.quick-filter-btn:not(.reset-btn)').forEach(btn => btn.classList.remove('active'));
  event.currentTarget.classList.add('active');
  filterList();
}

// ── 重置所有筛选条件 ─────────────────────────────────────────────────
function resetFilters() {
  document.getElementById('listSearch').value = '';
  ['priceMin','priceMax','premiumMin','premiumMax','convertValueMin','convertValueMax',
   'ytmMin','ytmMax','remainYearsMin','remainYearsMax',
   'remainScaleMin','remainScaleMax','issueScaleMin','issueScaleMax',
   'stockPriceMin','stockPriceMax','stockPbMin','stockPbMax',
   'stockMarketCapMin','stockMarketCapMax'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  // 重置上市时间
  const ldm = document.getElementById('listingDateMin');
  const ldx = document.getElementById('listingDateMax');
  if (ldm) ldm.value = '';
  if (ldx) ldx.value = '';
  // 重置市场为全部
  const allMarketRadio2 = document.querySelector('input[name="marketFilter"][value="all"]');
  if (allMarketRadio2) allMarketRadio2.checked = true;
  // 重置状态为"在市"
  const activeRadio = document.querySelector('input[name="delistFilter"][value="active"]');
  if (activeRadio) { activeRadio.checked = true; _delistFilter = 'active'; }
  // 清除评级选择
  clearRatingFilter();
  // 清除复选框
  ['filterStrongRedeem', 'filterNearStrongRedeem', 'filterPutback', 'filterNearPutback'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.checked = false;
  });
  // 取消快捷按钮高亮
  document.querySelectorAll('.quick-filter-btn').forEach(btn => btn.classList.remove('active'));
  filterList();
}


