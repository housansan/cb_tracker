// ── 全局状态 ─────────────────────────────────────────────
let allData             = [];
let currentPage         = 1;
const pageSize          = 20;
let mainChart           = null;
let remainChart         = null;
let lastLoadedBondCode  = ""; // 记录上次加载基础信息的债券代码

// ── 工具函数 ─────────────────────────────────────────────
function fmtDate(d) {
  return d.toISOString().slice(0, 10);
}

function toApiDate(dateStr) {
  return dateStr.replace(/-/g, "");
}

function showError(msg) {
  const el = document.getElementById("errorMsg");
  el.textContent    = msg;
  el.style.display  = "block";
}

function hideError() {
  document.getElementById("errorMsg").style.display = "none";
}

function setRange(days) {
  const today = new Date();
  const past  = new Date(today);
  past.setDate(today.getDate() - days);
  document.getElementById("end_date").value   = fmtDate(today);
  document.getElementById("start_date").value = fmtDate(past);
}

// 清空日期框，让后端自动用上市/退市日期
function setFullHistory() {
  document.getElementById("start_date").value = "";
  document.getElementById("end_date").value   = "";
}

// ── 查询入口 ─────────────────────────────────────────────
async function queryData() {
  const bondCode  = document.getElementById("bond_code").value.trim();
  const startDate = document.getElementById("start_date").value;
  const endDate   = document.getElementById("end_date").value;

  if (!bondCode) { showError("请输入可转债代码"); return; }
  hideError();

  // 仅在债券代码变化时才重新获取基础信息
  if (bondCode !== lastLoadedBondCode) {
    loadBondInfo(bondCode);
  }

  // 日期为空时表示获取全部历史，不传日期参数
  let url = `/api/history?bond_code=${encodeURIComponent(bondCode)}`;
  if (startDate && endDate) {
    url += `&start_date=${toApiDate(startDate)}&end_date=${toApiDate(endDate)}`;
  }

  document.getElementById("loading").style.display    = "block";
  document.getElementById("statsBar").style.display   = "none";
  document.getElementById("chartCard").style.display  = "none";
  document.getElementById("tableCard").style.display  = "none";

  try {
    const res  = await fetch(url);
    const json = await res.json();

    if (!json.success) {
      showError(json.message || "查询失败");
      return;
    }

    allData      = json.data.reverse(); // 时间正序
    currentPage  = 1;
    renderStats(bondCode, allData);
    renderDualChart(allData);
    renderTable();

    document.getElementById("statsBar").style.display        = "flex";
    document.getElementById("chartCard").style.display       = "block";
    document.getElementById("remainChartCard").style.display = "block";
    document.getElementById("tableCard").style.display       = "block";
    renderRemainChart(allData);
    // 容器从 display:none 变为 block 后，强制 ECharts 重新计算宽度
    setTimeout(() => {
      mainChart   && mainChart.resize();
      remainChart && remainChart.resize();
    }, 50);
  } catch (e) {
    showError("请求失败：" + e.message);
  } finally {
    document.getElementById("loading").style.display = "none";
  }
}

// ── 视图切换 ─────────────────────────────────────────────
// 资产大类切换：可转债 / LOF
let _currentAsset = 'cb';
let _lofLoaded = false;

function switchAsset(asset) {
  _currentAsset = asset;
  const cbList = document.getElementById('listView');
  const cbDetail = document.getElementById('detailView');
  const lofView = document.getElementById('lofView');
  const btnCb = document.getElementById('assetNavCb');
  const btnLof = document.getElementById('assetNavLof');

  if (asset === 'lof') {
    cbList.style.display = 'none';
    cbDetail.style.display = 'none';
    lofView.style.display = 'block';
    btnCb.classList.remove('active');
    btnLof.classList.add('active');
    if (!_lofLoaded) { loadLofList(); _lofLoaded = true; }
  } else {
    lofView.style.display = 'none';
    cbDetail.style.display = 'none';
    cbList.style.display = 'block';
    btnLof.classList.remove('active');
    btnCb.classList.add('active');
  }
}

function showListView() {
  document.getElementById('listView').style.display   = 'block';
  document.getElementById('detailView').style.display = 'none';
  history.pushState(null, '', '#list');
}

function openDetail(bondCode) {
  document.getElementById('listView').style.display   = 'none';
  document.getElementById('detailView').style.display = 'block';
  document.getElementById('bond_code').value          = bondCode;
  lastLoadedBondCode = '';
  loadBondInfo(bondCode);
  // 重置图表区域
  allData = [];
  document.getElementById('statsBar').style.display        = 'none';
  document.getElementById('chartCard').style.display       = 'none';
  document.getElementById('remainChartCard').style.display = 'none';
  document.getElementById('tableCard').style.display       = 'none';
  history.pushState(null, '', `#${bondCode}`);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ── 事件绑定 ─────────────────────────────────────────────
// 浏览器后退/前进
window.addEventListener('popstate', () => {
  const hash = location.hash.replace('#', '');
  if (!hash || hash === 'list') {
    document.getElementById('listView').style.display   = 'block';
    document.getElementById('detailView').style.display = 'none';
  } else {
    openDetail(hash);
  }
});

// 初始化日期
function initDates() {
  const today      = new Date();
  const oneYearAgo = new Date(today);
  oneYearAgo.setFullYear(today.getFullYear() - 1);
  const endDateEl   = document.getElementById("end_date");
  const startDateEl = document.getElementById("start_date");
  if (endDateEl)   endDateEl.value   = fmtDate(today);
  if (startDateEl) startDateEl.value = fmtDate(oneYearAgo);
}

// 页面加载时根据 hash 决定显示列表还是详情
function initPage() {
  console.log('[main.js] initPage 开始执行');
  try {
    // 初始化日期
    console.log('[main.js] 开始执行 initDates');
    initDates();
    console.log('[main.js] initDates 执行完成');
    
    // 绑定债券代码输入框失焦事件
    const bondCodeEl = document.getElementById("bond_code");
    console.log('[main.js] 获取债券代码输入框元素:', bondCodeEl);
    if (bondCodeEl) {
      bondCodeEl.addEventListener("blur", function () {
        console.log('[main.js] 债券代码输入框 blur 事件触发, 值:', this.value.trim());
        const code = this.value.trim();
        if (code && code !== lastLoadedBondCode) {
          loadBondInfo(code);
          _adjLoaded    = false;
          _couponLoaded = false;
          if (_activeTab === 'coupon') loadCouponInfo(code);
          else if (_activeTab === 'adj') loadAdjLogs(code);
        }
      });
    }
    
    // 回车触发查询
    document.addEventListener("keydown", e => { 
      console.log('[main.js] keydown 事件触发, 按键:', e.key);
      if (e.key === "Enter") queryData(); 
    });
    
    // 窗口缩放时重绘图表
    window.addEventListener("resize", () => {
      console.log('[main.js] 窗口 resize 事件触发');
      mainChart   && mainChart.resize();
      remainChart && remainChart.resize();
    });
    
    // 根据hash决定显示列表还是详情
    const hash = location.hash.replace('#', '');
    console.log('[main.js] 当前 hash:', hash);
    if (hash && hash !== 'list') {
      console.log('[main.js] 打开详情页:', hash);
      openDetail(hash);
    } else {
      console.log('[main.js] 显示列表页，开始加载债券列表');
      showListView();
      loadBondList();
    }
    console.log('[main.js] initPage 执行完成');
  } catch (e) {
    console.error('[main.js] initPage 执行出错:', e);
  }
}

// 确保DOM加载完成后执行初始化
console.log('[main.js] 检查 DOM 状态:', document.readyState);
if (document.readyState === 'loading') {
  console.log('[main.js] DOM 未就绪，监听 DOMContentLoaded 事件');
  window.addEventListener("DOMContentLoaded", () => {
    console.log('[main.js] DOMContentLoaded 事件触发，执行 initPage');
    initPage();
  });
} else {
  // DOM 已经加载完成，立即执行
  console.log('[main.js] DOM 已就绪，立即执行 initPage');
  initPage();
}
