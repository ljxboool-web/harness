/* CF-Profiler Web — single-file SPA. 直接与 /api/* 通讯，Chart.js 渲染所有图表。 */

'use strict';

// ========== constants ==========

const SKILL_DIMS = ['dp', 'graph', 'math', 'greedy', 'data_structure', 'string', 'search', 'geometry'];
const TRAIT_DIMS = ['stability', 'speed', 'pressure', 'breakthrough', 'activity'];
const VERDICT_SLOTS = ['ok', 'wrong_answer', 'time_limit_exceeded', 'memory_limit_exceeded', 'runtime_error', 'compilation_error', 'other'];
const VERDICT_COLORS = ['#3fb950', '#f85149', '#d29922', '#bc8cff', '#ff7b72', '#6e7681', '#484f58'];
const JUDGE_NAMES = ['strict', 'lenient', 'data'];
const JUDGE_COLORS = { strict: '#f85149', lenient: '#3fb950', data: '#58a6ff' };
const HEATMAP_LEVELS = ['#161b22', '#0e4429', '#006d32', '#26a641', '#39d353'];

Chart.defaults.color = '#c9d1d9';
Chart.defaults.borderColor = '#30363d';
Chart.defaults.font.family = 'JetBrains Mono, SF Mono, Menlo, monospace';
Chart.defaults.animation = false;

// ========== state ==========

const state = {
  handle: null,
  analysis: null,          // { user, aggregated, report, rating_history }
  narrativeTrace: null,    // SSE attempt events
  narrativeFinal: null,    // final narrative payload
  practicePlan: null,
  baselineDiff: null,
  metrics: null,
  autoRefreshTimer: null,
  currentNarrateStream: null,
  requestControllers: {
    analyze: null,
    baseline: null,
    metrics: null,
    recommendations: null,
  },
};

const charts = {};  // keyed by canvas id

// ========== API ==========

async function api(path, opts = {}) {
  const resp = await fetch(path, opts);
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body.detail) detail = body.detail;
    } catch {}
    throw new Error(detail);
  }
  return resp.json();
}

function abortRequest(key) {
  const controller = state.requestControllers[key];
  if (controller) {
    controller.abort();
    state.requestControllers[key] = null;
  }
}

function createRequestController(key) {
  abortRequest(key);
  const controller = new AbortController();
  state.requestControllers[key] = controller;
  return controller;
}

function closeNarrateStream() {
  if (state.currentNarrateStream) {
    state.currentNarrateStream.close();
    state.currentNarrateStream = null;
  }
}

// ========== chart helpers ==========

function upsertChart(id, config) {
  const canvas = document.getElementById(id);
  if (!canvas) return null;
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
  const chart = new Chart(canvas, config);
  charts[id] = chart;
  return chart;
}

function clearChart(id) {
  const canvas = document.getElementById(id);
  if (charts[id]) {
    charts[id].destroy();
    delete charts[id];
  }
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
}

function scoreColor(score) {
  if (score >= 70) return '#3fb950';
  if (score >= 40) return '#d29922';
  return '#f85149';
}

function judgeScoreClass(s) {
  if (s >= 4) return 'score-high';
  if (s >= 3) return 'score-mid';
  return 'score-low';
}

// ========== status chip ==========

function setStatus(text, kind = '') {
  const el = document.getElementById('status-chip');
  el.textContent = text;
  el.className = 'chip' + (kind ? ' ' + kind : '');
}

// ========== samples ==========

async function loadSamples() {
  try {
    const handles = await api('/api/samples');
    const dl = document.getElementById('sample-handles');
    dl.replaceChildren(...handles.map((handle) => {
      const option = document.createElement('option');
      option.value = handle;
      return option;
    }));
  } catch (e) {
    console.warn('loadSamples failed:', e);
  }
}

// ========== router ==========

function activateTab(tab) {
  document.querySelectorAll('.tab').forEach(el => {
    el.hidden = el.dataset.tab !== tab;
  });
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tab);
  });
  if (tab === 'judge') renderJudge();
  if (tab === 'baseline' && state.handle) loadBaseline();
  if (tab !== 'baseline') abortRequest('baseline');
  if (tab === 'metrics') {
    loadMetrics();
    startAutoRefresh();
  } else {
    stopAutoRefresh();
    abortRequest('metrics');
  }
}

function onHashChange() {
  const tab = (location.hash || '#profile').slice(1);
  activateTab(tab);
}

// ========== PROFILE TAB ==========

async function runAnalyze(handle, submissions) {
  setStatus(`分析 ${handle} …`, 'info');
  document.getElementById('analyze-btn').disabled = true;
  closeNarrateStream();
  stopAutoRefresh();
  abortRequest('baseline');
  abortRequest('metrics');
  abortRequest('recommendations');
  const controller = createRequestController('analyze');
  try {
    const data = await api(
      `/api/analyze/${encodeURIComponent(handle)}?submissions=${submissions}`,
      { signal: controller.signal },
    );
    if (state.requestControllers.analyze !== controller) return;
    state.handle = handle;
    state.analysis = data;
    state.narrativeTrace = null;
    state.narrativeFinal = null;
    state.practicePlan = null;
    state.baselineDiff = null;
    renderProfile();
    setStatus(`已加载 ${handle}`, 'ok');
    if (location.hash !== '#profile') location.hash = '#profile';
    else activateTab('profile');
  } catch (e) {
    if (e.name === 'AbortError') return;
    setStatus(`失败: ${e.message}`, 'err');
    console.error(e);
  } finally {
    if (state.requestControllers.analyze === controller) {
      state.requestControllers.analyze = null;
    }
    document.getElementById('analyze-btn').disabled = false;
  }
}

function renderProfile() {
  const { user, aggregated, report, rating_history } = state.analysis;
  document.getElementById('profile-empty').hidden = true;
  document.getElementById('profile-content').hidden = false;

  // Hero
  const ac = aggregated.verdicts;
  const totalV = ac.ok + ac.wrong_answer + ac.time_limit_exceeded + ac.memory_limit_exceeded + ac.runtime_error + ac.compilation_error + ac.other;
  const acRate = totalV > 0 ? (ac.ok / totalV * 100).toFixed(1) : '0.0';
  document.getElementById('hero-card').innerHTML = `
    <div>
      <div class="hero-handle">${escapeHTML(user.handle)}</div>
      <div class="hero-rank">${escapeHTML(user.rank || 'unrated')} · peak ${escapeHTML(user.maxRank || '—')}</div>
    </div>
    <div class="hero-stats">
      <div class="hero-stat"><span class="hero-stat-label">Rating</span><span class="hero-stat-value">${user.rating ?? '—'}</span></div>
      <div class="hero-stat"><span class="hero-stat-label">Peak</span><span class="hero-stat-value">${user.maxRating ?? '—'}</span></div>
      <div class="hero-stat"><span class="hero-stat-label">Contests</span><span class="hero-stat-value">${aggregated.rating.contests}</span></div>
      <div class="hero-stat"><span class="hero-stat-label">AC Rate</span><span class="hero-stat-value">${acRate}%</span></div>
    </div>
  `;

  renderSkillsRadar(report.skills);
  renderTraitsRadar(report.traits);
  renderDifficultyBar(aggregated.difficulty_buckets);
  renderVerdictDonut(ac);
  renderRatingLine(rating_history);
  renderHeatmap(aggregated.daily_submission_count);

  // reset narrative card
  document.getElementById('narrative-body').innerHTML = `
    <div class="muted small">点击「生成评语」调用 analyzer.generate_narrative_with_judge。
      无 DASHSCOPE_API_KEY 时走本地模板。</div>
  `;
  document.getElementById('narrate-chip').textContent = '';
  document.getElementById('narrate-chip').className = 'chip dim';
  document.getElementById('problemset-body').innerHTML = `
    <div class="muted small">根据 rating 和薄弱技能推荐未尝试过的 Codeforces 题目。</div>
  `;
  document.getElementById('problemset-chip').textContent = '';
  document.getElementById('problemset-chip').className = 'chip dim';
}

function renderSkillsRadar(skills) {
  const bySkill = Object.fromEntries(skills.map(s => [s.dimension, s]));
  upsertChart('chart-skills', {
    type: 'radar',
    data: {
      labels: SKILL_DIMS,
      datasets: [{
        label: 'score',
        data: SKILL_DIMS.map(d => bySkill[d]?.score ?? 0),
        backgroundColor: 'rgba(0, 191, 165, 0.18)',
        borderColor: '#00bfa5',
        pointBackgroundColor: SKILL_DIMS.map(d => scoreColor(bySkill[d]?.score ?? 0)),
        pointBorderColor: '#0d1117',
        pointRadius: 4,
      }],
    },
    options: {
      scales: {
        r: { min: 0, max: 100, ticks: { display: false, stepSize: 20 },
          grid: { color: '#30363d' }, angleLines: { color: '#30363d' },
          pointLabels: { color: '#c9d1d9', font: { size: 11 } } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => {
          const s = bySkill[SKILL_DIMS[ctx.dataIndex]];
          return `${s.dimension}: ${s.score} · ${s.solved} AC / ${s.attempted} attempted · conf=${s.confidence}`;
        } } },
      },
    },
  });
}

function renderTraitsRadar(traits) {
  const byTrait = Object.fromEntries(traits.map(t => [t.dimension, t]));
  upsertChart('chart-traits', {
    type: 'radar',
    data: {
      labels: TRAIT_DIMS,
      datasets: [{
        label: 'score',
        data: TRAIT_DIMS.map(d => byTrait[d]?.score ?? 0),
        backgroundColor: 'rgba(88, 166, 255, 0.18)',
        borderColor: '#58a6ff',
        pointBackgroundColor: TRAIT_DIMS.map(d => scoreColor(byTrait[d]?.score ?? 0)),
        pointBorderColor: '#0d1117',
        pointRadius: 4,
      }],
    },
    options: {
      scales: {
        r: { min: 0, max: 100, ticks: { display: false, stepSize: 20 },
          grid: { color: '#30363d' }, angleLines: { color: '#30363d' },
          pointLabels: { color: '#c9d1d9', font: { size: 11 } } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => {
          const t = byTrait[TRAIT_DIMS[ctx.dataIndex]];
          return `${t.dimension}: ${t.score} · ${t.evidence}`;
        } } },
      },
    },
  });
}

function renderDifficultyBar(buckets) {
  const labels = buckets.map(b => `${b.lo}-${b.hi}`);
  const solved = buckets.map(b => b.solved);
  const colors = buckets.map(b => {
    if (!b.attempted) return '#484f58';
    const rate = b.solved / b.attempted;
    if (rate >= 0.7) return '#3fb950';
    if (rate >= 0.4) return '#d29922';
    return '#f85149';
  });
  upsertChart('chart-difficulty', {
    type: 'bar',
    data: {
      labels,
      datasets: [{ label: 'solved', data: solved, backgroundColor: colors, borderRadius: 2 }],
    },
    options: {
      scales: {
        x: { grid: { display: false }, ticks: { color: '#8b949e', font: { size: 10 } } },
        y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' }, beginAtZero: true },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => {
          const b = buckets[ctx.dataIndex];
          const rate = b.attempted ? (b.solved / b.attempted * 100).toFixed(0) : '—';
          return `${b.lo}-${b.hi}: solved ${b.solved} / attempted ${b.attempted} (${rate}%)`;
        } } },
      },
    },
  });
}

function renderVerdictDonut(v) {
  const data = VERDICT_SLOTS.map(k => v[k] ?? 0);
  const nonzero = data.some(x => x > 0);
  if (!nonzero) {
    clearChart('chart-verdict');
    return;
  }
  upsertChart('chart-verdict', {
    type: 'doughnut',
    data: {
      labels: VERDICT_SLOTS,
      datasets: [{ data, backgroundColor: VERDICT_COLORS, borderColor: '#0d1117', borderWidth: 2 }],
    },
    options: {
      plugins: {
        legend: { position: 'right', labels: { color: '#c9d1d9', font: { size: 11 } } },
      },
      cutout: '55%',
    },
  });
}

function renderRatingLine(history) {
  if (!history.length) {
    clearChart('chart-rating');
    return;
  }
  const sorted = [...history].sort((a, b) => a.ts - b.ts);
  const labels = sorted.map(r => new Date(r.ts * 1000).toISOString().slice(0, 10));
  const data = sorted.map(r => r.newRating);
  upsertChart('chart-rating', {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'rating',
        data,
        borderColor: '#00bfa5',
        backgroundColor: 'rgba(0, 191, 165, 0.1)',
        fill: true,
        tension: 0.25,
        pointRadius: 2,
        pointHoverRadius: 6,
      }],
    },
    options: {
      scales: {
        x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e', maxTicksLimit: 8, font: { size: 10 } } },
        y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => {
          const r = sorted[ctx.dataIndex];
          const sign = r.delta >= 0 ? '+' : '';
          return `${r.contestName} · rank ${r.rank} · ${r.newRating} (${sign}${r.delta})`;
        } } },
      },
    },
  });
}

function renderHeatmap(dailyMap) {
  const container = document.getElementById('heatmap-container');
  const days = 180;
  const cell = 12, gap = 2;
  const now = new Date();
  const cells = [];
  const values = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setUTCDate(now.getUTCDate() - i);
    const key = d.toISOString().slice(0, 10);
    const count = dailyMap[key] || 0;
    values.push(count);
    cells.push({ key, count, date: d });
  }
  const maxV = Math.max(1, ...values);
  const levelOf = (c) => {
    if (c === 0) return 0;
    if (c <= maxV * 0.2) return 1;
    if (c <= maxV * 0.4) return 2;
    if (c <= maxV * 0.7) return 3;
    return 4;
  };

  const cols = Math.ceil(days / 7);
  const firstDow = cells[0].date.getUTCDay();
  const width = cols * (cell + gap);
  const height = 7 * (cell + gap);
  let svg = `<svg width="${width}" height="${height + 16}" viewBox="0 0 ${width} ${height + 16}">`;
  cells.forEach((c, i) => {
    const gridIdx = i + firstDow;
    const col = Math.floor(gridIdx / 7);
    const row = gridIdx % 7;
    const x = col * (cell + gap);
    const y = row * (cell + gap);
    const lvl = levelOf(c.count);
    svg += `<rect x="${x}" y="${y}" width="${cell}" height="${cell}" fill="${HEATMAP_LEVELS[lvl]}"><title>${c.key}: ${c.count}</title></rect>`;
  });
  svg += `</svg>`;
  container.innerHTML = svg;

  const legend = document.getElementById('heatmap-legend');
  legend.innerHTML = '少 ' + HEATMAP_LEVELS.map(c =>
    `<span class="legend-swatch" style="background:${c}"></span>`
  ).join('') + ' 多';
}

// ========== NARRATIVE (SSE) ==========

function startNarrate() {
  if (!state.handle || !state.analysis) return;
  closeNarrateStream();
  const btn = document.getElementById('narrate-btn');
  const chip = document.getElementById('narrate-chip');
  const body = document.getElementById('narrative-body');
  btn.disabled = true;
  chip.textContent = '启动…';
  chip.className = 'chip info';
  body.innerHTML = '<div class="muted small">生成中…</div>';

  state.narrativeTrace = [];
  state.narrativeFinal = null;

  const subs = document.getElementById('submissions-input').value;
  const url = `/api/narrate/${encodeURIComponent(state.handle)}?submissions=${subs}`;
  const es = new EventSource(url);
  state.currentNarrateStream = es;

  const finishWithError = (message) => {
    body.innerHTML = `<div class="chip err">错误: ${escapeHTML(message)}</div>`;
    chip.textContent = '失败';
    chip.className = 'chip err';
    btn.disabled = false;
    if (state.currentNarrateStream === es) {
      state.currentNarrateStream = null;
    }
    es.close();
  };

  es.addEventListener('attempt', (e) => {
    const d = JSON.parse(e.data);
    state.narrativeTrace.push(d);
    const indiv = d.individual.map(x => `${x.judge_name}=${x.score}`).join(' ');
    const noKey = (d.combined_reason || '').includes('无 API key');
    const kind = d.median_score >= 4 ? 'ok' : d.median_score >= 3 ? 'warn' : 'err';
    chip.textContent = noKey
      ? `未检测到 DASHSCOPE_API_KEY · 已用本地模板 · [${indiv}]`
      : `第 ${d.attempt} 次 · median ${d.median_score}/5 · [${indiv}]`;
    chip.className = 'chip ' + kind;
    if ((location.hash || '#profile') === '#judge') renderJudge();
  });
  es.addEventListener('done', (e) => {
    const d = JSON.parse(e.data);
    state.narrativeFinal = d;
    renderNarrativeBody(d);
    const finalKind = d.judge.median_score >= 4 ? 'ok' : d.judge.median_score >= 3 ? 'warn' : 'err';
    const indiv = d.judge.individual.map(x => `${x.judge_name}=${x.score}`).join(' ');
    const noKey = (d.judge.combined_reason || '').includes('无 API key')
      || (d.narrative || '').includes('未检测到 DASHSCOPE_API_KEY');
    chip.textContent = noKey
      ? `未检测到 DASHSCOPE_API_KEY · 已用本地模板 · [${indiv}]`
      : `完成 · median ${d.judge.median_score}/5 · [${indiv}] · ${d.trace.length} 轮`;
    chip.className = 'chip ' + finalKind;
    btn.disabled = false;
    if ((location.hash || '#profile') === '#judge') renderJudge();
    if (state.currentNarrateStream === es) {
      state.currentNarrateStream = null;
    }
    es.close();
  });
  es.addEventListener('error', (e) => {
    if (e?.data) {
      try {
        const d = JSON.parse(e.data);
        finishWithError(d.message || '连接中断');
        return;
      } catch {}
    }
    finishWithError('SSE 连接中断');
  });
  es.onerror = () => {
    if (state.currentNarrateStream === es) {
      finishWithError('SSE 连接中断');
    }
  };
}

function renderNarrativeBody(done) {
  const text = done.narrative || '';
  const body = document.getElementById('narrative-body');
  const noKey = (done.judge?.combined_reason || '').includes('无 API key')
    || text.includes('未检测到 DASHSCOPE_API_KEY');
  const notice = noKey
    ? '<div class="chip warn">未检测到 DASHSCOPE_API_KEY，当前显示本地模板评语</div>'
    : '';
  const parts = { strong: '', weak: '', advice: '' };
  const patterns = [
    ['strong', /【强项】([\s\S]*?)(?=【弱项】|【建议】|$)/],
    ['weak', /【弱项】([\s\S]*?)(?=【建议】|$)/],
    ['advice', /【建议】([\s\S]*?)$/],
  ];
  for (const [k, re] of patterns) {
    const m = text.match(re);
    if (m) parts[k] = m[1].trim();
  }
  if (!parts.strong && !parts.weak && !parts.advice) {
    body.innerHTML = `${notice}<pre class="narrative-raw">${escapeHTML(text)}</pre>`;
    return;
  }
  body.innerHTML = `
    ${notice}
    <div class="narrative-section strong"><span class="label">强项</span>${escapeHTML(parts.strong)}</div>
    <div class="narrative-section weak"><span class="label">弱项</span>${escapeHTML(parts.weak)}</div>
    <div class="narrative-section advice"><span class="label">建议</span>${escapeHTML(parts.advice)}</div>
  `;
}

function escapeHTML(s) {
  return String(s || '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])
  );
}

// ========== PRACTICE PLAN ==========

async function loadPracticePlan() {
  if (!state.handle || !state.analysis) return;
  const btn = document.getElementById('problemset-btn');
  const chip = document.getElementById('problemset-chip');
  const body = document.getElementById('problemset-body');
  btn.disabled = true;
  chip.textContent = '生成中…';
  chip.className = 'chip info';
  body.innerHTML = '<div class="muted small">正在筛选题目…</div>';
  const subs = document.getElementById('submissions-input').value;
  const controller = createRequestController('recommendations');
  try {
    const plan = await api(
      `/api/recommendations/${encodeURIComponent(state.handle)}?submissions=${subs}&limit=9`,
      { signal: controller.signal },
    );
    if (state.requestControllers.recommendations !== controller) return;
    state.practicePlan = plan;
    renderPracticePlan(plan);
    const source = plan.source === 'dashscope' ? 'DashScope' : '本地推荐器';
    chip.textContent = `${source} · ${plan.problems.length} 题 · ${plan.target_rating_min}-${plan.target_rating_max}`;
    chip.className = 'chip ok';
  } catch (e) {
    if (e.name === 'AbortError') return;
    chip.textContent = '失败';
    chip.className = 'chip err';
    body.innerHTML = `<div class="chip err">题单生成失败: ${escapeHTML(e.message)}</div>`;
  } finally {
    if (state.requestControllers.recommendations === controller) {
      state.requestControllers.recommendations = null;
    }
    btn.disabled = false;
  }
}

function renderPracticePlan(plan) {
  const body = document.getElementById('problemset-body');
  if (!plan.problems || !plan.problems.length) {
    body.innerHTML = '<div class="empty-row muted">没有找到匹配题目，可以调大 submissions 或稍后重试</div>';
    return;
  }
  const weak = (plan.weak_skills || []).map(s => `<span class="skill-pill">${escapeHTML(s)}</span>`).join('');
  body.innerHTML = `
    <div class="plan-summary">
      <div>${escapeHTML(plan.summary)}</div>
      <div class="plan-meta">
        <span class="chip info">rating ${plan.rating ?? '—'}</span>
        <span class="chip">target ${plan.target_rating_min}-${plan.target_rating_max}</span>
        ${weak}
      </div>
    </div>
    <div class="problem-grid">
      ${plan.problems.map((p, idx) => `
        <a class="problem-card" href="${escapeHTML(p.url)}" target="_blank" rel="noopener noreferrer">
          <div class="problem-topline">
            <span class="mono">${idx + 1}. ${p.contest_id}${escapeHTML(p.index)}</span>
            <span class="problem-rating">${p.rating}</span>
          </div>
          <div class="problem-name">${escapeHTML(p.name)}</div>
          <div class="problem-reason">${escapeHTML(p.reason)}</div>
          <div class="problem-tags">
            <span class="skill-pill">${escapeHTML(p.target_skill)}</span>
            ${(p.tags || []).slice(0, 4).map(t => `<span>${escapeHTML(t)}</span>`).join('')}
          </div>
          <div class="muted small">solved by ${p.solved_count ?? 0}</div>
        </a>
      `).join('')}
    </div>
  `;
}

// ========== JUDGE TAB ==========

function renderJudge() {
  const empty = document.getElementById('judge-empty');
  const content = document.getElementById('judge-content');
  const trace = state.narrativeTrace;
  if (!trace || trace.length === 0) {
    empty.hidden = false;
    content.hidden = true;
    return;
  }
  empty.hidden = true;
  content.hidden = false;

  document.getElementById('judge-attempts').textContent = trace.length;
  const finalScore = trace[trace.length - 1].median_score;
  const finalEl = document.getElementById('judge-final');
  finalEl.textContent = `${finalScore}/5`;
  finalEl.className = 'metric-value ' + (finalScore >= 4 ? 'ok' : finalScore >= 3 ? 'warn' : 'err');
  const firstPass = trace.length === 1 && finalScore >= 4;
  const fpEl = document.getElementById('judge-firstpass');
  fpEl.textContent = firstPass ? 'YES' : 'NO';
  fpEl.className = 'metric-value ' + (firstPass ? 'ok' : 'warn');

  // Chart: lines for strict / lenient / data + thick median line
  const labels = trace.map((_, i) => `第 ${i + 1} 次`);
  const seriesFor = (name) => trace.map(a => {
    const r = a.individual.find(r => r.judge_name === name);
    return r ? r.score : null;
  });
  upsertChart('chart-judge', {
    type: 'line',
    data: {
      labels,
      datasets: [
        ...JUDGE_NAMES.map(name => ({
          label: name,
          data: seriesFor(name),
          borderColor: JUDGE_COLORS[name],
          backgroundColor: JUDGE_COLORS[name] + '33',
          borderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
          tension: 0.15,
        })),
        {
          label: 'median',
          data: trace.map(a => a.median_score),
          borderColor: '#c9d1d9',
          borderWidth: 4,
          borderDash: [6, 3],
          pointRadius: 5,
          pointHoverRadius: 8,
          tension: 0.0,
        },
      ],
    },
    options: {
      scales: {
        y: { min: 1, max: 5, ticks: { stepSize: 1, color: '#8b949e' }, grid: { color: '#21262d' } },
        x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
      },
      plugins: {
        legend: { labels: { color: '#c9d1d9', boxWidth: 16 } },
      },
    },
  });

  // Reasons
  const reasons = document.getElementById('judge-reasons');
  reasons.innerHTML = trace.map((a, i) => `
    <div class="judge-attempt">
      <div class="judge-attempt-header">
        <strong>第 ${a.attempt || (i + 1)} 次</strong>
        <span class="${judgeScoreClass(a.median_score)}">median ${a.median_score}/5</span>
      </div>
      <div class="judge-attempt-reasons">
        ${a.individual.map(r => `
          <span>${escapeHTML(r.judge_name)}</span>
          <span>${escapeHTML(r.reason || '')}</span>
          <span class="score ${judgeScoreClass(r.score)}">${r.score}/5</span>
        `).join('')}
      </div>
    </div>
  `).join('');
}

// ========== BASELINE TAB ==========

async function loadBaseline() {
  if (!state.handle) return;
  const empty = document.getElementById('baseline-empty');
  const content = document.getElementById('baseline-content');
  empty.hidden = true;
  content.hidden = false;

  const threshold = document.getElementById('threshold-input').value;
  const subs = document.getElementById('submissions-input').value;
  const controller = createRequestController('baseline');
  try {
    const diff = await api(
      `/api/baseline/${encodeURIComponent(state.handle)}/diff?threshold=${threshold}&submissions=${subs}`,
      { signal: controller.signal },
    );
    if (state.requestControllers.baseline !== controller) return;
    state.baselineDiff = diff;
    renderBaseline(diff);
  } catch (e) {
    if (e.name === 'AbortError') return;
    setStatus(`baseline 获取失败: ${e.message}`, 'err');
  } finally {
    if (state.requestControllers.baseline === controller) {
      state.requestControllers.baseline = null;
    }
  }
}

function renderBaseline(diff) {
  const stateLabel = document.getElementById('baseline-state-label');
  const snapshot = document.getElementById('baseline-snapshot');
  if (!diff.exists) {
    stateLabel.textContent = '无 baseline';
    stateLabel.className = 'chip warn';
    snapshot.textContent = '点击右侧按钮创建首个 baseline';
    ['chart-baseline-skills', 'chart-baseline-traits'].forEach(clearChart);
    document.getElementById('drift-table-container').innerHTML =
      '<div class="empty-row muted">尚无 baseline，创建后再比对</div>';
    return;
  }
  const snapDate = new Date((diff.baseline.snapshot_at || 0) * 1000).toISOString().replace('T', ' ').slice(0, 19);
  stateLabel.textContent = `baseline at ${snapDate}`;
  stateLabel.className = 'chip info';
  snapshot.textContent = `rating ${diff.baseline.rating ?? '—'} · peak ${diff.baseline.peak ?? '—'}`;

  const oldSkills = SKILL_DIMS.map(d => diff.baseline.skills?.[d] ?? 0);
  const newSkills = SKILL_DIMS.map(d => diff.current.skills[d] ?? 0);
  upsertChart('chart-baseline-skills', {
    type: 'radar',
    data: {
      labels: SKILL_DIMS,
      datasets: [
        { label: 'baseline', data: oldSkills, backgroundColor: 'rgba(139, 148, 158, 0.25)', borderColor: '#8b949e', pointRadius: 3 },
        { label: 'current', data: newSkills, backgroundColor: 'rgba(0, 191, 165, 0.15)', borderColor: '#00bfa5', pointRadius: 3 },
      ],
    },
    options: radarOpts(),
  });
  const oldTraits = TRAIT_DIMS.map(d => diff.baseline.traits?.[d] ?? 0);
  const newTraits = TRAIT_DIMS.map(d => diff.current.traits[d] ?? 0);
  upsertChart('chart-baseline-traits', {
    type: 'radar',
    data: {
      labels: TRAIT_DIMS,
      datasets: [
        { label: 'baseline', data: oldTraits, backgroundColor: 'rgba(139, 148, 158, 0.25)', borderColor: '#8b949e', pointRadius: 3 },
        { label: 'current', data: newTraits, backgroundColor: 'rgba(88, 166, 255, 0.15)', borderColor: '#58a6ff', pointRadius: 3 },
      ],
    },
    options: radarOpts(),
  });

  const tbl = document.getElementById('drift-table-container');
  if (!diff.drifts.length) {
    tbl.innerHTML = `<div class="empty-row muted">0 drift (阈值 ${diff.threshold})</div>`;
  } else {
    tbl.innerHTML = `
      <table class="drift-table">
        <thead><tr><th>维度</th><th class="num">old</th><th class="num">new</th><th class="num">Δ</th></tr></thead>
        <tbody>${diff.drifts.map(d => `
          <tr>
            <td>${escapeHTML(d.dimension)}</td>
            <td class="num">${d.old.toFixed(1)}</td>
            <td class="num">${d.new.toFixed(1)}</td>
            <td class="num ${d.delta >= 0 ? 'delta-pos' : 'delta-neg'}">${d.delta >= 0 ? '+' : ''}${d.delta.toFixed(1)}</td>
          </tr>
        `).join('')}</tbody>
      </table>
    `;
  }
}

function radarOpts() {
  return {
    scales: {
      r: { min: 0, max: 100, ticks: { display: false },
        grid: { color: '#30363d' }, angleLines: { color: '#30363d' },
        pointLabels: { color: '#c9d1d9', font: { size: 11 } } },
    },
    plugins: { legend: { labels: { color: '#c9d1d9', boxWidth: 16 } } },
  };
}

async function saveBaseline() {
  if (!state.handle) return;
  if (!confirm(`以 ${state.handle} 当前分析覆盖 baselines/${state.handle}.json?`)) return;
  const btn = document.getElementById('baseline-save-btn');
  btn.disabled = true;
  try {
    const subs = document.getElementById('submissions-input').value;
    const resp = await api(`/api/baseline/${encodeURIComponent(state.handle)}?submissions=${subs}`, { method: 'POST' });
    setStatus(`baseline 已保存: ${resp.path}`, 'ok');
    await loadBaseline();
  } catch (e) {
    setStatus(`保存失败: ${e.message}`, 'err');
  } finally {
    btn.disabled = false;
  }
}

// ========== METRICS TAB ==========

async function loadMetrics() {
  const since = document.getElementById('since-input').value;
  const url = since === '0' ? '/api/metrics' : `/api/metrics?since=${since}`;
  const controller = createRequestController('metrics');
  try {
    const [m, logs] = await Promise.all([
      api(url, { signal: controller.signal }),
      api('/api/logs/judge?limit=30', { signal: controller.signal }),
    ]);
    if (state.requestControllers.metrics !== controller) return;
    state.metrics = m;
    renderMetrics(m, logs);
    document.getElementById('metrics-lastupdate').textContent =
      '更新于 ' + new Date().toLocaleTimeString();
  } catch (e) {
    if (e.name === 'AbortError') return;
    setStatus(`metrics 获取失败: ${e.message}`, 'err');
  } finally {
    if (state.requestControllers.metrics === controller) {
      state.requestControllers.metrics = null;
    }
  }
}

function renderMetrics(m, logs) {
  const rate = m.cache.hit_rate;
  const rateEl = document.getElementById('m-cache-rate');
  rateEl.textContent = rate !== null && rate !== undefined ? (rate * 100).toFixed(1) + '%' : '—';
  rateEl.className = 'metric-value' + (
    rate === null || rate === undefined ? '' : rate > 0.6 ? ' ok' : rate > 0.3 ? ' warn' : ' err'
  );
  document.getElementById('m-cache-count').textContent = `hit ${m.cache.hit} / miss ${m.cache.miss}`;

  const api = m.api || {};
  const p95El = document.getElementById('m-api-p95');
  p95El.textContent = api.p95_ms ? api.p95_ms.toFixed(0) + 'ms' : '—';
  p95El.className = 'metric-value' + (
    api.p95_ms === undefined ? '' : api.p95_ms < 500 ? ' ok' : api.p95_ms < 2000 ? ' warn' : ' err'
  );
  document.getElementById('m-api-count').textContent = api.count ? `${api.count} calls · avg ${api.avg_ms}ms` : '无 API 调用';

  const loops = m.loops || {};
  const fpEl = document.getElementById('m-loop-firstpass');
  if (loops.first_pass_rate !== undefined) {
    fpEl.textContent = (loops.first_pass_rate * 100).toFixed(0) + '%';
    fpEl.className = 'metric-value ' + (loops.first_pass_rate > 0.7 ? 'ok' : 'warn');
  } else {
    fpEl.textContent = '—';
    fpEl.className = 'metric-value';
  }
  document.getElementById('m-loop-count').textContent =
    loops.count ? `${loops.count} 轮 · avg ${loops.avg_attempts}` : '尚无 judge loop';

  const dEl = document.getElementById('m-drift-count');
  dEl.textContent = m.baseline_drift_count ?? 0;
  dEl.className = 'metric-value ' + (m.baseline_drift_count > 0 ? 'warn' : 'ok');

  renderJudgesChart(m.judges || {});
  renderJudgeLog(logs);
}

function renderJudgesChart(judges) {
  const names = Object.keys(judges);
  if (!names.length) {
    clearChart('chart-judges');
    return;
  }
  const means = names.map(n => judges[n].mean);
  const colors = names.map(n => JUDGE_COLORS[n] || '#c9d1d9');
  upsertChart('chart-judges', {
    type: 'bar',
    data: {
      labels: names,
      datasets: [{ label: 'mean score', data: means, backgroundColor: colors, borderRadius: 2 }],
    },
    options: {
      scales: {
        x: { grid: { display: false }, ticks: { color: '#c9d1d9' } },
        y: { min: 1, max: 5, ticks: { color: '#8b949e', stepSize: 1 }, grid: { color: '#21262d' } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => {
          const j = judges[names[ctx.dataIndex]];
          return `${names[ctx.dataIndex]}: mean ${j.mean} · median ${j.median} · count ${j.count}`;
        } } },
      },
    },
  });
}

function renderJudgeLog(logs) {
  const el = document.getElementById('judge-log-container');
  if (!logs.length) {
    el.innerHTML = '<div class="empty-row muted">无 judge loop 历史</div>';
    return;
  }
  el.innerHTML = logs.map(r => {
    const indiv = (r.individual_scores || []).join('/');
    return `<div class="log-line">
      <span class="chip ${r.score >= 4 ? 'ok' : 'warn'}">${r.score}/5</span>
      <span class="mono">${escapeHTML(r.handle || '—')}</span>
      <span class="muted small">attempt=${r.attempt}</span>
      <span class="muted small mono">[${indiv}]</span>
      <span class="muted small" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHTML(r.reason || '')}</span>
    </div>`;
  }).join('');
}

function startAutoRefresh() {
  if (!document.getElementById('autoref-toggle').checked) return;
  stopAutoRefresh();
  state.autoRefreshTimer = setInterval(loadMetrics, 10000);
}

function stopAutoRefresh() {
  if (state.autoRefreshTimer) {
    clearInterval(state.autoRefreshTimer);
    state.autoRefreshTimer = null;
  }
}

// ========== INIT ==========

function init() {
  // Samples
  loadSamples();

  // Analyze form
  document.getElementById('analyze-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const handle = document.getElementById('handle-input').value.trim();
    const subs = Number(document.getElementById('submissions-input').value);
    if (handle) runAnalyze(handle, subs);
  });

  // Submission slider label
  const subInput = document.getElementById('submissions-input');
  const subLabel = document.getElementById('submissions-label');
  subInput.addEventListener('input', () => { subLabel.textContent = subInput.value; });

  // Narrate
  document.getElementById('narrate-btn').addEventListener('click', startNarrate);
  document.getElementById('problemset-btn').addEventListener('click', loadPracticePlan);

  // Baseline
  const thInput = document.getElementById('threshold-input');
  const thLabel = document.getElementById('threshold-label');
  thInput.addEventListener('input', () => { thLabel.textContent = thInput.value; });
  thInput.addEventListener('change', () => { if (state.handle) loadBaseline(); });
  document.getElementById('baseline-save-btn').addEventListener('click', saveBaseline);

  // Metrics controls
  const sinceInput = document.getElementById('since-input');
  const sinceLabel = document.getElementById('since-label');
  const updateSinceLabel = () => {
    sinceLabel.textContent = sinceInput.value === '0' ? 'all' : `${sinceInput.value}h`;
  };
  updateSinceLabel();
  sinceInput.addEventListener('input', updateSinceLabel);
  sinceInput.addEventListener('change', loadMetrics);
  document.getElementById('metrics-refresh-btn').addEventListener('click', loadMetrics);
  document.getElementById('autoref-toggle').addEventListener('change', () => {
    if (document.getElementById('autoref-toggle').checked) startAutoRefresh();
    else stopAutoRefresh();
  });

  // Router
  window.addEventListener('hashchange', onHashChange);
  window.addEventListener('beforeunload', () => {
    closeNarrateStream();
    stopAutoRefresh();
    abortRequest('analyze');
    abortRequest('baseline');
    abortRequest('metrics');
    abortRequest('recommendations');
  });
  onHashChange();
}

document.addEventListener('DOMContentLoaded', init);
