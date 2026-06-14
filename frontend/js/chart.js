import {
  dark,
  gridC,
  textC,
  tooltipBg,
  tooltipBorder
} from './utils.js';

/* Chart.js 로드 실패 시 조용히 종료 */
if (typeof Chart === 'undefined') {
  console.warn('[chart.js] Chart.js 미로드 — 차트 렌더링 건너뜀');
}

/* ── TIMELINE CHART ── */
const tlMonths = ['22.01','22.04','22.07','22.10','23.01','23.04','23.07','23.10','24.01','24.04','24.07','24.10','25.01','25.04'];
const tlCommits     = [180,210,195,240,310,280,390,420,360,470,510,480,540,420];
const tlContribs    = [55,60,58,70,90,85,105,110,100,120,130,125,140,118];
const tlBugPct      = [10,11,10,13,14,12,16,15,13,15,17,14,16,15];
const timelineCanvas = document.getElementById('chart-timeline');

if (timelineCanvas) {
  new Chart(timelineCanvas.getContext('2d'), {
  type: 'bar',
  data: {
    labels: tlMonths,
    datasets: [
      { label:'커밋 수', data:tlCommits, backgroundColor:'#B5D4F4', borderRadius:2, yAxisID:'y', order:3 },
      { label:'활성 기여자 수', data:tlContribs, type:'line', borderColor:'#1D9E75', backgroundColor:'transparent', borderWidth:2, pointRadius:0, tension:0.4, yAxisID:'y2', order:2 },
      { label:'버그 픽스 비율(%)', data:tlBugPct, type:'line', borderColor:'#EF9F27', backgroundColor:'transparent', borderWidth:1.5, borderDash:[4,3], pointRadius:0, tension:0.4, yAxisID:'y3', order:1 }
    ]
  },
  options: {
    responsive:true, maintainAspectRatio:false,
    interaction:{ mode:'index', intersect:false },
    plugins:{
      legend:{ display:false },
      tooltip:{ backgroundColor:tooltipBg, borderColor:tooltipBorder, borderWidth:0.5, titleColor:dark?'#c2c0b6':'#3d3d3a', bodyColor:textC, padding:10 }
    },
    scales:{
      x:{ grid:{ color:gridC, lineWidth:0.5 }, ticks:{ color:textC }, border:{ color:'transparent' } },
      y:{ position:'left', grid:{ color:gridC, lineWidth:0.5 }, ticks:{ color:textC, maxTicksLimit:5 }, border:{ color:'transparent' } },
      y2:{ position:'right', grid:{ display:false }, ticks:{ color:'#1D9E75', maxTicksLimit:4 }, border:{ color:'transparent' } },
      y3:{ display:false }
    }
  }
});
}



/* ── DONUT CHART ── */
const donutCanvas = document.getElementById('chart-donut');

if (donutCanvas) {
  new Chart(donutCanvas.getContext('2d'), {
  type: 'doughnut',
  data: {
    datasets:[{ data:[33,19,14,12,22], backgroundColor:['#378ADD','#EF9F27','#1D9E75','#F0997B','#B4B2A9'], borderWidth:0, hoverOffset:4 }]
  },
  options: {
    responsive:false, cutout:'68%',
    plugins:{ legend:{ display:false }, tooltip:{ backgroundColor:tooltipBg, borderColor:tooltipBorder, borderWidth:0.5, bodyColor:textC } }
  }
});
}



/* ── RISK SCORE TREND ── */
const riskCanvas = document.getElementById('chart-risk');
const riskMonths = ['1월','2월','3월','4월','5월','6월','7월','8월','9월','10월','11월','12월'];
if (riskCanvas) {
  new Chart(riskCanvas.getContext('2d'),{
  type: 'line',
  data: {
    labels: riskMonths,
    datasets:[
      { label:'ReactDOM.js', data:[0.55,0.58,0.60,0.62,0.65,0.68,0.71,0.74,0.78,0.81,0.84,0.87], borderColor:'#E24B4A', backgroundColor:'transparent', borderWidth:2, pointRadius:0, tension:0.4 },
      { label:'ReactFiberWorkLoop.js', data:[0.50,0.52,0.54,0.55,0.58,0.60,0.62,0.65,0.67,0.69,0.71,0.72], borderColor:'#EF9F27', backgroundColor:'transparent', borderWidth:2, pointRadius:0, tension:0.4 },
      { label:'Scheduler.js', data:[0.40,0.42,0.45,0.48,0.50,0.52,0.54,0.56,0.58,0.60,0.61,0.62], borderColor:'#1D9E75', backgroundColor:'transparent', borderWidth:2, pointRadius:0, tension:0.4 }
    ]
  },
  options:{
    responsive:true, maintainAspectRatio:false,
    plugins:{ legend:{ display:false }, tooltip:{ backgroundColor:tooltipBg, borderColor:tooltipBorder, borderWidth:0.5, titleColor:dark?'#c2c0b6':'#3d3d3a', bodyColor:textC } },
    scales:{
      x:{ grid:{ color:gridC, lineWidth:0.5 }, ticks:{ color:textC, maxTicksLimit:6 }, border:{ color:'transparent' } },
      y:{ min:0.3, max:1.0, grid:{ color:gridC, lineWidth:0.5 }, ticks:{ color:textC, maxTicksLimit:5 }, border:{ color:'transparent' } }
    }
  }
});
}



/* ── COUPLING DISTRIBUTION ── */
const couplingCanvas = document.getElementById('chart-coupling-dist');

if (couplingCanvas) {
  new Chart(couplingCanvas.getContext('2d'), {
  type: 'bar',
  data: {
    labels:['0~10%','10~20%','20~30%','30~40%','40~50%','50~60%','60~70%','70~80%','80~90%','90~100%'],
    datasets:[{ label:'파일 쌍 수', data:[980,720,540,380,260,180,110,62,34,18], backgroundColor:'#9FE1CB', borderRadius:2 }]
  },
  options:{
    responsive:true, maintainAspectRatio:false,
    plugins:{ legend:{ display:false }, tooltip:{ backgroundColor:tooltipBg, borderColor:tooltipBorder, borderWidth:0.5, bodyColor:textC } },
    scales:{
      x:{ grid:{ display:false }, ticks:{ color:textC, maxRotation:45, font:{ size:9 } }, border:{ color:'transparent' } },
      y:{ grid:{ color:gridC, lineWidth:0.5 }, ticks:{ color:textC, maxTicksLimit:4 }, border:{ color:'transparent' } }
    }
  }
});
}



/* ── BUS FACTOR DIST ── */
const busCanvas = document.getElementById('chart-bus');

if (busCanvas) {
  new Chart(busCanvas.getContext('2d'), {
  type: 'bar',
  data: {
    labels:['BF=1','BF=2','BF=3','BF=4','BF=5','BF=6+'],
    datasets:[{
      label:'파일 수',
      data:[23,41,67,89,104,312],
      backgroundColor:['#F09595','#FAC775','#B5D4F4','#9FE1CB','#C0DD97','#D3D1C7'],
      borderRadius:3
    }]
  },
  options:{
    responsive:true, maintainAspectRatio:false,
    plugins:{ legend:{ display:false }, tooltip:{ backgroundColor:tooltipBg, borderColor:tooltipBorder, borderWidth:0.5, bodyColor:textC } },
    scales:{
      x:{ grid:{ display:false }, ticks:{ color:textC }, border:{ color:'transparent' } },
      y:{ grid:{ color:gridC, lineWidth:0.5 }, ticks:{ color:textC, maxTicksLimit:4 }, border:{ color:'transparent' } }
    }
  }
});
}

/* ── ACTIVITY HEATMAP ── */
(function() {
  const contribs = [
    { label:'sophiebits', data:[4,4,3,4,4,3,4,4,3,2,4,4,3,4,4,3] },
    { label:'gaearon',    data:[3,4,4,2,3,4,3,4,4,3,4,3,4,3,4,3] },
    { label:'acdlite',    data:[2,2,3,3,2,3,4,3,2,3,3,2,3,2,3,2] },
    { label:'lunaruan',   data:[1,2,2,1,2,2,1,2,2,1,2,1,2,1,1,2] },
    { label:'trueadm',    data:[0,1,1,0,1,1,0,1,1,0,1,0,1,0,1,0] },
  ];
  const wrap = document.getElementById('act-heatmap');
  if (!wrap) return;

  const mrow = document.createElement('div');
  mrow.className = 'act-months';
  const labels = ['','','','9월','','','','10월','','','','11월','','','','12월'];
  labels.forEach(t => {
    const s = document.createElement('div'); s.className = 'act-m'; s.textContent = t; mrow.appendChild(s);
  });
  wrap.appendChild(mrow);

  contribs.forEach(c => {
    const row = document.createElement('div'); row.className = 'act-row';
    const lbl = document.createElement('div'); lbl.className = 'act-label'; lbl.textContent = c.label; row.appendChild(lbl);
    c.data.forEach(v => {
      const cell = document.createElement('div'); cell.className = 'act-cell hm-' + v; row.appendChild(cell);
    });
    wrap.appendChild(row);
  });
})();

