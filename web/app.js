'use strict';

const state = {
  session: null,
  compare: null,
  compareResult: null,
  schedule: null, // last fetched Gantt schedule
  socHistory: {}, // sid -> [{step, pct}]
  log: [],        // accumulated executed rows, most recent last
};

const MAX_JOB_ROWS = 300;
const MAX_LOG_ROWS = 500;

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText || 'Request failed');
  return data;
}

function el(id) { return document.getElementById(id); }

// ---------- setup ----------

async function loadScenarios() {
  const scenarios = await api('/api/scenarios');
  const select = el('scenario-select');
  select.innerHTML = scenarios.map(s =>
    `<option value="${s.name}">${s.title} — ${s.satellites} апп., ${s.steps} шагов, ${s.jobs} зад.</option>`
  ).join('');
}

function collectOverrides() {
  const overrides = {};
  const socSat = el('ov-soc-sat').value.trim();
  const socVal = el('ov-soc-val').value;
  if (socSat && socVal !== '') overrides.initial_soc = { [socSat]: Number(socVal) };
  const solar = el('ov-solar').value;
  if (solar !== '') overrides.solar_multiplier = Number(solar);
  const prioJob = el('ov-prio-job').value.trim();
  const prioVal = el('ov-prio-val').value;
  if (prioJob && prioVal !== '') overrides.job_priority = { [prioJob]: Number(prioVal) };
  return Object.keys(overrides).length ? overrides : null;
}

async function createSession() {
  const body = {
    scenario: el('scenario-select').value,
    goal: el('goal-select').value,
    algorithm: el('algorithm-select').value,
    overrides: collectOverrides(),
  };
  const session = await api('/api/sessions', { method: 'POST', body: JSON.stringify(body) });
  state.session = session;
  state.compare = null;
  state.compareResult = null;
  state.socHistory = {};
  state.log = [];
  // Reveal the operator console and enable navigation now a shift exists.
  el('playbar').classList.remove('hidden');
  el('statusbar').classList.remove('hidden');
  el('btn-export').disabled = false;
  document.querySelectorAll('.tab[disabled]').forEach(t => (t.disabled = false));
  el('goal-switch-select').value = session.goal;
  switchTab('monitor');
  renderAll();
}

// ---------- tabs ----------

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t =>
    t.classList.toggle('tab-active', t.dataset.tab === name));
  document.querySelectorAll('.tabpane').forEach(p =>
    p.classList.toggle('tabpane-active', p.dataset.pane === name));
  // Canvases size to their visible width, so (re)draw when they become visible.
  if (name === 'monitor' && state.session) renderSocChart();
  if (name === 'schedule' && state.session) {
    el('sat-detail').innerHTML = '';
    loadSchedule().catch(e => alert(e.message));
  }
}

// ---------- advancing ----------

function recordSoc(rows, capacities) {
  for (const row of rows) {
    const cap = capacities[row.satellite_id];
    if (!cap) continue;
    const pct = (100 * row.energy_after_wh) / cap;
    (state.socHistory[row.satellite_id] ||= []).push({ step: row.step, pct });
  }
}

async function advance(payload) {
  if (!state.session) return;
  const view = await api(`/api/sessions/${state.session.id}/advance`, {
    method: 'POST', body: JSON.stringify(payload),
  });
  state.session = view;
  recordSoc(view.rows, view.capacities_wh);
  state.log.push(...view.rows);
  if (state.log.length > 5000) state.log = state.log.slice(-5000);
  renderAll();
}

// ---------- events ----------

function buildEvent() {
  const type = el('event-type').value;
  const sats = el('event-sats').value.split(',').map(s => s.trim()).filter(Boolean);
  const id = 'E-' + Date.now();
  const atStep = state.session.observation.step;
  if (type === 'add_jobs') {
    return {
      id, at_step: atStep, type,
      jobs: [{
        id: el('ej-id').value.trim(),
        kind: el('ej-kind').value,
        release_step: atStep,
        deadline_step: Number(el('ej-deadline').value),
        work_steps: Number(el('ej-work').value),
        eligible_satellites: sats,
        priority: Number(el('ej-priority').value),
        value_usd: Number(el('ej-value').value),
      }],
    };
  }
  return { id, at_step: atStep, type, satellite_ids: sats, end_step: Number(el('event-end').value) };
}

async function sendEvent() {
  el('event-error').textContent = '';
  try {
    const event = buildEvent();
    const view = await api(`/api/sessions/${state.session.id}/event`, {
      method: 'POST', body: JSON.stringify({ event }),
    });
    state.session = view;
    renderAll();
  } catch (err) {
    el('event-error').textContent = err.message;
  }
}

el('event-type').addEventListener('change', () => {
  el('event-job-row').classList.toggle('hidden', el('event-type').value !== 'add_jobs');
});

// ---------- goal switch / export ----------

async function switchGoal() {
  const view = await api(`/api/sessions/${state.session.id}/goal`, {
    method: 'POST', body: JSON.stringify({ goal: el('goal-switch-select').value }),
  });
  state.session = view;
  renderAll();
}

async function exportSession() {
  const result = await api(`/api/sessions/${state.session.id}/export`);
  const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${state.session.scenario_name}_${state.session.id}.json`;
  a.click();
}

// ---------- fork / compare ----------

async function forkSession() {
  const body = {
    goal: el('fork-goal').value,
    algorithm: el('fork-algorithm').value,
    label: `branch-${el('fork-goal').value}-${el('fork-algorithm').value}`,
  };
  state.compare = await api(`/api/sessions/${state.session.id}/fork`, {
    method: 'POST', body: JSON.stringify(body),
  });
  state.compareResult = null;
  renderCompare();
}

async function runCompareToEnd() {
  const total = state.session.total_steps;
  await advance({ until_step: total });
  const other = await api(`/api/sessions/${state.compare.id}/advance`, {
    method: 'POST', body: JSON.stringify({ until_step: total }),
  });
  state.compare = other;
  state.compareResult = await api(`/api/sessions/${state.session.id}/compare/${state.compare.id}`);
  renderAll();
}

// ---------- diagnostics ----------

const REASON_RU = {
  no_contact: 'нет сеанса связи',
  energy_reserve: 'резерв энергии',
  thermal_limit: 'тепловой предел',
  calibration_required: 'нужна калибровка',
  ground_capacity: 'лимит downlink (2)',
  satellite_unavailable: 'аппарат недоступен',
  outside_job_window: 'вне окна',
  ineligible_satellite: 'не допустимый исполнитель',
  duplicate_job_in_step: 'занято другим аппаратом',
};

async function loadDiagnostics() {
  const d = await api(`/api/sessions/${state.session.id}/diagnostics`);
  const blocked = Object.entries(d.blocked_reasons);
  const blockedHtml = blocked.length
    ? `<div class="scroll"><table><thead><tr><th>Причина отклонения</th><th>Раз</th></tr></thead>
       <tbody>${blocked.map(([r, n]) => `<tr><td>${REASON_RU[r] || r}</td><td>${n}</td></tr>`).join('')}</tbody></table></div>`
    : '<div class="hint">Планировщик не запрашивал заведомо невыполнимых действий (отклонённых команд нет).</div>';

  const sampleTable = (rows) => rows.length
    ? `<div class="scroll"><table><thead><tr><th>Задание</th><th>Приоритет</th><th>$</th><th>Сделано</th><th>Ограничения (из журнала)</th></tr></thead>
       <tbody>${rows.map(j => `<tr>
         <td>${j.id}</td><td>${j.priority}</td><td>${j.value_usd}</td>
         <td>${j.work_done}/${j.work_steps}</td>
         <td>${Object.keys(j.attempt_rejections).map(r => REASON_RU[r] || r).join(', ') || '—'}</td>
       </tr>`).join('')}</tbody></table></div>`
    : '<div class="hint">Нет.</div>';

  el('diagnostics-panel').innerHTML = `
    <div class="cards">
      <div class="card"><div class="value">${d.missed_total}</div><div class="label">Просрочено всего</div></div>
      <div class="card"><div class="value">${d.missed_no_contact_window_total}</div><div class="label">Ограничение задачи (не было сеанса)</div></div>
      <div class="card"><div class="value">${d.missed_had_opportunity_total}</div><div class="label">Была возможность (конкуренция/энергия/приоритет)</div></div>
      <div class="card"><div class="value">${d.idle_satellite_steps}</div><div class="label">Простой (апп×шаг)</div></div>
    </div>
    <h3>Отклонённые команды</h3>${blockedHtml}
    <h3>Ограничение задачи: не было сеанса связи (планировщик не мог помочь) — топ ${Math.min(d.missed_no_contact_window.length, 100)}</h3>
    ${sampleTable(d.missed_no_contact_window)}
    <h3>Была возможность — потеряно из-за конкуренции/энергии/приоритета — топ ${Math.min(d.missed_had_opportunity.length, 100)}</h3>
    <div class="hint">Пустое поле ограничений означает, что планировщик не запрашивал задание (выбрал другую работу по приоритету/ценности). «Сделано» больше нуля — задание было начато, но не завершено в срок; такой частичный прогресс выручки не приносит.</div>
    ${sampleTable(d.missed_had_opportunity)}`;
}

// ---------- Gantt timeline ----------

const GANTT = { gutter: 66, header: 16, rowH: 13 };
const ACTION = {
  0: { color: null, label: 'простой' },
  1: { color: '#4a7ad4', label: 'downlink' },
  2: { color: '#5aab6a', label: 'relay' },
  3: { color: '#e0a44a', label: 'калибровка' },
};

async function loadSchedule() {
  state.schedule = await api(`/api/sessions/${state.session.id}/schedule`);
  drawGantt();
}

function scheduleTabActive() {
  const p = document.querySelector('.tabpane[data-pane="schedule"]');
  return p && p.classList.contains('tabpane-active');
}

function drawGantt() {
  const data = state.schedule;
  if (!data) return;
  const canvas = el('gantt-canvas');
  const sats = data.satellites, n = data.total_steps, k = data.steps_executed;
  const cssW = canvas.clientWidth || 900;
  const cssH = GANTT.header + sats.length * GANTT.rowH;
  const dpr = window.devicePixelRatio || 1;
  canvas.style.height = cssH + 'px';
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  const plotW = cssW - GANTT.gutter;
  const xOf = (step) => GANTT.gutter + (step / n) * plotW;
  const cellW = Math.max(1, plotW / n);

  ctx.font = '10px ui-monospace, monospace';
  ctx.textBaseline = 'middle';

  // step axis ticks (every ~1/6 of the shift)
  ctx.fillStyle = '#767676';
  ctx.strokeStyle = '#2a2a2a';
  const tickEvery = Math.max(1, Math.round(n / 12));
  for (let s = 0; s <= n; s += tickEvery) {
    const x = xOf(s);
    ctx.fillText(String(s), x + 1, GANTT.header / 2);
    ctx.beginPath(); ctx.moveTo(x, GANTT.header); ctx.lineTo(x, cssH); ctx.stroke();
  }

  sats.forEach((sat, i) => {
    const y = GANTT.header + i * GANTT.rowH;
    // row base (zebra)
    ctx.fillStyle = i % 2 ? '#282828' : '#242424';
    ctx.fillRect(GANTT.gutter, y, plotW, GANTT.rowH);
    // contact windows (downlink OR relay available) — faint band
    ctx.fillStyle = 'rgba(90,140,210,.20)';
    for (let s = 0; s < n; s++) {
      if (sat.downlink_available[s] || sat.relay_available[s]) ctx.fillRect(xOf(s), y, cellW + 0.5, GANTT.rowH);
    }
    // outages
    ctx.fillStyle = 'rgba(150,60,60,.55)';
    sat.outage.forEach(([a, b]) => ctx.fillRect(xOf(a), y, xOf(b) - xOf(a), GANTT.rowH));
    // executed action cells
    for (let s = 0; s < k; s++) {
      const c = ACTION[sat.codes[s]].color;
      if (!c) continue;
      ctx.fillStyle = c;
      ctx.fillRect(xOf(s), y + 1, cellW + 0.5, GANTT.rowH - 2);
    }
    // completion ticks
    ctx.fillStyle = '#eafff0';
    for (let s = 0; s < k; s++) {
      if (sat.completed[s]) ctx.fillRect(xOf(s + 1) - 1.5, y + 1, 1.5, GANTT.rowH - 2);
    }
    // label
    ctx.fillStyle = '#c8c8c8';
    ctx.fillText(sat.id, 4, y + GANTT.rowH / 2);
  });

  // event markers
  data.events.forEach(ev => {
    const x = xOf(ev.at_step);
    ctx.strokeStyle = '#d15b9a'; ctx.setLineDash([3, 2]);
    ctx.beginPath(); ctx.moveTo(x, GANTT.header); ctx.lineTo(x, cssH); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#d15b9a'; ctx.fillRect(x - 2, 0, 4, GANTT.header - 2);
  });

  // current-step line
  const xk = xOf(k);
  ctx.strokeStyle = '#e0e0e0'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(xk, 0); ctx.lineTo(xk, cssH); ctx.stroke();
}

function ganttHit(e) {
  const data = state.schedule;
  const canvas = el('gantt-canvas');
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left, y = e.clientY - rect.top;
  const plotW = canvas.clientWidth - GANTT.gutter;
  if (x < GANTT.gutter || y < GANTT.header) return null;
  const step = Math.floor((x - GANTT.gutter) / plotW * data.total_steps);
  const idx = Math.floor((y - GANTT.header) / GANTT.rowH);
  if (idx < 0 || idx >= data.satellites.length || step < 0 || step >= data.total_steps) return null;
  return { sat: data.satellites[idx], step };
}

function onGanttHover(e) {
  const tip = el('gantt-tooltip');
  const hit = ganttHit(e);
  if (!hit) { tip.classList.add('hidden'); return; }
  const { sat, step } = hit;
  let what;
  if (step >= state.schedule.steps_executed) what = 'ещё не рассчитано';
  else {
    what = ACTION[sat.codes[step]].label;
    if (sat.jobs[step]) what += ` · ${sat.jobs[step]}`;
    if (sat.completed[step]) what += ' · завершено ✓';
  }
  const contact = (sat.downlink_available[step] || sat.relay_available[step]) ? ' · есть связь' : '';
  tip.textContent = `${sat.id} · шаг ${step} · ${what}${contact}`;
  tip.classList.remove('hidden');
  const wrap = el('gantt-canvas').parentElement.getBoundingClientRect();
  tip.style.left = (e.clientX - wrap.left + 12) + 'px';
  tip.style.top = (e.clientY - wrap.top + 12) + 'px';
}

function onGanttClick(e) {
  const hit = ganttHit(e);
  if (!hit) return;
  const sat = hit.sat;
  const st = state.session.observation.state[sat.id];
  const cap = state.session.capacities_wh[sat.id];
  const jobs = Object.values(state.session.observation.jobs)
    .filter(j => j.eligible_satellites.includes(sat.id));
  const jobStatusOf = (j) => j.completed_step !== null ? 'done'
    : (j.deadline_step <= state.session.observation.step ? 'missed' : 'active');
  el('sat-detail').innerHTML = `
    <h3>Аппарат ${sat.id}</h3>
    <div class="cards">
      <div class="card"><div class="value">${st ? (100 * st.energy_wh / cap).toFixed(1) : '—'}%</div><div class="label">заряд</div></div>
      <div class="card"><div class="value">${st ? st.temp_c.toFixed(1) : '—'}°</div><div class="label">температура</div></div>
      <div class="card"><div class="value">${st ? st.calibration_age_steps : '—'}</div><div class="label">возраст калибровки</div></div>
      <div class="card"><div class="value">${sat.counts.downlink}</div><div class="label">шагов downlink</div></div>
      <div class="card"><div class="value">${sat.counts.relay}</div><div class="label">шагов relay</div></div>
      <div class="card"><div class="value">${sat.counts.calibrate}</div><div class="label">калибровок</div></div>
      <div class="card"><div class="value">${sat.counts.idle}</div><div class="label">простой</div></div>
    </div>
    <h3>Задания с этим аппаратом (${jobs.length})</h3>
    <div class="scroll scroll-sm"><table><thead><tr><th>ID</th><th>Вид</th><th>Приоритет</th><th>$</th><th>Окно</th><th>Осталось</th><th>Статус</th></tr></thead>
    <tbody>${jobs.slice(0, 200).map(j => { const s = jobStatusOf(j); return `<tr>
      <td>${j.id}</td><td>${j.kind}</td><td>${j.priority}</td><td>${j.value_usd}</td>
      <td>${j.release_step}–${j.deadline_step}</td><td>${j.remaining_steps}</td>
      <td class="status-${s}">${s}</td></tr>`; }).join('') || '<tr><td colspan="7">нет</td></tr>'}</tbody></table></div>`;
}

// ---------- rendering ----------

function renderAll() {
  const s = state.session;
  const step = s.observation.step, total = s.total_steps;
  el('session-badge').textContent = `${s.scenario_name} · ${s.algorithm} · ${s.goal}`;

  // playback scrubber
  el('scrubber-fill').style.width = `${(step / total) * 100}%`;
  el('step-indicator').textContent = `шаг ${step} / ${total}`;

  // status bar
  const sm = s.summary;
  el('st-state').textContent = step >= total ? 'смена завершена' : 'смена идёт';
  el('st-done').textContent = sm.jobs_completed;
  el('st-missed').textContent = sm.jobs_due_missed;
  el('st-crit').textContent = `${sm.critical_jobs_completed_on_time}/${sm.critical_jobs_due}`;
  el('st-rev').textContent = `$${sm.revenue_usd.toFixed(2)}`;
  el('st-soc').textContent = `${sm.minimum_soc_pct.toFixed(1)}%`;

  renderSummary(sm);
  renderSatellites(s.observation.state, s.observation.available, s.capacities_wh);
  renderSocChart();
  renderJobs(s.observation.jobs, s.observation.step);
  renderLog();
  renderCompare();
  if (scheduleTabActive()) loadSchedule().catch(() => {});
}

function renderSummary(summary) {
  const cards = [
    ['Выполнено шагов', summary.steps_executed],
    ['Заданий всего', summary.jobs_total],
    ['Выполнено', summary.jobs_completed],
    ['Просрочено', summary.jobs_due_missed],
    ['Приоритет-3 в срок', `${summary.critical_jobs_completed_on_time}/${summary.critical_jobs_due}`],
    ['Выручка, $', summary.revenue_usd.toFixed(2)],
    ['Отклонённых команд', summary.blocked_command_count],
    ['Ниже резерва (апп×шаг)', summary.below_reserve_satellite_steps],
    ['Провалы по энергии', summary.brownout_satellite_steps],
    ['Мин. заряд, %', summary.minimum_soc_pct.toFixed(1)],
  ];
  el('summary-cards').innerHTML = cards.map(([label, value]) =>
    `<div class="card"><div class="value">${value}</div><div class="label">${label}</div></div>`
  ).join('');
}

function renderSatellites(stateBySat, available, capacities) {
  const head = '<tr><th>ID</th><th>Заряд, %</th><th>Температура, °C</th><th>Калибровка (возраст)</th><th>Доступен</th></tr>';
  const rows = Object.entries(stateBySat).sort(([a], [b]) => a.localeCompare(b)).map(([sid, st]) => {
    const cap = capacities[sid] || 1;
    const pct = (100 * st.energy_wh / cap).toFixed(1);
    return `<tr>
      <td>${sid}</td><td>${pct}</td><td>${st.temp_c.toFixed(1)}</td>
      <td>${st.calibration_age_steps}</td>
      <td>${available[sid] ? 'да' : 'нет'}</td>
    </tr>`;
  }).join('');
  el('sat-table').querySelector('thead').innerHTML = head;
  el('sat-table').querySelector('tbody').innerHTML = rows;
}

function renderSocChart() {
  const canvas = el('soc-chart');
  // Match the drawing buffer to the rendered width (device-pixel-aware) so
  // the plot fills the panel and stays crisp instead of scaling a 900px bitmap.
  const cssWidth = canvas.clientWidth || 900;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(cssWidth * dpr)) {
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(150 * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = cssWidth, H = 150;
  ctx.clearRect(0, 0, W, H);
  const sats = Object.keys(state.socHistory);
  if (!sats.length) return;
  const total = state.session.total_steps;
  const colors = ['#67e29b', '#4a6bee', '#f2c94c', '#ff6b6b', '#c084fc', '#38bdf8', '#fb923c', '#a3e635'];
  sats.forEach((sid, i) => {
    const points = state.socHistory[sid];
    ctx.strokeStyle = colors[i % colors.length];
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    points.forEach((p, idx) => {
      const x = (p.step / total) * W;
      const y = H - (p.pct / 100) * H;
      idx === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  // reserve line at 30%
  ctx.strokeStyle = '#6d7aa0';
  ctx.setLineDash([4, 4]);
  const y30 = H - 0.30 * H;
  ctx.beginPath(); ctx.moveTo(0, y30); ctx.lineTo(W, y30); ctx.stroke();
  ctx.setLineDash([]);
}

function jobStatus(job, currentStep) {
  if (job.completed_step !== null) return 'done';
  if (job.deadline_step <= currentStep) return 'missed';
  return 'active';
}

function renderJobs(jobs, currentStep) {
  const filter = el('job-filter').value.trim().toLowerCase();
  const entries = Object.values(jobs).map(j => ({ ...j, status: jobStatus(j, currentStep) }));
  const filtered = filter
    ? entries.filter(j => j.id.toLowerCase().includes(filter) || j.status.includes(filter))
    : entries;
  const shown = filtered.slice(0, MAX_JOB_ROWS);
  el('jobs-hint').textContent =
    `показано ${shown.length} из ${filtered.length} (всего заданий: ${entries.length})`;
  el('jobs-table').querySelector('thead').innerHTML =
    '<tr><th>ID</th><th>Вид</th><th>Приоритет</th><th>$</th><th>Окно</th><th>Осталось шагов</th><th>Статус</th></tr>';
  el('jobs-table').querySelector('tbody').innerHTML = shown.map(j => `<tr data-job-id="${j.id}" class="job-row">
    <td>${j.id}</td><td>${j.kind}</td><td>${j.priority}</td><td>${j.value_usd}</td>
    <td>${j.release_step}–${j.deadline_step}</td><td>${j.remaining_steps}</td>
    <td class="status-${j.status}">${j.status}</td>
  </tr>`).join('');
  el('jobs-table').querySelectorAll('.job-row').forEach(tr =>
    tr.addEventListener('click', () => explainJob(tr.dataset.jobId).catch(e => alert(e.message))));
}

const REASON_LABELS = {
  idle: 'ожидание (не назначено)',
  accepted: 'выполнено',
  satellite_unavailable: 'аппарат недоступен (отказ/недоступность)',
  calibration_required: 'требуется калибровка',
  no_contact: 'нет доступного сеанса связи',
  outside_job_window: 'вне окна задания',
  ineligible_satellite: 'аппарат не входит в допустимые исполнители',
  already_completed: 'задание уже завершено',
  energy_reserve: 'заблокировано резервом энергии',
  thermal_limit: 'вне теплового диапазона',
  ground_capacity: 'превышен лимit одновременных downlink',
  duplicate_job_in_step: 'над заданием уже работает другой аппарат на этом шаге',
  unknown_job: 'неизвестное задание',
  unknown_action: 'неизвестное действие',
};

async function explainJob(jobId) {
  const data = await api(`/api/sessions/${state.session.id}/jobs/${jobId}/explain`);
  const rowsByStep = {};
  for (const row of data.timeline) (rowsByStep[row.step] ||= []).push(row);
  const steps = Object.keys(rowsByStep).map(Number).sort((a, b) => a - b);
  const body = steps.map(step => rowsByStep[step].map(row => {
    const isThisJob = row.requested.job_id === jobId;
    const label = REASON_LABELS[row.reason] || row.reason;
    const tag = isThisJob ? (row.executed === 'job' ? 'работал над этим заданием' : `попытка отклонена: ${label}`)
      : (row.requested.action === 'idle' ? 'ничего не делал' : `занят другим (${row.requested.action}${row.requested.job_id ? ':' + row.requested.job_id : ''})`);
    return `<tr><td>${step}</td><td>${row.satellite_id}</td><td>${tag}</td></tr>`;
  }).join('')).join('');
  el('job-explain').innerHTML = `
    <h3>Разбор задания ${jobId} — статус: <span class="status-${data.status}">${data.status}</span></h3>
    <div class="hint">Допустимые исполнители: ${data.eligible_satellites.join(', ')}. Окно: ${data.job.release_step}–${data.job.deadline_step}, нужно шагов работы: ${data.job.work_steps}, осталось: ${data.job.remaining_steps}.</div>
    <div class="scroll"><table><thead><tr><th>Шаг</th><th>Аппарат</th><th>Что происходило</th></tr></thead>
    <tbody>${body || '<tr><td colspan="3">Окно задания ещё не наступило или не пройдено.</td></tr>'}</tbody></table></div>`;
}

function renderLog() {
  const filter = el('log-filter').value.trim().toLowerCase();
  const rows = filter
    ? state.log.filter(r => r.reason.includes(filter) || r.satellite_id.toLowerCase().includes(filter))
    : state.log;
  const shown = rows.slice(-MAX_LOG_ROWS).reverse();
  el('log-table').querySelector('thead').innerHTML =
    '<tr><th>Шаг</th><th>Аппарат</th><th>Запрошено</th><th>Выполнено</th><th>Причина</th></tr>';
  el('log-table').querySelector('tbody').innerHTML = shown.map(r => {
    const cls = r.reason === 'accepted' || r.reason === 'idle' ? 'reason-accepted' : 'reason-blocked';
    const requested = r.requested.action + (r.requested.job_id ? `:${r.requested.job_id}` : '');
    return `<tr><td>${r.step}</td><td>${r.satellite_id}</td><td>${requested}</td>
      <td>${r.executed}</td><td class="${cls}">${r.reason}</td></tr>`;
  }).join('');
}

function renderCompare() {
  const panel = el('compare-panel');
  if (!state.compare) { panel.innerHTML = '<div class="hint">Ветвь сравнения ещё не создана.</div>'; return; }
  const a = state.session, b = state.compare;
  let html = `<div class="row">
    <div class="card"><div class="label">Ветвь A</div><div class="value">${a.label || a.algorithm}/${a.goal}</div></div>
    <div class="card"><div class="label">Ветвь B</div><div class="value">${b.label || b.algorithm}/${b.goal}</div></div>
    <button id="btn-run-compare">Досчитать обе ветви и сравнить</button>
  </div>`;
  if (state.compareResult) {
    const d = state.compareResult.diff;
    html += `<div class="cards">
      <div class="card"><div class="value">${d.jobs_completed >= 0 ? '+' : ''}${d.jobs_completed}</div><div class="label">Δ выполнено (A−B)</div></div>
      <div class="card"><div class="value">${d.critical_jobs_completed_on_time >= 0 ? '+' : ''}${d.critical_jobs_completed_on_time}</div><div class="label">Δ приоритет-3</div></div>
      <div class="card"><div class="value">${d.revenue_usd >= 0 ? '+' : ''}$${d.revenue_usd.toFixed(2)}</div><div class="label">Δ выручка</div></div>
      <div class="card"><div class="value">${d.jobs_due_missed >= 0 ? '+' : ''}${d.jobs_due_missed}</div><div class="label">Δ просрочено</div></div>
    </div>
    <div class="hint">${state.compareResult.verdict}</div>`;
  }
  panel.innerHTML = html;
  const btn = document.getElementById('btn-run-compare');
  if (btn) btn.addEventListener('click', () => runCompareToEnd().catch(e => alert(e.message)));
}

// ---------- wiring ----------

el('btn-create').addEventListener('click', () => createSession().catch(e => alert(e.message)));
el('btn-step').addEventListener('click', () => advance({ steps: 1 }).catch(e => alert(e.message)));
el('btn-advance-n').addEventListener('click', () =>
  advance({ steps: Number(el('advance-n').value) }).catch(e => alert(e.message)));
el('btn-advance-until').addEventListener('click', () =>
  advance({ until_step: Number(el('advance-until').value) }).catch(e => alert(e.message)));
el('btn-advance-all').addEventListener('click', () =>
  advance({ until_step: state.session.total_steps }).catch(e => alert(e.message)));
el('btn-switch-goal').addEventListener('click', () => switchGoal().catch(e => alert(e.message)));
el('btn-export').addEventListener('click', () => exportSession().catch(e => alert(e.message)));
el('btn-send-event').addEventListener('click', () => sendEvent());
el('btn-fork').addEventListener('click', () => forkSession().catch(e => alert(e.message)));
el('btn-diagnostics').addEventListener('click', () => loadDiagnostics().catch(e => alert(e.message)));
el('job-filter').addEventListener('input', () => renderJobs(state.session.observation.jobs, state.session.observation.step));
el('log-filter').addEventListener('input', renderLog);

document.querySelectorAll('.tab').forEach(tab =>
  tab.addEventListener('click', () => { if (!tab.disabled) switchTab(tab.dataset.tab); }));

el('gantt-canvas').addEventListener('mousemove', onGanttHover);
el('gantt-canvas').addEventListener('mouseleave', () => el('gantt-tooltip').classList.add('hidden'));
el('gantt-canvas').addEventListener('click', onGanttClick);

// Click the timeline scrubber to run the shift up to that step.
el('scrubber').addEventListener('click', (e) => {
  if (!state.session) return;
  const rect = el('scrubber').getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  const target = Math.round(frac * state.session.total_steps);
  if (target > state.session.observation.step) advance({ until_step: target }).catch(err => alert(err.message));
});

loadScenarios().catch(e => alert('Не удалось загрузить список сценариев: ' + e.message));
