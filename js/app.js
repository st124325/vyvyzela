/* Слой UI: форма конфигурации, карта, временная шкала, диаграмма доступности, сравнение вариантов. */
(function () {
  const Geometry = window.Geometry;
  const Routing = window.Routing;
  const Simulation = window.Simulation;
  const Validate = window.Validate;
  const { DEFAULT_CONFIG, PLANE_COLORS, generateSatellites, buildStep, simulateVariant, findVulnerableSatellites, autoTuneConfig, configToScenario } = Simulation;
  const { computeRoute, REASON_TEXT } = Routing;
  const { validateScenario } = Validate;
  const d2r = Geometry.d2r;

  /* ======================= СОСТОЯНИЕ ПРИЛОЖЕНИЯ ======================= */
  let draft = DEFAULT_CONFIG();
  let variants = [];
  let variantSeq = 1;
  let activeVariantId = null;
  let currentStep = 0;
  let selectedGs = null;
  let playing = false;
  let rafId = null;
  let lastTs = null;
  let simSeconds = 0;
  let mapMode = 'map'; // 'map' — континенты, 'grid' — схема с сеткой
  const PLAY_DURATION_S = 45; // за столько секунд проигрываются полные расчётные сутки

  /* ======================= ПРЕСЕТЫ ======================= */
  window.applyPreset = function applyPreset(key) {
    const base = DEFAULT_CONFIG();
    if (key === 'full') { draft = { ...base }; }
    if (key === 'phase1') { draft = { ...base, activePlanes: [1] }; }
    if (key === 'degraded') {
      const sats = generateSatellites({ ...base, activePlanes: [1, 2, 3] });
      const ids = [sats[0], sats[5], sats[10], sats[16], sats[21], sats[26], sats[32], sats[37], sats[42], sats[47]].map(s => s.id);
      draft = { ...base, outages: ids.map(id => ({ type: 'satellite', id, startHour: 0, endHour: 24 })) };
    }
    if (key === 'isl2000') { draft = { ...base, islMaxRangeKm: 2000 }; }
    if (key === 'recommended') {
      // Найдено автоподбором (см. кнопку «Автоподбор конфигурации» и js/simulation.js:autoTuneConfig):
      // систематический дефицит доступности вызван контактом со шлюзом (Москва, 55.75° с.ш.), а не
      // дальностью МСС или базовым фазированием — см. README, раздел «Как подобран рекомендованный вариант».
      draft = { ...base, raanOffset: [-20, -20, -20], phaseOffset: [7.5, 7.5, 7.5], islMaxRangeKm: 3000, minElevationDeg: 5 };
    }
    clearMsgs();
    renderConfigForm();
  };

  window.resetDraft = function resetDraft() {
    draft = DEFAULT_CONFIG();
    clearMsgs();
    document.getElementById('fileInput').value = '';
    renderConfigForm();
  };

  function clearMsgs() {
    document.getElementById('customScenarioNote').innerHTML = '';
    document.getElementById('fileMsg').innerHTML = '';
  }

  /* ======================= ЗАГРУЗКА ФАЙЛА СЦЕНАРИЯ ======================= */
  window.handleFileUpload = function handleFileUpload(evt) {
    const file = evt.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onerror = () => {
      document.getElementById('fileMsg').innerHTML = `<div class="msg err">Не удалось прочитать файл. Проверьте, что он не повреждён, и попробуйте снова.</div>`;
    };
    reader.onload = e => {
      let raw;
      try { raw = JSON.parse(e.target.result); }
      catch (err) {
        document.getElementById('fileMsg').innerHTML = `<div class="msg err">Не удалось прочитать JSON: ${err.message}</div>`;
        return;
      }
      // Принимаем как файл-сценарий, так и наш экспорт результатов ({name, config, metrics}) —
      // это замыкает цикл «выгрузить вариант → загрузить обратно» из ТЗ.
      const obj = (raw && raw.config && raw.config.groundStations && !raw.ground_stations)
        ? configToScenario(raw.config, raw.name)
        : raw;
      const errors = validateScenario(obj);
      if (errors.length) {
        document.getElementById('fileMsg').innerHTML =
          `<div class="msg err"><b>Файл содержит ошибки, исправьте и загрузите снова:</b><ul style="margin:6px 0 0 16px;padding:0">${errors.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul></div>`;
        return;
      }
      draft = {
        ...DEFAULT_CONFIG(),
        groundStations: obj.ground_stations.map(g => ({ name: g.name, lat: g.lat, lon: g.lon, gateway: !!g.gateway })),
        customPlanes: obj.planes.map((p, i) => ({ id: (p.id != null ? p.id : i + 1), raan: p.raan, inclination: p.inclination, satellites: p.satellites, phase_offset: p.phase_offset || 0 })),
        islMaxRangeKm: obj.isl_max_range_km ?? 3000,
        minElevationDeg: obj.min_elevation_deg ?? 10,
        durationHours: Math.min(72, obj.duration_hours ?? 24),
        stepSeconds: Math.max(30, obj.step_seconds ?? 120),
        outages: (obj.outages || []).map(o => ({ type: o.type, id: o.id, startHour: o.start_hour, endHour: o.end_hour })),
        activePlanes: [1, 2, 3]
      };
      document.getElementById('fileMsg').innerHTML = `<div class="msg ok">Сценарий «${escapeHtml(obj.name || file.name)}» загружен и прошёл проверку.</div>`;
      document.getElementById('customScenarioNote').innerHTML =
        `<div class="hint">Загружен пользовательский сценарий: орбитальные плоскости и наземные пункты заданы файлом, слайдеры очереди/фазирования ниже отключены.</div>`;
      renderConfigForm();
    };
    reader.readAsText(file);
  };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  /* ======================= ФОРМА КОНФИГУРАЦИИ ======================= */
  function renderConfigForm() {
    const custom = !!draft.customPlanes;

    const qWrap = document.getElementById('queueRadios');
    const queues = [
      { v: '1', label: 'Только 1-я очередь (16 КА)', planes: [1] },
      { v: '12', label: '1-я и 2-я очередь (32 КА)', planes: [1, 2] },
      { v: '123', label: 'Все три очереди (48 КА)', planes: [1, 2, 3] }
    ];
    qWrap.innerHTML = queues.map(q => {
      const checked = !custom && JSON.stringify(draft.activePlanes) === JSON.stringify(q.planes) ? 'checked' : '';
      return `<label><input type="radio" name="queue" value="${q.v}" ${checked} ${custom ? 'disabled' : ''} onchange="setQueue('${q.v}')"> ${q.label}</label>`;
    }).join('');

    const planeWrap = document.getElementById('planeSliders');
    if (custom) {
      planeWrap.innerHTML = `<div class="hint">Недоступно: параметры плоскостей заданы загруженным сценарием.</div>`;
    } else {
      let html = '';
      for (let i = 0; i < 3; i++) {
        html += `
          <label>Плоскость ${i + 1} — азимут (RAAN) смещение <span class="sliderVal">${draft.raanOffset[i]}°</span></label>
          <input type="range" min="-60" max="60" step="1" value="${draft.raanOffset[i]}" oninput="setRaanOffset(${i},this.value)">
          <label>Плоскость ${i + 1} — фазирование <span class="sliderVal">${draft.phaseOffset[i]}°</span></label>
          <input type="range" min="0" max="22.5" step="0.5" value="${draft.phaseOffset[i]}" oninput="setPhaseOffset(${i},this.value)">
        `;
      }
      planeWrap.innerHTML = html;
    }

    document.getElementById('islRange').value = draft.islMaxRangeKm;
    document.getElementById('islVal').textContent = draft.islMaxRangeKm + ' км';
    document.getElementById('elevRange').value = draft.minElevationDeg;
    document.getElementById('elevVal').textContent = draft.minElevationDeg + '°';
    document.getElementById('durationInput').value = draft.durationHours;
    document.getElementById('stepInput').value = draft.stepSeconds;

    const satSel = document.getElementById('outageSatSelect');
    const sats = generateSatellites(draft);
    satSel.innerHTML = sats.map(s => `<option value="${s.id}">${s.id} (плоскость ${s.plane})</option>`).join('');

    renderOutageList();
  }
  window.setQueue = function setQueue(v) {
    const map = { '1': [1], '12': [1, 2], '123': [1, 2, 3] };
    draft.activePlanes = map[v];
    renderConfigForm();
  };
  window.setRaanOffset = function setRaanOffset(i, v) { draft.raanOffset[i] = Number(v); renderConfigForm(); };
  window.setPhaseOffset = function setPhaseOffset(i, v) { draft.phaseOffset[i] = Number(v); renderConfigForm(); };
  window.onIslChange = function onIslChange(v) { draft.islMaxRangeKm = Number(v); document.getElementById('islVal').textContent = v + ' км'; };
  window.onElevChange = function onElevChange(v) { draft.minElevationDeg = Number(v); document.getElementById('elevVal').textContent = v + '°'; };
  window.onDurationChange = function onDurationChange(v) { const n = Number(v); if (Number.isFinite(n) && n > 0) draft.durationHours = Math.min(72, n); };
  window.onStepChange = function onStepChange(v) { const n = Number(v); if (Number.isFinite(n) && n >= 30) draft.stepSeconds = n; };

  window.addSatOutage = function addSatOutage() {
    const satId = document.getElementById('outageSatSelect').value;
    const startHour = Number(document.getElementById('outStart').value);
    const endHour = Number(document.getElementById('outEnd').value);
    if (!satId || !Number.isFinite(startHour) || !Number.isFinite(endHour) || endHour <= startHour) return;
    draft.outages.push({ type: 'satellite', id: satId, startHour, endHour });
    renderOutageList();
  };
  window.toggleGwOutage = function toggleGwOutage() {
    const on = document.getElementById('gwOutageChk').checked;
    document.getElementById('gwOutageRow').style.display = on ? 'flex' : 'none';
    draft.outages = draft.outages.filter(o => o.type !== 'gateway');
    if (on) {
      const startHour = Number(document.getElementById('gwStart').value);
      const endHour = Number(document.getElementById('gwEnd').value);
      draft.outages.push({ type: 'gateway', startHour, endHour });
    }
    renderOutageList();
  };
  window.removeOutage = function removeOutage(idx) { draft.outages.splice(idx, 1); renderOutageList(); };
  function renderOutageList() {
    const el = document.getElementById('outageList');
    if (draft.outages.length === 0) { el.innerHTML = '<div class="hint">Отказов не задано.</div>'; return; }
    el.innerHTML = draft.outages.map((o, i) => {
      const label = o.type === 'gateway' ? `Шлюз недоступен ${o.startHour}–${o.endHour} ч` : `${o.id} недоступен ${o.startHour}–${o.endHour} ч`;
      return `<div class="outageRow"><span>${escapeHtml(label)}</span><button class="small danger" onclick="removeOutage(${i})">✕</button></div>`;
    }).join('');
  }

  /* ======================= РАСЧЁТ И ВАРИАНТЫ ======================= */
  window.runCalculation = function runCalculation() {
    const calcMsgEl = document.getElementById('calcMsg');
    calcMsgEl.innerHTML = '';
    let metrics, cfgSnapshot;
    try {
      cfgSnapshot = JSON.parse(JSON.stringify(draft));
      metrics = simulateVariant(cfgSnapshot);
    } catch (err) {
      calcMsgEl.innerHTML = `<div class="msg err">Не удалось выполнить расчёт: ${escapeHtml(err.message)}. Проверьте конфигурацию (число спутников в плоскостях, длительность и шаг расчёта).</div>`;
      return;
    }
    const name = document.getElementById('variantName').value.trim() || `Вариант ${variantSeq}`;
    const variant = { id: 'v' + (variantSeq++), name, config: cfgSnapshot, metrics, createdAt: new Date() };
    variants.push(variant);
    activeVariantId = variant.id;
    currentStep = 0;
    selectedGs = cfgSnapshot.groundStations.find(g => !g.gateway).name;
    document.getElementById('variantName').value = '';
    renderVariants();
    renderGsSelect();
    update();
    renderGantt();
  };

  function renderVariants() {
    const el = document.getElementById('variantsList');
    if (variants.length === 0) { el.innerHTML = '<div class="placeholder">Пока нет рассчитанных вариантов</div>'; return; }
    el.innerHTML = variants.map(v => {
      const active = v.id === activeVariantId ? 'active' : '';
      const badge = v.metrics.meetsTarget ? '<span class="badge good">цель 90% достигнута</span>' : '<span class="badge bad">ниже 90% для части пунктов</span>';
      return `
      <div class="variantRow ${active}">
        <div class="top">
          <div class="name">${escapeHtml(v.name)}</div>
          <label style="margin:0;display:flex;align-items:center;gap:4px;font-size:11px;color:var(--muted)">
            <input type="checkbox" data-compare="${v.id}" style="width:auto" onchange="onCompareToggle()"> сравнить
          </label>
        </div>
        <div class="stat">Средняя доступность: <b>${v.metrics.overallAvailability.toFixed(1)}%</b> · ${badge}</div>
        <div class="btns">
          <button class="small" onclick="showVariant('${v.id}')">Показать на карте</button>
          <button class="small" onclick="exportScenario('${v.id}')" title="Формат, который можно загрузить обратно">Сценарий .json</button>
          <button class="small" onclick="exportVariant('${v.id}')" title="Конфигурация + рассчитанные метрики">Результаты .json</button>
          <button class="small danger" onclick="deleteVariant('${v.id}')">Удалить</button>
        </div>
      </div>`;
    }).join('');
  }
  window.showVariant = function showVariant(id) {
    activeVariantId = id;
    const v = variants.find(x => x.id === id);
    currentStep = 0;
    if (!v.config.groundStations.some(g => g.name === selectedGs && !g.gateway)) {
      selectedGs = v.config.groundStations.find(g => !g.gateway).name;
    }
    renderVariants(); renderGsSelect(); renderGantt(); update();
  };
  window.deleteVariant = function deleteVariant(id) {
    variants = variants.filter(v => v.id !== id);
    if (activeVariantId === id) { activeVariantId = variants.length ? variants[0].id : null; currentStep = 0; }
    renderVariants(); renderGsSelect(); renderGantt(); update(); renderCompare();
  };
  function downloadJson(obj, filename) {
    const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  // Экспорт результатов: конфигурация во внутреннем виде + рассчитанные метрики (для отчёта).
  window.exportVariant = function exportVariant(id) {
    const v = variants.find(x => x.id === id);
    downloadJson({ name: v.name, config: v.config, metrics: v.metrics }, `${v.name.replace(/\s+/g, '_')}_результаты.json`);
  };
  // Экспорт сценария: формат файла-сценария, который загружается обратно через форму.
  window.exportScenario = function exportScenario(id) {
    const v = variants.find(x => x.id === id);
    downloadJson(configToScenario(v.config, v.name), `${v.name.replace(/\s+/g, '_')}_сценарий.json`);
  };
  // Выгрузка текущей (ещё не рассчитанной) конфигурации — для сценария «изменил → выгрузил → загрузил».
  window.exportDraftScenario = function exportDraftScenario() {
    const nm = (document.getElementById('variantName').value.trim()) || 'Текущий сценарий';
    downloadJson(configToScenario(draft, nm), `${nm.replace(/\s+/g, '_')}_сценарий.json`);
  };
  window.onCompareToggle = function onCompareToggle() { renderCompare(); };

  /* ======================= ДОП. ФУНКЦИЯ: НАИБОЛЕЕ УЯЗВИМЫЕ СПУТНИКИ ======================= */
  window.runVulnerabilityScan = function runVulnerabilityScan() {
    const el = document.getElementById('vulnResults');
    el.innerHTML = '<div class="hint">Расчёт... (перебор отказов по каждому спутнику)</div>';
    setTimeout(() => {
      let result;
      try {
        result = findVulnerableSatellites(draft, { topN: 8 });
      } catch (err) {
        el.innerHTML = `<div class="msg err">Не удалось выполнить анализ: ${escapeHtml(err.message)}</div>`;
        return;
      }
      const rows = result.ranked.map(r => `
        <div class="vulnRow"><span>${r.satId} (плоскость ${r.plane})</span><span>-${r.availabilityDrop.toFixed(2)} п.п. → ${r.overallAvailability.toFixed(1)}%</span></div>
      `).join('');
      el.innerHTML = `<div class="hint" style="margin-bottom:6px">Базовая средняя доступность: ${result.baselineAvailability.toFixed(1)}%. Наибольшее падение при отказе одного спутника:</div>${rows}`;
    }, 10);
  };

  /* ======================= ДОП. ФУНКЦИЯ: АВТОПОДБОР КОНФИГУРАЦИИ ======================= */
  window.runAutoTune = function runAutoTune() {
    const el = document.getElementById('autoTuneResults');
    el.innerHTML = '<div class="hint">Подбор параметров... (грубый перебор RAAN/фазирования/МСС/угла места)</div>';
    setTimeout(() => {
      let best;
      try {
        best = autoTuneConfig(draft);
      } catch (err) {
        el.innerHTML = `<div class="msg err">Не удалось выполнить подбор: ${escapeHtml(err.message)}</div>`;
        return;
      }
      if (!best) { el.innerHTML = '<div class="hint">Не удалось найти конфигурацию (нет спутников при текущих ограничениях).</div>'; return; }
      el.innerHTML = `
        <div class="hint" style="margin-bottom:6px">
          Лучший найденный вариант: минимальная доступность по пунктам ≈ ${best.minAvail.toFixed(1)}%, средняя ≈ ${best.overallAvailability.toFixed(1)}%.<br>
          RAAN-смещение: ${best.raanOffset[0]}°, фазирование: ${best.phaseOffset[0]}°, МСС: ${best.islMaxRangeKm} км, угол места: ${best.minElevationDeg}°.
        </div>
        <button class="small primary" onclick="applyAutoTuneResult(${best.raanOffset[0]},${best.phaseOffset[0]},${best.islMaxRangeKm},${best.minElevationDeg})">Применить к текущему варианту</button>
      `;
    }, 10);
  };
  window.applyAutoTuneResult = function applyAutoTuneResult(raan, phase, isl, elev) {
    draft.customPlanes = null;
    draft.raanOffset = [raan, raan, raan];
    draft.phaseOffset = [phase, phase, phase];
    draft.islMaxRangeKm = isl;
    draft.minElevationDeg = elev;
    clearMsgs();
    renderConfigForm();
  };

  /* ======================= ОТРИСОВКА КАРТЫ ======================= */
  function activeVariant() { return variants.find(v => v.id === activeVariantId) || null; }

  function projectPolar(lat, lon, cx, cy, Rmax) {
    const rho = (90 - lat) / 180 * Rmax;
    const theta = d2r(lon - 90);
    return { x: cx + rho * Math.cos(theta), y: cy + rho * Math.sin(theta) };
  }

  // Полярная азимутальная проекция: центр — Северный полюс, экватор на половине радиуса.
  function drawGraticule(ctx, cx, cy, Rmax, faint) {
    ctx.strokeStyle = faint ? 'rgba(150,150,150,0.12)' : 'rgba(150,150,150,0.20)';
    ctx.fillStyle = faint ? 'rgba(180,180,180,0.35)' : 'rgba(180,180,180,0.55)';
    ctx.lineWidth = 1;
    ctx.font = '10px ui-monospace,monospace';
    [80, 60, 40, 20, 0].forEach(lat => {
      const rho = (90 - lat) / 180 * Rmax;
      ctx.beginPath(); ctx.arc(cx, cy, rho, 0, Math.PI * 2); ctx.stroke();
      ctx.fillText(lat + '°', cx + 4, cy - rho + 10);
    });
    for (let lon = 0; lon < 360; lon += 45) {
      const p1 = projectPolar(90, lon, cx, cy, Rmax), p2 = projectPolar(-5, lon, cx, cy, Rmax);
      ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();
    }
  }

  function drawEarth(ctx, cx, cy, Rmax) {
    if (mapMode !== 'map') { drawGraticule(ctx, cx, cy, Rmax, false); return; }
    // Океан-диск с мягким градиентом.
    ctx.save();
    ctx.beginPath(); ctx.arc(cx, cy, Rmax, 0, Math.PI * 2); ctx.clip();
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, Rmax);
    g.addColorStop(0, '#15304f');
    g.addColorStop(0.6, '#102844');
    g.addColorStop(1, '#0b1a2e');
    ctx.fillStyle = g;
    ctx.fillRect(cx - Rmax, cy - Rmax, Rmax * 2, Rmax * 2);
    // Континенты.
    const coast = window.COASTLINES || [];
    ctx.lineJoin = 'round';
    for (const ring of coast) {
      ctx.beginPath();
      for (let i = 0; i < ring.length; i++) {
        const p = projectPolar(ring[i][1], ring[i][0], cx, cy, Rmax);
        if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
      }
      ctx.closePath();
      ctx.fillStyle = 'rgba(58,82,58,0.55)';
      ctx.fill();
      ctx.strokeStyle = 'rgba(120,168,128,0.55)';
      ctx.lineWidth = 0.8;
      ctx.stroke();
    }
    ctx.restore();
    // Внешний ободок диска.
    ctx.beginPath(); ctx.arc(cx, cy, Rmax, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(120,150,190,0.25)'; ctx.lineWidth = 1; ctx.stroke();
    drawGraticule(ctx, cx, cy, Rmax, true);
  }

  let hitTargets = [];
  let satHitTargets = [];
  function drawMap(tSecOverride) {
    const canvas = document.getElementById('mapCanvas');
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height, cx = W / 2, cy = H / 2, Rmax = 280;
    ctx.clearRect(0, 0, W, H);
    ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    hitTargets = [];
    satHitTargets = [];

    const v = activeVariant();
    drawEarth(ctx, cx, cy, Rmax);

    if (!v) {
      ctx.fillStyle = 'rgba(180,180,180,0.7)';
      ctx.font = '13px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Выполните расчёт варианта, чтобы увидеть сеть', cx, cy);
      ctx.textAlign = 'left';
      return;
    }

    const tSec = (tSecOverride != null) ? tSecOverride : currentStep * v.config.stepSeconds;
    const step = buildStep(v.config, tSec);

    ctx.strokeStyle = 'rgba(73,211,222,0.28)';
    ctx.lineWidth = 1;
    for (const [a, b] of step.islEdges) {
      const pa = step.satPos[a], pb = step.satPos[b];
      const p1 = projectPolar(pa.lat, pa.lon, cx, cy, Rmax), p2 = projectPolar(pb.lat, pb.lon, cx, cy, Rmax);
      ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();
    }

    let route = null;
    const routeSet = new Set();
    if (selectedGs) route = computeRoute(selectedGs, step, v.config);
    if (route && route.ok) {
      const gs = v.config.groundStations.find(g => g.name === selectedGs);
      const gw = v.config.groundStations.find(g => g.gateway);
      const pts = [projectPolar(gs.lat, gs.lon, cx, cy, Rmax)];
      for (const nodeId of route.path.slice(1, -1)) {
        routeSet.add(nodeId);
        const sp = step.satPos[nodeId];
        pts.push(projectPolar(sp.lat, sp.lon, cx, cy, Rmax));
      }
      pts.push(projectPolar(gw.lat, gw.lon, cx, cy, Rmax));
      ctx.strokeStyle = '#57D68D'; ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y);
      for (const p of pts.slice(1)) ctx.lineTo(p.x, p.y);
      ctx.stroke();
    }

    // Спутники, находящиеся в отказе (нет в satPos), рисуем приглушённо — чтобы было видно
    // «аппарат и его состояние», как требует ТЗ, а не просто исчезновение точки.
    for (const sat of generateSatellites(v.config)) {
      if (step.satPos[sat.id]) continue; // активные нарисуем ниже
      const eci = Geometry.satECI(sat, tSec);
      const geo = Geometry.ecefToGeodetic(Geometry.eciToEcef(eci, tSec));
      const p = projectPolar(geo.lat, geo.lon, cx, cy, Rmax);
      ctx.beginPath(); ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(143,161,189,0.28)'; ctx.fill();
      ctx.strokeStyle = 'rgba(229,99,122,0.7)'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(p.x - 3, p.y - 3); ctx.lineTo(p.x + 3, p.y + 3);
      ctx.moveTo(p.x + 3, p.y - 3); ctx.lineTo(p.x - 3, p.y + 3); ctx.stroke();
      satHitTargets.push({ x: p.x, y: p.y, id: sat.id, plane: sat.plane, down: true });
    }

    // Активные спутники; на маршруте — крупнее, с белым кольцом и подписью id.
    for (const id in step.satPos) {
      const s = step.satPos[id];
      const p = projectPolar(s.lat, s.lon, cx, cy, Rmax);
      const onRoute = routeSet.has(id);
      ctx.beginPath(); ctx.arc(p.x, p.y, onRoute ? 5 : 3.2, 0, Math.PI * 2);
      ctx.fillStyle = PLANE_COLORS[s.plane] || '#999';
      ctx.fill();
      if (onRoute) {
        ctx.strokeStyle = '#E7EDF5'; ctx.lineWidth = 1.5; ctx.stroke();
        ctx.fillStyle = '#E7EDF5'; ctx.font = '10px ui-monospace,monospace';
        ctx.fillText(id, p.x + 7, p.y - 6);
      }
      satHitTargets.push({ x: p.x, y: p.y, id, plane: s.plane, down: false, onRoute });
    }

    for (const gs of v.config.groundStations) {
      const p = projectPolar(gs.lat, gs.lon, cx, cy, Rmax);
      ctx.beginPath();
      if (gs.gateway) {
        ctx.moveTo(p.x, p.y - 7); ctx.lineTo(p.x + 7, p.y + 5); ctx.lineTo(p.x - 7, p.y + 5); ctx.closePath();
        ctx.fillStyle = '#F2A93B';
      } else {
        const ok = computeRoute(gs.name, step, v.config).ok;
        ctx.rect(p.x - 5, p.y - 5, 10, 10);
        ctx.fillStyle = ok ? '#57D68D' : '#E5637A';
      }
      ctx.fill();
      ctx.strokeStyle = gs.name === selectedGs ? '#E7EDF5' : 'rgba(231,237,245,0.4)';
      ctx.lineWidth = gs.name === selectedGs ? 2 : 1;
      ctx.stroke();
      ctx.fillStyle = '#E7EDF5'; ctx.font = '11px sans-serif';
      ctx.fillText(gs.name, p.x + 9, p.y + 4);
      hitTargets.push({ x: p.x, y: p.y, name: gs.name, gateway: gs.gateway });
    }
  }

  function renderLegend() {
    document.getElementById('legend').innerHTML = `
      <div class="item"><span class="dot" style="background:${PLANE_COLORS[1]}"></span>Плоскость 1</div>
      <div class="item"><span class="dot" style="background:${PLANE_COLORS[2]}"></span>Плоскость 2</div>
      <div class="item"><span class="dot" style="background:${PLANE_COLORS[3]}"></span>Плоскость 3</div>
      <div class="item"><span class="line" style="background:rgba(73,211,222,0.6)"></span>Связь МСС</div>
      <div class="item"><span class="line" style="background:#57D68D;height:3px"></span>Активный маршрут</div>
      <div class="item"><span class="dot" style="background:rgba(143,161,189,0.35);border:1px solid rgba(229,99,122,0.7)"></span>КА в отказе</div>
      <div class="item"><span class="dot" style="background:#57D68D;border-radius:2px"></span>Пункт: связь есть</div>
      <div class="item"><span class="dot" style="background:#E5637A;border-radius:2px"></span>Пункт: связи нет</div>
      <div class="item"><span class="dot" style="background:#F2A93B;border-radius:50% 50% 0 50%;transform:rotate(45deg)"></span>Шлюз</div>
      <div class="hint" style="margin-top:4px">Наведите курсор на спутник — покажется его номер и состояние. Клик по спутнику — выбрать его для задания отказа.</div>
    `;
  }

  function canvasPoint(e) {
    const rect = e.target.getBoundingClientRect();
    const scaleX = e.target.width / rect.width, scaleY = e.target.height / rect.height;
    return { x: (e.clientX - rect.left) * scaleX, y: (e.clientY - rect.top) * scaleY };
  }
  function nearest(targets, x, y) {
    let best = null, bd = Infinity;
    for (const t of targets) { const d = Math.hypot(t.x - x, t.y - y); if (d < bd) { bd = d; best = t; } }
    return { best, bd };
  }

  document.getElementById('mapCanvas').addEventListener('click', e => {
    const { x, y } = canvasPoint(e);
    const gsHit = nearest(hitTargets.filter(t => !t.gateway), x, y);
    const satHit = nearest(satHitTargets, x, y);
    // Что ближе к курсору — тем и управляем. Спутник → выбираем его для задания отказа.
    if (satHit.best && satHit.bd < 14 && (!gsHit.best || satHit.bd < gsHit.bd)) {
      selectOutageSat(satHit.best.id);
      return;
    }
    if (gsHit.best && gsHit.bd < 20) { selectedGs = gsHit.best.name; renderGsSelect(); update(); }
  });

  const tooltip = document.getElementById('mapTooltip');
  document.getElementById('mapCanvas').addEventListener('mousemove', e => {
    const { x, y } = canvasPoint(e);
    const satHit = nearest(satHitTargets, x, y);
    if (satHit.best && satHit.bd < 14) {
      const s = satHit.best;
      const state = s.down ? 'в отказе' : (s.onRoute ? 'на активном маршруте' : 'активен');
      tooltip.innerHTML = `<b>${s.id}</b> · плоскость ${s.plane}<br>${state}`;
      tooltip.style.display = 'block';
      tooltip.style.left = (e.offsetX + 12) + 'px';
      tooltip.style.top = (e.offsetY + 12) + 'px';
      e.target.style.cursor = 'pointer';
    } else {
      const gsHit = nearest(hitTargets.filter(t => !t.gateway), x, y);
      tooltip.style.display = 'none';
      e.target.style.cursor = (gsHit.best && gsHit.bd < 20) ? 'pointer' : 'default';
    }
  });
  document.getElementById('mapCanvas').addEventListener('mouseleave', () => { tooltip.style.display = 'none'; });

  // Выбирает спутник в списке отказов и подсвечивает форму — закрывает сценарий проверки
  // «выбрать спутник текущего маршрута, задать период его недоступности».
  function selectOutageSat(satId) {
    const sel = document.getElementById('outageSatSelect');
    if ([...sel.options].some(o => o.value === satId)) {
      sel.value = satId;
      const panel = document.getElementById('outagePanel');
      panel.scrollIntoView({ behavior: 'smooth', block: 'center' });
      panel.classList.add('flash');
      setTimeout(() => panel.classList.remove('flash'), 900);
    }
  }

  /* ======================= ВЫБОР ПУНКТА / ПАНЕЛЬ ======================= */
  function renderGsSelect() {
    const v = activeVariant();
    const wrap = document.getElementById('gsSelectButtons');
    if (!v) { wrap.innerHTML = ''; return; }
    const gsList = v.config.groundStations.filter(g => !g.gateway);
    wrap.innerHTML = gsList.map(g => `<button class="${g.name === selectedGs ? 'sel' : ''}" onclick="selectGs('${g.name.replace(/'/g, "\\'")}')">${escapeHtml(g.name)}</button>`).join('');
  }
  window.selectGs = function selectGs(name) { selectedGs = name; renderGsSelect(); update(); };

  function renderGsPanel() {
    const v = activeVariant();
    const statsEl = document.getElementById('gsStats');
    const routeEl = document.getElementById('routeBox');
    if (!v || !selectedGs) {
      statsEl.innerHTML = '';
      routeEl.className = 'route';
      routeEl.textContent = 'Выполните расчёт варианта, чтобы увидеть маршрут.';
      return;
    }
    const tSec = currentStep * v.config.stepSeconds;
    const step = buildStep(v.config, tSec);
    const vis = step.gsVis[selectedGs] || [];
    const route = computeRoute(selectedGs, step, v.config);

    statsEl.innerHTML = `
      <div class="stat-tile"><div class="k">Видимых спутников</div><div class="v">${vis.length}</div></div>
      <div class="stat-tile"><div class="k">Макс. угол места</div><div class="v">${vis.length ? vis[0].elevDeg.toFixed(1) + '°' : '—'}</div></div>
      <div class="stat-tile"><div class="k">Хопов до шлюза</div><div class="v">${route.ok ? route.hops : '—'}</div></div>
      <div class="stat-tile"><div class="k">Длина участка МСС</div><div class="v">${route.ok ? Math.round(route.distKm) + ' км' : '—'}</div></div>
    `;
    if (route.ok) {
      routeEl.className = 'route ok';
      routeEl.textContent = 'Маршрут: ' + route.path.join(' → ');
    } else {
      routeEl.className = 'route fail';
      routeEl.textContent = REASON_TEXT[route.reason] || 'Маршрут не найден';
    }
  }

  /* ======================= ВРЕМЕННАЯ ШКАЛА ======================= */
  function formatTime(tSec) {
    const totalMin = Math.floor(tSec / 60);
    const hh = String(Math.floor(totalMin / 60) % 24).padStart(2, '0');
    const mm = String(totalMin % 60).padStart(2, '0');
    return hh + ':' + mm;
  }
  window.onTimeChange = function onTimeChange(v) { currentStep = Number(v); update(); };
  window.setMapMode = function setMapMode(mode) {
    mapMode = mode;
    document.getElementById('modeMap').classList.toggle('sel', mode === 'map');
    document.getElementById('modeGrid').classList.toggle('sel', mode === 'grid');
    drawMap();
  };
  window.togglePlay = function togglePlay() {
    const v = activeVariant();
    if (!v) return;
    playing = !playing;
    document.getElementById('playBtn').textContent = playing ? '⏸' : '▶';
    if (playing) {
      lastTs = null;
      simSeconds = currentStep * v.config.stepSeconds;
      rafId = requestAnimationFrame(playLoop);
    } else if (rafId) {
      cancelAnimationFrame(rafId);
    }
  };
  // Плавное проигрывание: спутники двигаются непрерывно (rAF), а дискретный шаг обновляется
  // только для курсора Ганта и панели пункта — так анимация гладкая, а метрики согласованы.
  function playLoop(ts) {
    if (!playing) return;
    const v = activeVariant();
    if (!v) { playing = false; return; }
    if (lastTs === null) lastTs = ts;
    const dt = (ts - lastTs) / 1000; lastTs = ts;
    const total = v.metrics.totalSteps, stepSec = v.config.stepSeconds;
    const totalSim = total * stepSec;
    const speed = totalSim / PLAY_DURATION_S;
    simSeconds = (simSeconds + dt * speed) % totalSim;
    drawMap(simSeconds);
    document.getElementById('timeLabel').textContent = formatTime(simSeconds);
    const newStep = Math.floor(simSeconds / stepSec) % total;
    if (newStep !== currentStep) {
      currentStep = newStep;
      document.getElementById('timeSlider').value = currentStep;
      updateGanttCursor();
      renderGsPanel();
    }
    rafId = requestAnimationFrame(playLoop);
  }

  /* ======================= ДИАГРАММА ДОСТУПНОСТИ (ГАНТ) ======================= */
  function renderGantt() {
    const v = activeVariant();
    const el = document.getElementById('gantt');
    if (!v) { el.innerHTML = '<div class="placeholder">Нет данных — выполните расчёт варианта.</div>'; return; }
    const total = v.metrics.totalSteps;
    let html = '';
    for (const gsName in v.metrics.perGs) {
      const m = v.metrics.perGs[gsName];
      const segs = m.segments.map(([a, b]) => {
        const left = (a / total * 100).toFixed(2), width = ((b - a + 1) / total * 100).toFixed(2);
        return `<div class="seg" style="left:${left}%;width:${width}%"></div>`;
      }).join('');
      html += `
        <div class="ganttRow">
          <div class="label">${escapeHtml(gsName)} <span style="color:var(--muted)">(${m.availability.toFixed(1)}%)</span></div>
          <div class="ganttTrack" onclick="ganttClick(event,${total})">${segs}<div class="cursor" id="cursor-${gsName.replace(/\s/g, '_')}"></div></div>
        </div>`;
    }
    el.innerHTML = html;
    updateGanttCursor();
  }
  window.ganttClick = function ganttClick(e, total) {
    const rect = e.currentTarget.getBoundingClientRect();
    const frac = (e.clientX - rect.left) / rect.width;
    currentStep = Math.max(0, Math.min(total - 1, Math.round(frac * total)));
    document.getElementById('timeSlider').value = currentStep;
    update();
  };
  function updateGanttCursor() {
    const v = activeVariant(); if (!v) return;
    const total = v.metrics.totalSteps;
    const pct = (currentStep / total * 100) + '%';
    document.querySelectorAll('.cursor').forEach(c => c.style.left = pct);
  }

  /* ======================= СРАВНЕНИЕ ВАРИАНТОВ ======================= */
  function configSummary(config) {
    const sats = generateSatellites(config);
    const planesCount = config.customPlanes ? config.customPlanes.length : config.activePlanes.length;
    return [
      ['Спутников в группировке', String(sats.length)],
      ['Орбитальных плоскостей', String(planesCount)],
      ['RAAN-смещение, °', config.customPlanes ? 'задано файлом' : config.raanOffset.join(' / ')],
      ['Фазирование, °', config.customPlanes ? 'задано файлом' : config.phaseOffset.join(' / ')],
      ['Дальность МСС, км', String(config.islMaxRangeKm)],
      ['Порог угла места, °', String(config.minElevationDeg)],
      ['Длительность, ч', String(config.durationHours)],
      ['Шаг расчёта, с', String(config.stepSeconds)],
      ['Задано периодов отказов', String((config.outages || []).length)]
    ];
  }

  function renderCompare() {
    const checked = [...document.querySelectorAll('[data-compare]')].filter(c => c.checked).map(c => c.dataset.compare);
    const el = document.getElementById('compareArea');
    if (checked.length < 2) { el.innerHTML = '<div class="placeholder">Отметьте два и более варианта в списке слева галочкой «в сравнение», чтобы увидеть сопоставление.</div>'; return; }
    const vs = checked.map(id => variants.find(v => v.id === id));
    const allGs = [...new Set(vs.flatMap(v => Object.keys(v.metrics.perGs)))];

    // Таблица «изменённых параметров»: строки с различающимися значениями подсвечиваются.
    const summaries = vs.map(v => configSummary(v.config));
    const paramLabels = summaries[0].map(r => r[0]);
    let paramRows = '';
    paramLabels.forEach((label, ri) => {
      const cells = summaries.map(s => s[ri][1]);
      const differs = cells.some(c => c !== cells[0]);
      paramRows += `<tr>${`<td>${escapeHtml(label)}</td>`}` +
        cells.map(c => `<td class="num ${differs ? 'diff' : ''}">${escapeHtml(c)}</td>`).join('') + '</tr>';
    });
    const paramHead = '<tr><th>Параметр</th>' + vs.map(v => `<th>${escapeHtml(v.name)}</th>`).join('') + '</tr>';
    const paramTable = `<h3>Параметры конфигурации <span class="hint" style="font-weight:400">(отличия подсвечены)</span></h3><table>${paramHead}${paramRows}</table>`;

    let head = '<tr><th>Пункт</th>' + vs.map(v => `<th>${escapeHtml(v.name)}</th>`).join('') + '</tr>';
    let rows = '';
    for (const gs of allGs) {
      rows += `<tr><td>${escapeHtml(gs)}</td>` + vs.map(v => {
        const m = v.metrics.perGs[gs];
        if (!m) return '<td class="num">—</td>';
        const cls = m.availability >= 90 ? 'good' : 'bad';
        return `<td class="num"><span class="badge ${cls}">${m.availability.toFixed(1)}%</span> · макс.перерыв ${Math.round(m.maxOutageMin)} мин · ${m.outageCount} сбоя(ев)</td>`;
      }).join('') + '</tr>';
    }
    rows += '<tr><td><b>В среднем</b></td>' + vs.map(v => `<td class="num"><b>${v.metrics.overallAvailability.toFixed(1)}%</b></td>`).join('') + '</tr>';

    const best = vs.reduce((a, b) => b.metrics.overallAvailability > a.metrics.overallAvailability ? b : a);
    const worst = vs.reduce((a, b) => b.metrics.overallAvailability < a.metrics.overallAvailability ? b : a);
    const anyMeets = vs.some(v => v.metrics.meetsTarget);
    const rec = `Наилучшую среднюю доступность (${best.metrics.overallAvailability.toFixed(1)}%) обеспечивает вариант «${escapeHtml(best.name)}»` +
      (best.metrics.meetsTarget ? ', для всех наземных пунктов достигнут целевой ориентир 90%.' : ', однако не для всех пунктов достигнут целевой ориентир 90% — целесообразно проверить конфигурацию для пунктов с наибольшими перерывами.') +
      ` Наименьшую доступность (${worst.metrics.overallAvailability.toFixed(1)}%) показал вариант «${escapeHtml(worst.name)}».` +
      (anyMeets ? '' : ' Ни один из сравниваемых вариантов пока не обеспечивает 90% для всех пунктов — рассмотрите увеличение числа развёрнутых плоскостей, дальности МСС или снижение порога угла места.');

    el.innerHTML = `${paramTable}<h3 style="margin-top:14px">Показатели доступности</h3><table>${head}${rows}</table><div class="recommendation">${rec}</div>`;
  }

  /* ======================= ОБНОВЛЕНИЕ ======================= */
  function update() {
    const slider = document.getElementById('timeSlider');
    const v = activeVariant();
    if (v) slider.max = Math.max(1, v.metrics.totalSteps - 1); // диапазон зависит от длительности/шага
    slider.value = currentStep;
    const stepSec = v ? v.config.stepSeconds : 120;
    document.getElementById('timeLabel').textContent = formatTime(currentStep * stepSec);
    drawMap();
    renderGsPanel();
    updateGanttCursor();
  }

  /* ======================= ИНИЦИАЛИЗАЦИЯ ======================= */
  function init() {
    renderConfigForm();
    renderLegend();
    renderVariants();
    update();
  }
  init();
})();
