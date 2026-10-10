"""Single-file dashboard served by the proxy itself: CFO view (spend, trend,
waste $) + engineer view (models, overhead, suggestions with quality deltas).

ponytail: one HTML page + Chart.js CDN instead of a Next.js app — the metrics
logic is the product here. The upgrade path (Next.js + Tremor) is in README.
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>LLM Cost Forensics</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root { --bg:#0f1117; --card:#181b25; --text:#e6e8ee; --dim:#8b90a0; --accent:#4f8cff; --warn:#ffb020; --bad:#ff5d5d; --good:#3ecf8e; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:14px/1.5 -apple-system, 'Segoe UI', sans-serif; }
  header { display:flex; align-items:center; gap:12px; padding:16px 24px; border-bottom:1px solid #232736; }
  header h1 { font-size:17px; margin:0; }
  header .sub { color:var(--dim); font-size:12px; }
  input { background:var(--card); color:var(--text); border:1px solid #2a2f40; border-radius:8px; padding:8px 10px; width:340px; }
  button { background:var(--accent); border:0; color:#fff; border-radius:8px; padding:8px 14px; cursor:pointer; }
  main { padding:20px 24px; max-width:1200px; margin:0 auto; }
  .kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; }
  .card { background:var(--card); border:1px solid #232736; border-radius:12px; padding:14px 16px; }
  .kpi .v { font-size:22px; font-weight:700; margin-top:2px; }
  .kpi .l { color:var(--dim); font-size:12px; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:12px; }
  h2 { font-size:13px; color:var(--dim); text-transform:uppercase; letter-spacing:.06em; margin:0 0 10px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; color:var(--dim); font-weight:500; padding:6px 8px; border-bottom:1px solid #232736; }
  td { padding:6px 8px; border-bottom:1px solid #1d2130; }
  .tag { display:inline-block; padding:1px 8px; border-radius:99px; font-size:11px; }
  .tag.safe { background:#12352a; color:var(--good); } .tag.risky { background:#3a2f12; color:var(--warn); }
  .tag.unsafe, .tag.critical { background:#3a1717; color:var(--bad); }
  .tag.unevaluated, .tag.warning { background:#252a3a; color:var(--dim); }
  #alerts .card { border-color:var(--bad); margin-bottom:12px; }
</style>
</head>
<body>
<header>
  <h1>💸 LLM Cost Forensics</h1>
  <span class="sub">where the AI budget actually goes</span>
  <span style="flex:1"></span>
  <input id="key" placeholder="proxy key (cf_...) — admin key sees all teams" />
  <button onclick="saveKey()">Load</button>
</header>
<main>
  <div id="alerts"></div>
  <div class="kpis" id="kpis"></div>
  <div class="grid2">
    <div class="card"><h2>Spend per day</h2><canvas id="trend" height="220"></canvas></div>
    <div class="card"><h2>Spend by feature</h2><canvas id="features" height="220"></canvas></div>
  </div>
  <div class="grid2">
    <div class="card"><h2>Waste by feature × cause</h2><table id="waste"></table></div>
    <div class="card"><h2>Spend by team</h2><canvas id="teams" height="220"></canvas></div>
  </div>
  <div class="card" style="margin-top:12px"><h2>Cheaper-model suggestions (quality-checked)</h2><table id="suggestions"></table></div>
</main>
<script>
const $ = id => document.getElementById(id);
let charts = {};
function saveKey(){ localStorage.setItem('cfkey', $('key').value.trim()); load(); }
async function api(path){
  const r = await fetch(path, {headers:{Authorization:'Bearer '+(localStorage.getItem('cfkey')||'')}});
  if(!r.ok) throw new Error(path+' -> '+r.status);
  return r.json();
}
function chart(id, cfg){ if(charts[id]) charts[id].destroy(); charts[id] = new Chart($(id), cfg); }
function money(x){ return '$'+(+x).toFixed(2); }
// every API value is caller-influenced (X-Feature, team names): escape before innerHTML
function esc(x){ return String(x).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]); }
async function load(){
  try{
    const [s, trend, feats, teams, waste, sugg, alerts] = await Promise.all([
      api('/api/summary'), api('/api/trend'), api('/api/by/feature'),
      api('/api/by/team'), api('/api/waste'), api('/api/suggestions'), api('/api/alerts')]);
    $('kpis').innerHTML = [
      ['Total spend', money(s.total_cost_usd)], ['Calls', esc(s.calls)],
      ['Waste', money(s.waste_cost_usd)+' ('+esc(s.waste_pct)+'%)'],
      ['Cache saved', money(s.cache_saved_usd)],
      ['Overhead p50/p99', esc(s.proxy_overhead_ms_p50)+' / '+esc(s.proxy_overhead_ms_p99)+' ms'],
      ['Errors', esc(s.errors)]
    ].map(([l,v])=>`<div class="card kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join('');
    $('alerts').innerHTML = alerts.map(a =>
      `<div class="card">🚨 <b>${esc(a.team)}</b> is at <b>${esc(a.pct)}%</b> of its $${esc(a.budget_usd)} monthly budget (MTD ${money(a.mtd_usd)}) <span class="tag ${esc(a.level)}">${esc(a.level)}</span></div>`).join('');
    chart('trend', {type:'line', data:{labels:trend.map(t=>t.day),
      datasets:[{data:trend.map(t=>t.cost_usd), borderColor:'#4f8cff', backgroundColor:'#4f8cff33', fill:true, tension:.3}]},
      options:{plugins:{legend:{display:false}}, scales:{y:{ticks:{callback:v=>'$'+v}}}}});
    chart('features', {type:'bar', data:{labels:feats.map(f=>f.feature),
      datasets:[{data:feats.map(f=>f.cost_usd), backgroundColor:'#3ecf8e'}]},
      options:{plugins:{legend:{display:false}}, scales:{y:{ticks:{callback:v=>'$'+v}}}}});
    chart('teams', {type:'doughnut', data:{labels:teams.map(t=>t.team),
      datasets:[{data:teams.map(t=>t.cost_usd), backgroundColor:['#4f8cff','#3ecf8e','#ffb020','#ff5d5d','#a78bfa','#f472b6']}]},
      options:{plugins:{legend:{position:'right'}}}});
    $('waste').innerHTML = '<tr><th>Feature</th><th>Cause</th><th>Calls</th><th>Cost</th></tr>' +
      (waste.length ? waste.map(w=>`<tr><td>${esc(w.feature)}</td><td>${esc(w.flag)}</td><td>${esc(w.calls)}</td><td>${money(w.cost_usd)}</td></tr>`).join('') : '<tr><td colspan=4 style="color:var(--dim)">no waste detected 🎉</td></tr>');
    $('suggestions').innerHTML = '<tr><th>Feature</th><th>Current</th><th>Suggested</th><th>Spend</th><th>Projected</th><th>Savings</th><th>Quality</th></tr>' +
      (sugg.length ? sugg.map(x=>{
        const q = x.quality; const tag = `<span class="tag ${esc(q.verdict)}">${esc(q.verdict)}</span>`;
        const detail = q.verdict==='unevaluated' ? '' : ` win ${(q.win_rate*100).toFixed(0)}% · Δ${esc(q.delta)} · n=${esc(q.samples)}`;
        return `<tr><td>${esc(x.feature)}</td><td>${esc(x.current_model)}</td><td>${esc(x.suggested_model)}</td><td>${money(x.current_cost_usd)}</td><td>${money(x.projected_cost_usd)}</td><td><b>${money(x.monthly_savings_usd)}</b></td><td>${tag}${detail}</td></tr>`;
      }).join('') : '<tr><td colspan=7 style="color:var(--dim)">no substitution candidates yet</td></tr>');
  }catch(e){
    $('kpis').innerHTML = `<div class="card kpi"><div class="l">error</div><div class="v" style="font-size:14px">${esc(e.message)} — paste a valid proxy key above</div></div>`;
  }
}
$('key').value = localStorage.getItem('cfkey')||'';
if($('key').value) load();
</script>
</body>
</html>"""
