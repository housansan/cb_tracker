// ── 策略筛选器 ────────────────────────────────────────────────────
let _strategyMarket = 'a_share';
let _strategySteps  = [];
let _stepCounter    = 0;
let _strategyResult = null;
let _strategySortKey = null;
let _strategySortDir = 'asc';

// ── 字段选项 ─────────────────────────────────────────────────────
const STRATEGY_FIELDS = [
  { value: 'pe',         label: 'PE（市盈率）' },
  { value: 'pb',         label: 'PB（市净率）' },
  { value: 'market_cap', label: '总市值（亿元）' },
  { value: 'net_assets', label: '净资产（亿元）' },
];

const FIELD_LABEL = Object.fromEntries(STRATEGY_FIELDS.map(f => [f.value, f.label.split('（')[0]]));

// ── 预设策略 ─────────────────────────────────────────────────────
const STRATEGY_PRESETS = {
  custom: [],
  coarse_select: [
    { type: 'dimension_union', dimensions: [
      { field: 'pe', dir: 'asc', limit: 100, skip_non_positive: true },
      { field: 'pb', dir: 'asc', limit: 100, skip_non_positive: true },
    ]},
  ],
  value_basic: [
    { type: 'range',        field: 'net_assets', op: '>=', value: 100 },
    { type: 'sort_limit',   field: 'pb',  dir: 'asc', limit: 100 },
    { type: 'sort_limit',   field: 'pe',  dir: 'asc', limit: 0   },  // limit=0：只排序不截断
    { type: 'industry_cap', max: 5, limit: 30 },                     // 边选边限，共取30只
  ],
  low_pb: [
    { type: 'sort_limit', field: 'pb', dir: 'asc', limit: 50 },
  ],
  low_pe: [
    { type: 'sort_limit', field: 'pe', dir: 'asc', limit: 50 },
  ],
  large_cap_value: [
    { type: 'range',      field: 'market_cap', op: '>=', value: 1000 },
    { type: 'sort_limit', field: 'pe',         dir: 'asc', limit: 30 },
  ],
};

// ── DB 状态和更新 ─────────────────────────────────────────────────
let _dbStatusTimer = null;

async function loadDbStatus() {
  try {
    const res  = await fetch('/api/stock_screener/db_status');
    const json = await res.json();
    if (!json.success) return;

    const mkt   = _strategyMarket;
    const meta  = json[mkt] || {};
    const textEl = document.getElementById('dbStatusText');
    const btnEl  = document.getElementById('dbUpdateBtn');

    if (json.updating || meta.updating) {
      if (textEl) textEl.textContent = '正在更新数据...';
      if (btnEl)  btnEl.disabled = true;
      clearTimeout(_dbStatusTimer);
      _dbStatusTimer = setTimeout(async () => {
        await loadDbStatus();
        // 更新完成后若当前无数据则自动加载
        if (!_strategyResult) _autoRunEmpty();
      }, 5000);
    } else {
      const dateStr = meta.last_update ? meta.last_update.slice(0, 16) : '从未更新';
      const count   = meta.stock_count  || 0;
      if (textEl) textEl.textContent = `数据截止：${dateStr}  共 ${count} 只`;
      if (btnEl)  btnEl.disabled = false;
      clearTimeout(_dbStatusTimer);
      _dbStatusTimer = null;
    }
  } catch (e) {
    const textEl = document.getElementById('dbStatusText');
    if (textEl) textEl.textContent = '状态获取失败';
  }
}

async function updateData() {
  const btn = document.getElementById('dbUpdateBtn');
  if (btn) btn.disabled = true;
  const textEl = document.getElementById('dbStatusText');
  if (textEl) textEl.textContent = '正在触发更新...';

  try {
    const res  = await fetch('/api/stock_screener/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ market: _strategyMarket }),
    });
    const json = await res.json();
    if (!json.success) {
      if (textEl) textEl.textContent = `更新失败：${json.message}`;
      if (btn) btn.disabled = false;
      return;
    }
    if (textEl) textEl.textContent = '正在后台更新，请稍候...';
    // 开始轮询
    clearTimeout(_dbStatusTimer);
    _dbStatusTimer = setTimeout(loadDbStatus, 3000);
  } catch (e) {
    if (textEl) textEl.textContent = `更新请求失败：${e.message}`;
    if (btn) btn.disabled = false;
  }
}

// ── 市场切换 ─────────────────────────────────────────────────────
function setStrategyMarket(mkt) {
  _strategyMarket = mkt;
  document.getElementById('strategyMarketA').classList.toggle('active', mkt === 'a_share');
  document.getElementById('strategyMarketHK').classList.toggle('active', mkt === 'hk_share');
  loadDbStatus();
  // 切换市场后重新加载全部数据
  _strategyResult = null;
  _runStrategyWithSteps([]);
}

// ── 预设加载 ─────────────────────────────────────────────────────
function loadPreset(name) {
  const steps = STRATEGY_PRESETS[name] || [];
  _strategySteps = steps.map(s => ({ id: ++_stepCounter, ...s }));
  renderStrategySteps();
}

// ── 步骤管理 ─────────────────────────────────────────────────────
// ── 行业选项缓存（按市场分开缓存）────────────────────────────────
let _industryOptions = {};   // { a_share: [...], hk_share: [...] }
let _industryLoadErrors = {}; // { a_share: '错误信息', hk_share: '错误信息' }
let _industryLoading = false;

async function ensureIndustriesLoaded() {
  const mkt = _strategyMarket;
  if (_industryOptions[mkt]?.length || _industryLoading) return;
  _industryLoading = true;
  try {
    const res = await fetch(`/api/stock_screener/industries?market=${mkt}`);
    const json = await res.json();
    if (json.success) {
      _industryOptions[mkt] = json.data;
      delete _industryLoadErrors[mkt];
    } else {
      _industryOptions[mkt] = [];
      _industryLoadErrors[mkt] = json.message || '行业列表加载失败';
    }
  } catch (e) {
    _industryLoadErrors[mkt] = `行业列表加载失败：${e.message}`;
    console.warn('行业列表加载失败', e);
  } finally {
    _industryLoading = false;
  }
}

// 获取当前市场的行业列表
function _currentIndustries() {
  return _industryOptions[_strategyMarket] || [];
}

// 步骤增删 ─────────────────────────────────────────────────────────
const STEP_DEFAULTS = {
  range:           { field: 'net_assets', op: '>=', value: 100 },
  sort_limit:      { field: 'pb', dir: 'asc', limit: 100 },
  industry_cap:    { max: 5 },
  industry_filter: { industries: [], mode: 'include' },
  dimension_union: { dimensions: [
    { field: 'pe', dir: 'asc', limit: 100, skip_non_positive: true },
    { field: 'pb', dir: 'asc', limit: 100, skip_non_positive: true },
  ]},
};

// 记住用户上次为每种步骤类型选择的字段，新步骤继承（避免每次重置回净资产/PB）
let _lastStepFields = {};   // { range: 'net_assets', sort_limit: 'pb' }

function addStep(type) {
  // 从现有步骤中读取用户最近一次选择的字段，作为新步骤的默认
  if (!_lastStepFields[type]) {
    const recent = [..._strategySteps].reverse().find(s => s.type === type);
    if (recent) _lastStepFields[type] = recent.field || recent.dimensions?.[0]?.field;
  }
  const defaults = { ...STEP_DEFAULTS[type] };
  if (type === 'range' && _lastStepFields.range) {
    defaults.field = _lastStepFields.range;
  }
  if (type === 'sort_limit' && _lastStepFields.sort_limit) {
    defaults.field = _lastStepFields.sort_limit;
  }
  _strategySteps.push({ id: ++_stepCounter, type, ...defaults });
  if (type === 'industry_filter') ensureIndustriesLoaded().then(renderStrategySteps);
  renderStrategySteps();
}

// 步骤字段变化时记住（供下次新增使用），并同步到步骤数据
function onStepFieldChange(id, fieldKey, value) {
  const step = _strategySteps.find(s => s.id === id);
  if (!step) return;
  step[fieldKey] = value;
  _lastStepFields[step.type] = value;
}

function deleteStep(id) {
  _strategySteps = _strategySteps.filter(s => s.id !== id);
  renderStrategySteps();
}

function moveStep(id, dir) {
  const idx = _strategySteps.findIndex(s => s.id === id);
  if (idx < 0) return;
  const nIdx = idx + dir;
  if (nIdx < 0 || nIdx >= _strategySteps.length) return;
  [_strategySteps[idx], _strategySteps[nIdx]] = [_strategySteps[nIdx], _strategySteps[idx]];
  renderStrategySteps();
}

// ── 拖拽排序（iOS 风格：按住步骤行拖动）──────────────────────────
let _dragStepId = null;   // 当前拖拽的步骤 id

function onDragStartStep(e, id) {
  _dragStepId = id;
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', String(id));
  // 被拖动行视觉高亮
  const row = document.getElementById(`sp-${id}`);
  if (row) row.classList.add('dragging');
}

function onDragEndStep(id) {
  _dragStepId = null;
  const row = document.getElementById(`sp-${id}`);
  if (row) row.classList.remove('dragging');
  // 清除所有 drag-over 标记
  document.querySelectorAll('.strategy-step-row.drag-over').forEach(r => r.classList.remove('drag-over'));
}

function onDragOverStep(e, targetId) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  // 高亮目标行上边缘
  const row = document.getElementById(`sp-${targetId}`);
  if (row && targetId !== _dragStepId) row.classList.add('drag-over');
}

function onDragLeaveStep(targetId) {
  const row = document.getElementById(`sp-${targetId}`);
  if (row) row.classList.remove('drag-over');
}

function onDropStep(e, targetId) {
  e.preventDefault();
  const srcId = _dragStepId != null ? _dragStepId : parseInt(e.dataTransfer.getData('text/plain') || '-1', 10);
  _dragStepId = null;
  // 清除所有 drag-over 标记
  document.querySelectorAll('.strategy-step-row.drag-over').forEach(r => r.classList.remove('drag-over'));
  if (srcId === targetId) return;
  const fromIdx = _strategySteps.findIndex(s => s.id === srcId);
  const toIdx   = _strategySteps.findIndex(s => s.id === targetId);
  if (fromIdx < 0 || toIdx < 0) return;
  const item = _strategySteps.splice(fromIdx, 1)[0];
  _strategySteps.splice(toIdx, 0, item);
  renderStrategySteps();
}

function onStepTypeChange(id) {
  const type = document.getElementById(`sp-${id}-type`).value;
  ['range', 'sort_limit', 'industry_cap', 'industry_filter', 'dimension_union'].forEach(t =>
    document.getElementById(`sp-${id}-p-${t}`).style.display = (t === type) ? '' : 'none'
  );
  if (type === 'industry_filter') ensureIndustriesLoaded().then(() => populateIndustryCheckboxes(id));
}

// ── 步骤渲染 ─────────────────────────────────────────────────────
// domId: select 的 id；selectedVal: 当前选中的字段；onChange: 可选的 onchange 回调字符串
function _fieldSelect(domId, selectedVal, onChange) {
  const opts = STRATEGY_FIELDS.map(f =>
    `<option value="${f.value}"${f.value === selectedVal ? ' selected' : ''}>${f.label}</option>`
  ).join('');
  const oc = onChange ? ` onchange="${onChange}"` : '';
  return `<select id="${domId}" class="price-range-input" style="width:auto;padding:2px 4px"${oc}>${opts}</select>`;
}

function renderStrategySteps() {
  const container = document.getElementById('strategyStepsList');
  if (!container) return;

  if (!_strategySteps.length) {
    container.innerHTML = '<div style="color:#aaa;padding:10px;font-size:.85em">暂无步骤 — 选择预设或点击「添加步骤」</div>';
    return;
  }

  container.innerHTML = _strategySteps.map((s, idx) => {
    const id   = s.id;   // ← 关键：必须在这里声明
    const last = idx === _strategySteps.length - 1;

    const rangeParams = `<span id="sp-${id}-p-range" style="${s.type!=='range'?'display:none':''}">
      ${_fieldSelect(`sp-${id}-field`, s.field || 'net_assets', `onStepFieldChange(${id},'field',this.value)`)}
      <select id="sp-${id}-op" class="price-range-input" style="width:46px;padding:2px">
        <option value=">="${s.op==='>='?' selected':''}>≥</option>
        <option value="<="${s.op==='<='?' selected':''}>≤</option>
        <option value=">"${s.op==='>'?' selected':''}>&gt;</option>
        <option value="<"${s.op==='<'?' selected':''}>&lt;</option>
        <option value="="${s.op==='='?' selected':''}>＝</option>
      </select>
      <input type="number" id="sp-${id}-value" class="price-range-input"
             value="${s.value ?? ''}" style="width:72px">
    </span>`;

    const sortParams = `<span id="sp-${id}-p-sort_limit" style="${s.type!=='sort_limit'?'display:none':''}">
      按&nbsp;${_fieldSelect(`sp-${id}-sfield`, s.field || 'pb', `onStepFieldChange(${id},'field',this.value)`)}
      <select id="sp-${id}-sdir" class="price-range-input" style="width:52px;padding:2px">
        <option value="asc"${s.dir==='asc'?' selected':''}>升序</option>
        <option value="desc"${s.dir==='desc'?' selected':''}>降序</option>
      </select>
      取前&nbsp;<input type="number" id="sp-${id}-limit" class="price-range-input"
             value="${s.limit ?? 100}" style="width:60px" title="0=不限，仅排序">&nbsp;只（0=不限）
    </span>`;

    const indCapParams = `<span id="sp-${id}-p-industry_cap" style="${s.type!=='industry_cap'?'display:none':''}">
      每行业最多&nbsp;<input type="number" id="sp-${id}-max" class="price-range-input"
             value="${s.max ?? 5}" style="width:50px">&nbsp;只，
      共选出&nbsp;<input type="number" id="sp-${id}-ind-limit" class="price-range-input"
             value="${s.limit ?? 0}" style="width:55px" title="0=不限总数">&nbsp;只（0=不限）
    </span>`;

    const dims = Array.isArray(s.dimensions) ? s.dimensions : STEP_DEFAULTS.dimension_union.dimensions;
    const dimRows = dims.map((d, di) => `
      <span style="display:inline-flex;align-items:center;gap:4px;margin-right:6px;background:#f5f5f5;padding:2px 6px;border-radius:4px">
        ${_fieldSelect(`sp-${id}-dim${di}-field`, d.field || 'pe')}
        <select id="sp-${id}-dim${di}-dir" class="price-range-input" style="width:52px;padding:2px">
          <option value="asc"${d.dir==='asc'?' selected':''}>升序</option>
          <option value="desc"${d.dir==='desc'?' selected':''}>降序</option>
        </select>
        前&nbsp;<input type="number" id="sp-${id}-dim${di}-limit" class="price-range-input"
               value="${d.limit ?? 100}" style="width:55px">&nbsp;只
      </span>`).join('');
    const dimUnionParams = `<span id="sp-${id}-p-dimension_union" style="${s.type!=='dimension_union'?'display:none':''}">
      ${dimRows}
      <span style="font-size:.8em;color:#888">（各维度分别排序取前N，合并为并集）</span>
    </span>`;

    const selectedInds = Array.isArray(s.industries) ? new Set(s.industries) : new Set();
    const selCount = selectedInds.size;
    const indMode = s.mode === 'exclude' ? 'exclude' : 'include';
    const indFilterParams = `<span id="sp-${id}-p-industry_filter" style="${s.type!=='industry_filter'?'display:none':''}">
      <select id="sp-${id}-ind-mode" class="price-range-input" style="width:auto;padding:2px 6px" onchange="onStepFieldChange(${id},'mode',this.value)">
        <option value="include"${indMode==='include'?' selected':''}>仅包含</option>
        <option value="exclude"${indMode==='exclude'?' selected':''}>排除</option>
      </select>
      <details style="display:inline-block;position:relative">
        <summary style="cursor:pointer;list-style:none;padding:3px 10px;border:1px solid #d9d9d9;border-radius:6px;background:#fff;color:#333;font-size:.85em;user-select:none">
          已选 <span id="sp-${id}-ind-count" style="color:#3949ab;font-weight:600">${selCount || '0'}</span> 个行业 ▾
        </summary>
        <div style="position:absolute;z-index:100;top:100%;left:0;min-width:200px;background:#fff;border:1px solid #ddd;border-radius:6px;padding:8px;box-shadow:0 4px 12px rgba(0,0,0,.15)">
          <input type="text" placeholder="搜索行业..." id="sp-${id}-ind-search"
                 style="width:100%;margin-bottom:6px;padding:4px 6px;border:1px solid #ddd;border-radius:4px;font-size:.82em;color:#333;box-sizing:border-box"
                 oninput="filterIndCheckboxes(${id})">
          <div style="display:flex;gap:6px;margin-bottom:6px">
            <button class="quick-filter-btn" style="font-size:.78em;padding:2px 7px" onclick="setAllIndCheckboxes(${id},true)">全选</button>
            <button class="quick-filter-btn" style="font-size:.78em;padding:2px 7px" onclick="setAllIndCheckboxes(${id},false)">清空</button>
          </div>
          <div id="sp-${id}-ind-list" style="max-height:220px;overflow-y:auto;font-size:.82em"></div>
        </div>
      </details>
    </span>`;

    return `<div class="strategy-step-row" id="sp-${id}" draggable="true"
        ondragstart="onDragStartStep(event, ${id})"
        ondragend="onDragEndStep(${id})"
        ondragover="onDragOverStep(event, ${id})"
        ondragleave="onDragLeaveStep(${id})"
        ondrop="onDropStep(event, ${id})"
        style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #f0f0f0;cursor:grab">
      <span class="strategy-step-badge">${idx + 1}</span>
      <span class="drag-handle" title="按住拖动排序" style="color:#bbb;cursor:grab;user-select:none;font-size:1em">⠿</span>
      <select id="sp-${id}-type" class="price-range-input" style="width:auto;padding:2px 4px" onchange="onStepTypeChange(${id})">
        <option value="range"${s.type==='range'?' selected':''}>范围筛选</option>
        <option value="sort_limit"${s.type==='sort_limit'?' selected':''}>排序取前N</option>
        <option value="industry_cap"${s.type==='industry_cap'?' selected':''}>行业限制</option>
        <option value="industry_filter"${s.type==='industry_filter'?' selected':''}>行业筛选</option>
        <option value="dimension_union"${s.type==='dimension_union'?' selected':''}>维度合并（并集）</option>
      </select>
      ${rangeParams}${sortParams}${indCapParams}${indFilterParams}${dimUnionParams}
      <span style="margin-left:auto;display:flex;gap:3px">
        <button class="quick-filter-btn" onclick="moveStep(${id},-1)"${idx===0?' disabled':''} style="padding:1px 7px">↑</button>
        <button class="quick-filter-btn" onclick="moveStep(${id},1)"${last?' disabled':''} style="padding:1px 7px">↓</button>
        <button class="quick-filter-btn" onclick="deleteStep(${id})" style="padding:1px 8px;color:#c62828">✕</button>
      </span>
    </div>`;
  }).join('');

  if (_currentIndustries().length) {
    _strategySteps.filter(s => s.type === 'industry_filter').forEach(s => populateIndustryCheckboxes(s.id));
  }
}

// ── 行业复选框 ────────────────────────────────────────────────────

function populateIndustryCheckboxes(id) {
  const listEl = document.getElementById(`sp-${id}-ind-list`);
  if (!listEl) return;
  // 读取当前已选（来自 _strategySteps 或 DOM 里已有的选中状态）
  const step = _strategySteps.find(s => s.id === id);
  const preSelected = new Set(step ? (step.industries || []) : []);
  const industries = _currentIndustries();

  if (!industries.length) {
    const message = _industryLoadErrors[_strategyMarket] || '暂无行业数据';
    listEl.textContent = message;
    listEl.style.padding = '8px';
    listEl.style.color = '#c62828';
    return;
  }
  listEl.style.padding = '';
  listEl.style.color = '';

  listEl.innerHTML = industries.map(ind => {
    const checked = preSelected.has(ind) ? ' checked' : '';
    return `<label style="display:block;padding:2px 0;cursor:pointer;color:#333">
      <input type="checkbox" value="${ind}"${checked} onchange="onIndCheckboxChange(${id})"> ${ind}
    </label>`;
  }).join('');
}

function onIndCheckboxChange(id) {
  const selected = getSelectedIndustries(id);
  const countEl = document.getElementById(`sp-${id}-ind-count`);
  if (countEl) countEl.textContent = selected.length || '0';
  // 同步回 _strategySteps
  const step = _strategySteps.find(s => s.id === id);
  if (step) step.industries = selected;
}

function getSelectedIndustries(id) {
  const listEl = document.getElementById(`sp-${id}-ind-list`);
  if (!listEl) return [];
  return Array.from(listEl.querySelectorAll('input[type=checkbox]:checked')).map(cb => cb.value);
}

function filterIndCheckboxes(id) {
  const q = (document.getElementById(`sp-${id}-ind-search`)?.value || '').toLowerCase();
  const listEl = document.getElementById(`sp-${id}-ind-list`);
  if (!listEl) return;
  listEl.querySelectorAll('label').forEach(label => {
    label.style.display = q && !label.textContent.toLowerCase().includes(q) ? 'none' : 'block';
  });
}

function setAllIndCheckboxes(id, checked) {
  const listEl = document.getElementById(`sp-${id}-ind-list`);
  if (!listEl) return;
  listEl.querySelectorAll('input[type=checkbox]').forEach(cb => {
    if (cb.closest('label').style.display !== 'none') cb.checked = checked;
  });
  onIndCheckboxChange(id);
}

// ── 步骤读取（从 DOM）─────────────────────────────────────────────
function collectSteps() {
  return _strategySteps.map(s => {
    const id   = s.id;
    const type = document.getElementById(`sp-${id}-type`)?.value || s.type;
    if (type === 'range') return {
      type, op: document.getElementById(`sp-${id}-op`)?.value || '>=',
      field: document.getElementById(`sp-${id}-field`)?.value || 'net_assets',
      value: parseFloat(document.getElementById(`sp-${id}-value`)?.value) || 0,
    };
    if (type === 'sort_limit') return {
      type, field: document.getElementById(`sp-${id}-sfield`)?.value || 'pb',
      dir:   document.getElementById(`sp-${id}-sdir`)?.value || 'asc',
      limit: parseInt(document.getElementById(`sp-${id}-limit`)?.value)  || 0,
      skip_non_positive: true,
    };
    if (type === 'industry_cap') return {
      type, max: parseInt(document.getElementById(`sp-${id}-max`)?.value) || 5,
      limit: parseInt(document.getElementById(`sp-${id}-ind-limit`)?.value) || 0,
    };
    if (type === 'industry_filter') return {
      type,
      industries: getSelectedIndustries(id),
      mode: document.getElementById(`sp-${id}-ind-mode`)?.value || 'include',
    };
    if (type === 'dimension_union') {
      const dims = (s.dimensions || STEP_DEFAULTS.dimension_union.dimensions);
      return {
        type,
        dimensions: dims.map((d, di) => ({
          field: document.getElementById(`sp-${id}-dim${di}-field`)?.value || d.field,
          dir:   document.getElementById(`sp-${id}-dim${di}-dir`)?.value   || d.dir,
          limit: parseInt(document.getElementById(`sp-${id}-dim${di}-limit`)?.value) || d.limit,
          skip_non_positive: true,
        })),
      };
    }
    return null;
  }).filter(Boolean);
}

// ── 执行策略 ─────────────────────────────────────────────────────
async function runStrategy() {
  await _runStrategyWithSteps(collectSteps());
}

async function _runStrategyWithSteps(steps) {
  const el    = document.getElementById('strategyContent');
  if (!el) return;
  el.innerHTML = '<div class="list-loading">正在运行策略（首次约需 5-20 秒）...</div>';
  const btn = document.getElementById('strategyRunBtn');
  if (btn) btn.disabled = true;

  try {
    const res = await fetch('/api/stock_screener/strategy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ market: _strategyMarket, steps }),
    });
    const json = await res.json();
    if (!json.success) {
      if (json.need_update) {
        const msg = json.updating
          ? '数据正在初始化中，请稍候再运行策略...'
          : '暂无数据，正在后台获取，请稍后重试';
        el.innerHTML = `<div style="color:#888;padding:24px;text-align:center">${msg}</div>`;
        // 如果正在更新，轮询状态
        if (json.updating) {
          clearTimeout(_dbStatusTimer);
          _dbStatusTimer = setTimeout(loadDbStatus, 5000);
        }
      } else {
        el.innerHTML = `<div style="color:#c62828;padding:24px">⚠ ${json.message}</div>`;
      }
      return;
    }
    _strategyResult = json;
    _strategySortKey = null;
    const countEl = document.getElementById('strategyCount');
    if (countEl) countEl.textContent = `结果 ${json.data.length} 只`;
    loadDbStatus();  // 运行后刷新截止日期显示
    renderStrategyResult(json);
  } catch (e) {
    el.innerHTML = `<div style="color:#c62828;padding:24px">⚠ 执行失败：${e.message}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── 快速筛选（对结果实时过滤）────────────────────────────────────
let _lastFilteredData = [];
let _pieVisible = false;
let _pieChart   = null;

// 保存 / 恢复快速筛选栏状态（重建 DOM 时保留用户输入）
const _QF_INPUT_IDS = ['qfPeMin','qfPeMax','qfPbMin','qfPbMax','qfCapMin','qfCapMax','qfNaMin','qfNaMax'];
let _savedQf = { inputs: {}, indMode: 'include', indSelected: null };

function saveQfState() {
  _savedQf = { inputs: {}, indMode: 'include', indSelected: null };
  _QF_INPUT_IDS.forEach(id => {
    const el = document.getElementById(id);
    if (el) _savedQf.inputs[id] = el.value;
  });
  const modeEl = document.getElementById('qfIndMode');
  if (modeEl) _savedQf.indMode = modeEl.value;
  const list = document.getElementById('qfIndustryList');
  if (list) {
    _savedQf.indSelected = Array.from(list.querySelectorAll('input[type=checkbox]:checked')).map(cb => cb.value);
  }
}

function restoreQfState() {
  _QF_INPUT_IDS.forEach(id => {
    const el = document.getElementById(id);
    if (el && _savedQf.inputs[id] !== undefined) el.value = _savedQf.inputs[id];
  });
  const modeEl = document.getElementById('qfIndMode');
  if (modeEl && _savedQf.indMode) modeEl.value = _savedQf.indMode;
  const list = document.getElementById('qfIndustryList');
  if (list && Array.isArray(_savedQf.indSelected)) {
    const sel = new Set(_savedQf.indSelected);
    list.querySelectorAll('input[type=checkbox]').forEach(cb => { cb.checked = sel.has(cb.value); });
    const lbl = document.getElementById('qfIndLabel');
    if (lbl) {
      const all = list.querySelectorAll('input[type=checkbox]');
      lbl.textContent = sel.size === all.length ? '全部行业' : `已选 ${sel.size} / ${all.length} 个`;
    }
  }
}

function _getQfSelectedInds() {
  const list = document.getElementById('qfIndustryList');
  if (!list) return null;
  const boxes   = list.querySelectorAll('input[type=checkbox]');
  if (!boxes.length) return null;
  const all     = Array.from(boxes);
  const checked = all.filter(cb => cb.checked).map(cb => cb.value);
  return checked.length === all.length ? null : new Set(checked);
}

function updateQfIndLabel() {
  const list = document.getElementById('qfIndustryList');
  const lbl  = document.getElementById('qfIndLabel');
  if (!list || !lbl) return;
  const all     = list.querySelectorAll('input[type=checkbox]');
  const checked = list.querySelectorAll('input[type=checkbox]:checked');
  lbl.textContent = checked.length === all.length
    ? '全部行业'
    : `已选 ${checked.length} / ${all.length} 个`;
  applyQuickFilter();
}

function setAllQfIndustries(checked) {
  const list = document.getElementById('qfIndustryList');
  if (!list) return;
  list.querySelectorAll('input[type=checkbox]').forEach(cb => { cb.checked = checked; });
  updateQfIndLabel();
}

function applyQuickFilter() {
  if (!_strategyResult) return;
  const data    = _strategyResult.data;
  // 解析筛选值：空串/非数字 → null（不参与过滤）；0 和负数都保留
  const _num = (id) => {
    const v = document.getElementById(id)?.value;
    if (v === undefined || v === null || String(v).trim() === '') return null;
    const n = parseFloat(v);
    return isNaN(n) ? null : n;
  };
  const peMin   = _num('qfPeMin');
  const peMax   = _num('qfPeMax');
  const pbMin   = _num('qfPbMin');
  const pbMax   = _num('qfPbMax');
  const capMin  = _num('qfCapMin');
  const capMax  = _num('qfCapMax');
  const naMin   = _num('qfNaMin');
  const naMax   = _num('qfNaMax');
  const selInds = _getQfSelectedInds();
  const indMode = document.getElementById('qfIndMode')?.value || 'include';

  let filtered = data.filter(s => {
    if (peMin  !== null && (s.pe  == null || s.pe  < peMin))  return false;
    if (peMax  !== null && (s.pe  == null || s.pe  > peMax))  return false;
    if (pbMin  !== null && (s.pb  == null || s.pb  < pbMin))  return false;
    if (pbMax  !== null && (s.pb  == null || s.pb  > pbMax))  return false;
    if (capMin !== null && (s.market_cap == null || s.market_cap < capMin)) return false;
    if (capMax !== null && (s.market_cap == null || s.market_cap > capMax)) return false;
    if (naMin  !== null && (s.net_assets == null || s.net_assets < naMin))  return false;
    if (naMax  !== null && (s.net_assets == null || s.net_assets > naMax))  return false;
    if (selInds !== null) {
      const inSet = selInds.has(s.industry || '');
      if (indMode === 'include' && !inSet) return false;
      if (indMode === 'exclude' &&  inSet) return false;
    }
    return true;
  });

  filtered = _sortedData(filtered);
  _lastFilteredData = filtered;

  const countEl = document.getElementById('strategyCount');
  if (countEl) countEl.textContent = `结果 ${filtered.length} / ${data.length} 只`;

  const tableEl = document.getElementById('strategyTableArea');
  if (tableEl) {
    const hasIndustry = filtered.some(s => s.industry);
    tableEl.innerHTML = _strategyGrouped ? _renderGrouped(filtered) : _buildTable(filtered, hasIndustry, true);
  }

  if (_pieVisible) renderPieChart(filtered);
}

function resetQuickFilters() {
  ['qfPeMin','qfPeMax','qfPbMin','qfPbMax','qfCapMin','qfCapMax','qfNaMin','qfNaMax']
    .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  setAllQfIndustries(true);
}

function _buildQuickFilterBar(industries) {
  const indOptions = (industries || []).map(ind =>
    `<label style="display:block;padding:3px 0;cursor:pointer;color:#333;white-space:nowrap">
       <input type="checkbox" value="${ind}" checked onchange="updateQfIndLabel()"> ${ind}
     </label>`
  ).join('');
  const indDropdown = (industries || []).length
    ? `<select id="qfIndMode" class="price-range-input" style="width:auto;padding:2px 6px" onchange="applyQuickFilter()">
         <option value="include">仅包含</option>
         <option value="exclude">排 除</option>
       </select>
       <details style="display:inline-block;position:relative;vertical-align:middle">
        <summary style="cursor:pointer;list-style:none;padding:3px 10px;border:1px solid #d9d9d9;border-radius:6px;background:#fff;color:#333;font-size:.85em;user-select:none;white-space:nowrap">
          <span id="qfIndLabel">全部行业</span> ▾
        </summary>
        <div style="position:absolute;z-index:200;top:calc(100% + 4px);left:0;min-width:160px;background:#fff;border:1px solid #ddd;border-radius:6px;padding:8px;box-shadow:0 4px 14px rgba(0,0,0,.18)">
          <div style="display:flex;gap:6px;margin-bottom:6px">
            <button class="quick-filter-btn" style="font-size:.78em;padding:2px 8px" onclick="setAllQfIndustries(true)">全选</button>
            <button class="quick-filter-btn" style="font-size:.78em;padding:2px 8px" onclick="setAllQfIndustries(false)">清空</button>
          </div>
          <div id="qfIndustryList" style="max-height:220px;overflow-y:auto;font-size:.82em">${indOptions}</div>
        </div>
      </details>`
    : `<span style="color:#aaa;font-size:.82em">（无行业数据）</span>`;

  return `<div style="background:#f8f9fa;border:1px solid #e0e0e0;border-radius:8px;padding:8px 14px;margin-bottom:10px">
    <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:.82em">
      <span style="color:#666;font-weight:600">快速筛选：</span>
      <span style="color:#888">PE</span>
      <input type="number" id="qfPeMin" class="price-range-input" placeholder="最小" style="width:62px" oninput="applyQuickFilter()">
      <span style="color:#ccc">~</span>
      <input type="number" id="qfPeMax" class="price-range-input" placeholder="最大" style="width:62px" oninput="applyQuickFilter()">
      <span style="color:#888;margin-left:6px">PB</span>
      <input type="number" id="qfPbMin" class="price-range-input" placeholder="最小" style="width:62px" oninput="applyQuickFilter()">
      <span style="color:#ccc">~</span>
      <input type="number" id="qfPbMax" class="price-range-input" placeholder="最大" style="width:62px" oninput="applyQuickFilter()">
      <span style="color:#888;margin-left:6px">市值(亿)</span>
      <input type="number" id="qfCapMin" class="price-range-input" placeholder="最小" style="width:66px" oninput="applyQuickFilter()">
      <span style="color:#ccc">~</span>
      <input type="number" id="qfCapMax" class="price-range-input" placeholder="最大" style="width:66px" oninput="applyQuickFilter()">
      <span style="color:#888;margin-left:6px">净资产(亿)</span>
      <input type="number" id="qfNaMin" class="price-range-input" placeholder="最小" style="width:66px" oninput="applyQuickFilter()">
      <span style="color:#ccc">~</span>
      <input type="number" id="qfNaMax" class="price-range-input" placeholder="最大" style="width:66px" oninput="applyQuickFilter()">
      <span style="color:#888;margin-left:6px">行业</span>
      ${indDropdown}
      <button class="quick-filter-btn reset-btn" onclick="resetQuickFilters()" style="margin-left:4px">重置</button>
    </div>
  </div>`;
}

// ── 饼图 ─────────────────────────────────────────────────────────

function togglePieChart() {
  _pieVisible = !_pieVisible;
  const wrap = document.getElementById('strategyPieWrap');
  const btn  = document.getElementById('pieToggleBtn');
  if (wrap) wrap.style.display = _pieVisible ? '' : 'none';
  if (btn)  btn.classList.toggle('active', _pieVisible);
  if (_pieVisible) {
    const d = _lastFilteredData.length ? _lastFilteredData : (_strategyResult?.data || []);
    renderPieChart(d);
    // 容器从隐藏变可见后，强制 ECharts 重新计算尺寸
    setTimeout(() => { if (_pieChart) _pieChart.resize(); }, 60);
  }
}

function renderPieChart(data) {
  if (typeof echarts === 'undefined') return;
  const wrap = document.getElementById('strategyPieChart');
  if (!wrap) return;
  const indMap = {};
  data.forEach(s => { const ind = s.industry || '其他'; indMap[ind] = (indMap[ind] || 0) + 1; });
  const total    = data.length;
  const pieData  = Object.entries(indMap).sort((a, b) => b[1] - a[1]).map(([name, value]) => ({ name, value }));

  // 确保实例绑定到当前 DOM 节点（重建后 wrap 是新的）
  if (!_pieChart) _pieChart = echarts.init(wrap);
  _pieChart.setOption({
    backgroundColor: '#fff',
    tooltip: {
      trigger: 'item',
      formatter: p => `${p.name}<br/>股票数：${p.value} 只<br/>占比：${(p.value/total*100).toFixed(1)}%`,
    },
    legend: {
      orient: 'vertical', right: 10, top: 'middle', type: 'scroll',
      textStyle: { fontSize: 12 },
      // 图例项显示 名称(数量)
      formatter: name => {
        const it = pieData.find(p => p.name === name);
        return it ? `${name} (${it.value})` : name;
      },
    },
    series: [{
      type: 'pie',
      // 实心饼，充分利用空间；预留右侧图例区
      radius: ['0%', '72%'],
      center: ['38%', '50%'],
      data: pieData,
      minAngle: 3,   // 最小扇区角度，避免过小的行业挤在一起
      label: {
        show: true,
        // 所有行业都显示标签（即使只有1只）
        formatter: p => p.value >= 1 ? `${p.name} ${p.value}只` : '',
        fontSize: 11,
        lineHeight: 14,
      },
      labelLine: { show: true, length: 12, length2: 6 },
      emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,.25)' } },
    }],
  });
}

// ── 结果渲染 ─────────────────────────────────────────────────────
let _strategyGrouped = false;

function toggleStrategyGrouped() {
  _strategyGrouped = !_strategyGrouped;

  // 只更新按钮样式，不重建筛选栏
  const btn = document.getElementById('strategyGroupBtn');
  if (btn) {
    btn.textContent = _strategyGrouped ? '📋 列表视图' : '🏷 行业分组';
    btn.classList.toggle('active', _strategyGrouped);
  }

  // 用当前已筛选数据重渲染表格区域（保留所有筛选条件）
  const tableEl = document.getElementById('strategyTableArea');
  if (tableEl) {
    const data = _lastFilteredData.length ? _lastFilteredData : (_strategyResult?.data || []);
    const hasIndustry = data.some(s => s.industry);
    tableEl.innerHTML = _strategyGrouped ? _renderGrouped(data) : _buildTable(data, hasIndustry, true);
  }
}

function renderStrategyResult(json) {
  const el = document.getElementById('strategyContent');
  const { data, stats } = json;

  // 重建 DOM 前保存快速筛选栏状态（输入值、行业选择、模式）
  saveQfState();
  // 重建 DOM 前销毁旧 ECharts 实例（避免绑定到已删除节点）
  if (_pieChart) {
    try { _pieChart.dispose(); } catch (e) {}
    _pieChart = null;
  }
  // 重置筛选状态
  _lastFilteredData = data;

  // 步骤流程条
  const flowHtml = (stats || []).map((s, i) => {
    const isLast = i === stats.length - 1;
    return `<div class="fine-stat-card${isLast ? ' fine-stat-highlight' : ''}">
      <div class="fine-stat-label">${s.label}</div>
      <div class="fine-stat-val">${s.count.toLocaleString()} 只</div>
    </div>${!isLast ? '<div class="fine-stat-arrow">→</div>' : ''}`;
  }).join('');

  const statsHtml = `<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px">${flowHtml}</div>`;

  if (!data.length) {
    el.innerHTML = statsHtml + '<div style="color:#aaa;padding:24px;text-align:center">无结果，请调整策略步骤后重新运行</div>';
    return;
  }

  // 提取结果中实际存在的行业（排序后传给筛选栏）
  const uniqueInds = [...new Set(data.map(s => s.industry || '').filter(Boolean))].sort();

  // 控制栏：行业分组 + 饼图切换
  const controlBar = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
    <button class="screener-sub-btn${_strategyGrouped ? ' active' : ''}" id="strategyGroupBtn"
            onclick="toggleStrategyGrouped()">
      ${_strategyGrouped ? '📋 列表视图' : '🏷 行业分组'}
    </button>
    <button class="screener-sub-btn${_pieVisible ? ' active' : ''}" id="pieToggleBtn"
            onclick="togglePieChart()">
      📊 行业饼图
    </button>
    <span style="font-size:.82em;color:#888">${_strategyGrouped ? '按行业折叠展示' : '平铺列表'}</span>
  </div>
  <div id="strategyPieWrap" style="display:${_pieVisible ? '' : 'none'};margin-bottom:10px">
    <div id="strategyPieChart" style="width:100%;height:420px"></div>
  </div>`;

  const qfBar = _buildQuickFilterBar(uniqueInds);

  if (_strategyGrouped) {
    el.innerHTML = statsHtml + controlBar + qfBar + `<div id="strategyTableArea">${_renderGrouped(data)}</div>`;
  } else {
    el.innerHTML = statsHtml + controlBar + qfBar + `<div id="strategyTableArea">${_renderFlat(data)}</div>`;
  }
  // 若饼图已开，重新渲染
  if (_pieVisible) renderPieChart(_lastFilteredData.length ? _lastFilteredData : data);

  // 恢复用户之前输入的快速筛选条件，并重新应用
  restoreQfState();
  applyQuickFilter();
}

// ── 列表视图 ──────────────────────────────────────────────────────
function _renderFlat(data) {
  const hasIndustry = data.some(s => s.industry);
  let sorted = _sortedData(data);
  return _buildTable(sorted, hasIndustry, true);
}

// ── 行业分组视图 ──────────────────────────────────────────────────
function _renderGrouped(data) {
  // 按行业分组，组内按 PE 升序
  const groups = {};
  data.forEach(s => {
    const ind = s.industry || '其他';
    if (!groups[ind]) groups[ind] = [];
    groups[ind].push(s);
  });

  // 按组内平均PE升序排列各行业
  const sortedGroups = Object.entries(groups).sort((a, b) => {
    const avgPe = arr => {
      const valid = arr.filter(s => s.pe != null && s.pe > 0);
      return valid.length ? valid.reduce((s, x) => s + x.pe, 0) / valid.length : 9999;
    };
    return avgPe(a[1]) - avgPe(b[1]);
  });

  return sortedGroups.map(([industry, stocks]) => {
    const stocksSorted = [...stocks].sort((a, b) => (a.pe ?? 9999) - (b.pe ?? 9999));
    const tableHtml = _buildTable(stocksSorted, false, false);
    return `<details open style="margin-bottom:12px">
      <summary style="padding:8px 12px;background:#e8eaf6;border-radius:6px;cursor:pointer;font-weight:600;color:#283593;list-style:none;display:flex;justify-content:space-between;align-items:center">
        <span>${industry}</span>
        <span style="font-size:.85em;font-weight:normal;color:#3949ab">${stocks.length} 只</span>
      </summary>
      ${tableHtml}
    </details>`;
  }).join('');
}

function _sortedData(data) {
  if (!_strategySortKey) return data;
  const TEXT_KEYS = new Set(['code','name','industry','market']);
  return [...data].sort((a, b) => {
    if (TEXT_KEYS.has(_strategySortKey)) {
      const va = a[_strategySortKey]||'', vb = b[_strategySortKey]||'';
      return _strategySortDir==='asc' ? va.localeCompare(vb) : vb.localeCompare(va);
    }
    let va = parseFloat(a[_strategySortKey]), vb = parseFloat(b[_strategySortKey]);
    if (isNaN(va)) va = _strategySortDir==='asc' ? Infinity : -Infinity;
    if (isNaN(vb)) vb = _strategySortDir==='asc' ? Infinity : -Infinity;
    return _strategySortDir==='asc' ? va-vb : vb-va;
  });
}

function _buildTable(rows, hasIndustry, sortable) {
  function _icon(key) {
    if (!sortable || _strategySortKey!==key) return sortable ? '<span class="sort-icon">⇅</span>' : '';
    return _strategySortDir==='asc' ? '<span class="sort-icon active">↑</span>' : '<span class="sort-icon active">↓</span>';
  }
  function _th(label, key, cls='num') {
    if (!sortable) return `<th class="${cls}">${label}</th>`;
    return `<th class="${cls} sortable" onclick="sortStrategy('${key}')">${label}${_icon(key)}</th>`;
  }
  const hasPrice = rows.some(s => s.price != null);
  const thead = `<tr>
    <th class="num">序号</th>
    ${_th('代码','code','')} ${_th('名称','name','')}
    ${hasPrice ? _th('最新价','price') : ''}
    ${_th('PE','pe')} ${_th('PB','pb')}
    ${_th('总市值(亿)','market_cap')} ${_th('净资产(亿)','net_assets')}
    ${hasIndustry ? _th('行业','industry','') : ''}
  </tr>`;
  const tbody = rows.map((s, idx) => `<tr>
    <td class="num">${idx+1}</td>
    <td>${s.code||'-'}</td><td>${s.name||'-'}</td>
    ${hasPrice ? `<td class="num">${s.price!=null?parseFloat(s.price).toFixed(2):'-'}</td>` : ''}
    <td class="num">${s.pe!=null?parseFloat(s.pe).toFixed(2):'-'}</td>
    <td class="num">${s.pb!=null?parseFloat(s.pb).toFixed(2):'-'}</td>
    <td class="num">${s.market_cap!=null?parseFloat(s.market_cap).toFixed(1):'-'}</td>
    <td class="num">${s.net_assets!=null?parseFloat(s.net_assets).toFixed(1):'-'}</td>
    ${hasIndustry ? `<td style="font-size:.85em;color:#888">${s.industry||'-'}</td>` : ''}
  </tr>`).join('');
  return `<div class="bond-list-table-wrap">
    <table class="bond-list-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table>
  </div>`;
}

function sortStrategy(key) {
  if (_strategySortKey===key) _strategySortDir = _strategySortDir==='asc'?'desc':'asc';
  else { _strategySortKey=key; _strategySortDir='asc'; }
  applyQuickFilter();   // 排序不重置筛选器，只更新表格区域
}

// ── 初始化时渲染空步骤列表，并自动加载全部股票 ──────────────────────
document.addEventListener('DOMContentLoaded', () => {
  renderStrategySteps();
});

// 供 main.js 的 switchAsset('screener') 调用
function initStrategyView() {
  renderStrategySteps();
  // 自动运行空策略，展示所有股票（让用户直接看到数据）
  _autoRunEmpty();
}

async function _autoRunEmpty() {
  const el = document.getElementById('strategyContent');
  if (!el) return;
  // 若数据正在初始化，等待后重试
  const res  = await fetch('/api/stock_screener/db_status').catch(() => null);
  if (res) {
    const meta = (await res.json().catch(() => ({})))[_strategyMarket] || {};
    if (meta.updating) {
      // 正在更新，稍后重试（loadDbStatus 的轮询会触发 _autoRunEmpty）
      el.innerHTML = '<div style="color:#888;padding:24px;text-align:center">数据正在初始化，请稍候...</div>';
      return;
    }
  }
  await _runStrategyWithSteps([]);
}
