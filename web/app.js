'use strict';

const state = {
  session: null,
  compare: null,
  compareResult: null,
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
  el('workspace').classList.remove('hidden');
  renderAll();
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

// ---------- rendering ----------

function renderAll() {
  const s = state.session;
  el('session-badge').classList.remove('hidden');
  el('session-badge').textContent =
    `${s.scenario_name} · ${s.algorithm} · ${s.goal} · шаг ${s.observation.step}/${s.total_steps}`;
  el('step-indicator').textContent = `шаг ${s.observation.step} из ${s.total_steps}`;

  renderSummary(s.summary);
  renderSatellites(s.observation.state, s.observation.available, s.capacities_wh);
  renderSocChart();
  renderJobs(s.observation.jobs, s.observation.step);
  renderLog();
  renderCompare();
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
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
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
      const x = (p.step / total) * canvas.width;
      const y = canvas.height - (p.pct / 100) * canvas.height;
      idx === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  // reserve line at 30%
  ctx.strokeStyle = '#6d7aa0';
  ctx.setLineDash([4, 4]);
  const y30 = canvas.height - 0.30 * canvas.height;
  ctx.beginPath(); ctx.moveTo(0, y30); ctx.lineTo(canvas.width, y30); ctx.stroke();
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
el('job-filter').addEventListener('input', () => renderJobs(state.session.observation.jobs, state.session.observation.step));
el('log-filter').addEventListener('input', renderLog);

loadScenarios().catch(e => alert('Не удалось загрузить список сценариев: ' + e.message));
