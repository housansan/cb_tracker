// ── 双折线图（收盘价 + 转股溢价率 + 到期收益率）────────────────────────
function renderDualChart(data) {
  if (!data.length) return;
  if (!mainChart) mainChart = echarts.init(document.getElementById("mainChart"));

  data = [...data].sort((a, b) => (a["日期"] || "").localeCompare(b["日期"] || ""));

  const dates    = data.map(d => (d["日期"] || "").replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3"));
  const closes   = data.map(d => d["收盘价"]    != null ? parseFloat(d["收盘价"]).toFixed(3)    : null);
  const premiums = data.map(d => d["转股溢价率"] != null ? parseFloat(d["转股溢价率"]).toFixed(2) : null);
  const ytms     = data.map(d => d["到期收益率"] != null ? parseFloat(d["到期收益率"]).toFixed(4) : null);
  const hasPremium = premiums.some(v => v !== null);
  const hasYtm     = ytms.some(v => v !== null);

  const legendData = ["收盘价"];
  if (hasPremium) legendData.push("转股溢价率");
  if (hasYtm)     legendData.push("到期收益率");

  const yAxisArr = [
    {
      name: "收盘价（元）",
      nameTextStyle: { color: "#3949ab", fontSize: 11 },
      type: "value", scale: true,
      axisLabel: { fontSize: 11, color: "#3949ab" },
      axisLine: { show: true, lineStyle: { color: "#3949ab" } },
      splitLine: { lineStyle: { color: "#f0f0f0" } }
    }
  ];
  if (hasPremium) {
    yAxisArr.push({
      name: "溢价率（%）",
      nameTextStyle: { color: "#e65100", fontSize: 11 },
      type: "value", scale: true,
      axisLabel: { fontSize: 11, color: "#e65100", formatter: v => v + "%" },
      axisLine: { show: true, lineStyle: { color: "#e65100" } },
      splitLine: { show: false }
    });
  }
  if (hasYtm) {
    yAxisArr.push({
      name: "到期收益率（%）",
      nameTextStyle: { color: "#2e7d32", fontSize: 11 },
      type: "value", scale: true,
      position: "right",
      offset: hasPremium ? 60 : 0,
      axisLabel: { fontSize: 11, color: "#2e7d32", formatter: v => v + "%" },
      axisLine: { show: true, lineStyle: { color: "#2e7d32" } },
      splitLine: { show: false }
    });
  }

  const ytmAxisIndex = hasPremium ? 2 : 1;
  const rightCount   = (hasPremium ? 1 : 0) + (hasYtm ? 1 : 0);

  const option = {
    backgroundColor: "#fff",
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      formatter: params => {
        let s = `<b>${params[0].axisValue}</b><br/>`;
        params.forEach(p => {
          if (p.value == null) return;
          const unit = p.seriesName === "收盘价" ? " 元" : "%";
          s += `${p.marker}${p.seriesName}：<b>${p.value}${unit}</b><br/>`;
        });
        return s;
      }
    },
    legend: { data: legendData, bottom: 4, left: "center" },
    grid: { left: "6%", right: rightCount > 0 ? (rightCount === 2 ? "12%" : "6%") : "2%", top: 48, bottom: 100, containLabel: true },
    xAxis: {
      type: "category", data: dates, boundaryGap: false,
      axisLabel: { fontSize: 11, rotate: 30 },
      axisLine: { lineStyle: { color: "#ddd" } },
      splitLine: { show: false }
    },
    yAxis: yAxisArr,
    dataZoom: [
      { type: "inside", start: 60, end: 100 },
      { type: "slider",  start: 60, end: 100, height: 24, bottom: 36 }
    ],
    series: [
      {
        name: "收盘价",
        type: "line", yAxisIndex: 0, data: closes,
        smooth: true, symbol: "none",
        lineStyle: { color: "#3949ab", width: 2 },
        areaStyle: {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(57,73,171,.20)" },
              { offset: 1, color: "rgba(57,73,171,.02)" }
            ]
          }
        }
      },
      hasPremium ? {
        name: "转股溢价率",
        type: "line", yAxisIndex: 1, data: premiums,
        smooth: true, symbol: "none",
        lineStyle: { color: "#e65100", width: 2, type: "dashed" },
        itemStyle: { color: "#e65100" }
      } : null,
      hasYtm ? {
        name: "到期收益率",
        type: "line", yAxisIndex: ytmAxisIndex, data: ytms,
        smooth: true, symbol: "none",
        lineStyle: { color: "#2e7d32", width: 2, type: "dotted" },
        itemStyle: { color: "#2e7d32" }
      } : null
    ].filter(Boolean)
  };

  mainChart.setOption(option, true);
}

// ── 剩余规模折线图 ────────────────────────────────────────────────────
function renderRemainChart(data) {
  if (!data.length) return;
  if (!remainChart) remainChart = echarts.init(document.getElementById('remainChart'));

  const sorted  = [...data].sort((a, b) => (a['日期'] || '').localeCompare(b['日期'] || ''));
  // 只保留规模发生变化的日期点（首尾必保留），避免大量重复数据点
  const deduped = sorted.filter((d, i) => {
    if (i === 0 || i === sorted.length - 1) return true;
    const cur  = d['剩余规模']          != null ? parseFloat(d['剩余规模']).toFixed(2)          : null;
    const prev = sorted[i - 1]['剩余规模'] != null ? parseFloat(sorted[i - 1]['剩余规模']).toFixed(2) : null;
    return cur !== prev;
  });
  const dates = deduped.map(d => (d['日期'] || '').replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3'));
  const amts  = deduped.map(d => d['剩余规模'] != null ? parseFloat(d['剩余规模']).toFixed(2) : null);

  const option = {
    backgroundColor: '#fff',
    title: { text: '剩余规模（亿元）', left: 16, top: 12, textStyle: { fontSize: 13, color: '#333', fontWeight: 600 } },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: params => {
        const p = params[0];
        return `<b>${p.axisValue}</b><br/>${p.marker}剩余规模：<b>${p.value != null ? p.value + ' 亿' : '-'}</b>`;
      }
    },
    grid: { left: '6%', right: '3%', top: 52, bottom: 60, containLabel: true },
    xAxis: {
      type: 'category', data: dates, boundaryGap: false,
      axisLabel: { fontSize: 11, rotate: 30 },
      axisLine: { lineStyle: { color: '#ddd' } },
      splitLine: { show: false }
    },
    yAxis: {
      name: '亿元',
      nameTextStyle: { color: '#7b1fa2', fontSize: 11 },
      type: 'value', scale: true,
      axisLabel: { fontSize: 11, color: '#7b1fa2' },
      axisLine: { show: true, lineStyle: { color: '#7b1fa2' } },
      splitLine: { lineStyle: { color: '#f0f0f0' } }
    },
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider',  start: 0, end: 100, height: 20, bottom: 8 }
    ],
    series: [{
      name: '剩余规模',
      type: 'line', data: amts,
      smooth: false, step: 'end',
      symbol: 'circle', symbolSize: 5,
      lineStyle: { color: '#7b1fa2', width: 2 },
      itemStyle: { color: '#7b1fa2' },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(123,31,162,.18)' },
            { offset: 1, color: 'rgba(123,31,162,.02)' }
          ]
        }
      }
    }]
  };
  remainChart.setOption(option, true);
}
