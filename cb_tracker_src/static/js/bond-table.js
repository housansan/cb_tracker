// ── 历史数据表格 + 分页 + 导出 CSV ──────────────────────────
function renderTable() {
  const total      = allData.length;
  const totalPages = Math.ceil(total / pageSize);
  const start      = (currentPage - 1) * pageSize;
  const pageData   = allData.slice(start, start + pageSize);

  const tbody = document.getElementById("tableBody");
  tbody.innerHTML = pageData.map(row => {
    const date       = (row["日期"] || "").replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3");
    const close      = row["收盘价"]     != null ? parseFloat(row["收盘价"]).toFixed(3)     : "-";
    const stockClose = row["正股收盘价"] != null ? parseFloat(row["正股收盘价"]).toFixed(3) : "-";
    const convVal    = row["转股价值"]   != null ? parseFloat(row["转股价值"]).toFixed(3)   : "-";
    const premium    = row["转股溢价率"] != null ? parseFloat(row["转股溢价率"]).toFixed(2) : "-";
    const open       = row["开盘价"]     != null ? parseFloat(row["开盘价"]).toFixed(3)     : "-";
    const high       = row["最高价"]     != null ? parseFloat(row["最高价"]).toFixed(3)     : "-";
    const low        = row["最低价"]     != null ? parseFloat(row["最低价"]).toFixed(3)     : "-";
    const vol        = row["成交量"]     != null ? parseInt(row["成交量"]).toLocaleString() : "-";
    const remainAmt  = row["剩余规模"]   != null ? parseFloat(row["剩余规模"]).toFixed(2)   : "-";
    const ytmVal     = row["到期收益率"] != null ? parseFloat(row["到期收益率"]).toFixed(2) : "-";
    const ytmNum     = parseFloat(ytmVal);
    const ytmCls     = isNaN(ytmNum) ? "" : ytmNum >= 0 ? "up" : "down";
    const ytmTxt     = isNaN(ytmNum) ? "-" : (ytmNum >= 0 ? "+" : "") + ytmVal + "%";
    const premiumNum = parseFloat(premium);
    const premiumCls = isNaN(premiumNum) ? "" : premiumNum >= 0 ? "up" : "down";
    const premiumTxt = isNaN(premiumNum) ? "-" : (premiumNum >= 0 ? "+" : "") + premium + "%";
    return `<tr>
      <td>${date}</td>
      <td class="up">${close}</td>
      <td>${stockClose}</td>
      <td>${convVal}</td>
      <td class="${premiumCls}">${premiumTxt}</td>
      <td>${open}</td>
      <td>${high}</td>
      <td>${low}</td>
      <td>${vol}</td>
      <td>${remainAmt}</td>
      <td class="${ytmCls}">${ytmTxt}</td>
    </tr>`;
  }).join("");

  document.getElementById("pageInfo").textContent = `共 ${total} 条，第 ${currentPage}/${totalPages} 页`;
  renderPagination(totalPages);
}

function renderPagination(totalPages) {
  const pg = document.getElementById("pagination");
  if (totalPages <= 1) { pg.innerHTML = ""; return; }

  let html = `<button class="page-btn" onclick="goPage(${currentPage - 1})" ${currentPage === 1 ? "disabled" : ""}>‹ 上一页</button>`;

  const range = [];
  for (let i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || Math.abs(i - currentPage) <= 2) range.push(i);
    else if (range[range.length - 1] !== "...") range.push("...");
  }
  range.forEach(p => {
    if (p === "...") html += `<span style="padding:0 4px;color:#aaa">…</span>`;
    else html += `<button class="page-btn ${p === currentPage ? "active" : ""}" onclick="goPage(${p})">${p}</button>`;
  });

  html += `<button class="page-btn" onclick="goPage(${currentPage + 1})" ${currentPage === totalPages ? "disabled" : ""}>下一页 ›</button>`;
  pg.innerHTML = html;
}

function goPage(p) {
  const totalPages = Math.ceil(allData.length / pageSize);
  if (p < 1 || p > totalPages) return;
  currentPage = p;
  renderTable();
  document.getElementById("tableCard").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ── 统计栏 ──────────────────────────────────────────────
function renderStats(code, data) {
  if (!data.length) return;
  const closes = data.map(d => parseFloat(d["收盘价"] || d.close || 0));
  const highs  = data.map(d => parseFloat(d["最高价"] || d.high  || 0));
  const lows   = data.map(d => parseFloat(d["最低价"] || d.low   || 0));
  const first  = closes[0];
  const last   = closes[closes.length - 1];
  const chg    = ((last - first) / first * 100).toFixed(2);

  document.getElementById("statCode").textContent  = code;
  document.getElementById("statTotal").textContent = data.length + " 条";
  document.getElementById("statClose").textContent = last.toFixed(3);
  document.getElementById("statHigh").textContent  = Math.max(...highs).toFixed(3);
  document.getElementById("statLow").textContent   = Math.min(...lows).toFixed(3);
  const chgEl = document.getElementById("statChange");
  chgEl.textContent = (chg >= 0 ? "+" : "") + chg + "%";
  chgEl.className   = "stat-value " + (chg >= 0 ? "up" : "down");
}

// ── 导出 CSV ─────────────────────────────────────────────
function exportCSV() {
  if (!allData.length) { showError("请先查询数据"); return; }
  const headers = Object.keys(allData[0]);
  const rows    = [headers.join(","), ...allData.map(r => headers.map(h => r[h] ?? "").join(","))];
  const blob    = new Blob(["\uFEFF" + rows.join("\n")], { type: "text/csv;charset=utf-8;" });
  const a       = document.createElement("a");
  a.href        = URL.createObjectURL(blob);
  a.download    = `bond_${document.getElementById("bond_code").value}_history.csv`;
  a.click();
}
