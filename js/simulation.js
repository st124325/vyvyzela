/* Модель данных, генерация спутников, состояние сети на шаг времени, суточная симуляция. */
(function (root) {
  const Geometry = root.Geometry || (typeof require !== 'undefined' ? require('./geometry.js') : null);
  const Routing = root.Routing || (typeof require !== 'undefined' ? require('./routing.js') : null);

  const DEFAULT_CONFIG = () => ({
    activePlanes: [1, 2, 3],
    raanBase: [0, 120, 240],
    raanOffset: [0, 0, 0],
    phaseOffset: [0, 0, 0],
    inclinationDeg: 86,
    islMaxRangeKm: 3000,
    minElevationDeg: 10,
    durationHours: 24,
    stepSeconds: 120,
    outages: [],
    customPlanes: null,
    groundStations: [
      { name: 'Мурманск', lat: 68.97, lon: 33.09, gateway: false },
      { name: 'Салехард', lat: 66.53, lon: 66.60, gateway: false },
      { name: 'Норильск', lat: 69.35, lon: 88.20, gateway: false },
      { name: 'Тикси', lat: 71.64, lon: 128.87, gateway: false },
      { name: 'Певек', lat: 69.70, lon: 170.31, gateway: false },
      { name: 'Москва (шлюз)', lat: 55.75, lon: 37.62, gateway: true }
    ]
  });

  const PLANE_COLORS = { 1: '#49D3DE', 2: '#9C8CFB', 3: '#F2A93B', 4: '#57D68D', 5: '#E5637A' };

  function generateSatellites(config) {
    const sats = [];
    if (config.customPlanes) {
      for (const p of config.customPlanes) {
        const n = Number(p.satellites);
        if (!Number.isFinite(n) || n < 1) continue; // защита от деления на 0 при повреждённом конфиге
        for (let s = 0; s < n; s++) {
          sats.push({
            id: `P${p.id}-S${s + 1}`, plane: p.id, raan: p.raan, inc: p.inclination,
            u0: (360 / n) * s + (p.phase_offset || 0)
          });
        }
      }
      return sats;
    }
    for (let p = 1; p <= 3; p++) {
      if (!config.activePlanes.includes(p)) continue;
      const raan = config.raanBase[p - 1] + (config.raanOffset[p - 1] || 0);
      for (let s = 0; s < 16; s++) {
        sats.push({
          id: `P${p}-S${s + 1}`, plane: p, raan, inc: config.inclinationDeg,
          u0: (360 / 16) * s + (config.phaseOffset[p - 1] || 0)
        });
      }
    }
    return sats;
  }

  function isSatDown(satId, outages, hour) {
    return outages.some(o => o.type === 'satellite' && o.id === satId && hour >= o.startHour && hour < o.endHour);
  }
  function isGatewayDown(outages, hour) {
    return outages.some(o => o.type === 'gateway' && hour >= o.startHour && hour < o.endHour);
  }

  /* Строит состояние сети на момент времени tSec. sats можно передать заранее (список статичен на весь расчёт). */
  function buildStep(config, tSec, sats) {
    const hour = tSec / 3600;
    const list = sats || generateSatellites(config);
    const satPos = {};
    for (const sat of list) {
      if (isSatDown(sat.id, config.outages, hour)) continue;
      const eci = Geometry.satECI(sat, tSec);
      const ecef = Geometry.eciToEcef(eci, tSec);
      const geo = Geometry.ecefToGeodetic(ecef);
      satPos[sat.id] = { ecef, lat: geo.lat, lon: geo.lon, plane: sat.plane };
    }
    const gwDown = isGatewayDown(config.outages, hour);
    const gsVis = {};
    for (const gs of config.groundStations) {
      if (gs.gateway && gwDown) { gsVis[gs.name] = []; continue; }
      const vlist = [];
      for (const id in satPos) {
        const { elevDeg } = Geometry.elevationAngle(gs.lat, gs.lon, satPos[id].ecef);
        if (elevDeg >= config.minElevationDeg) vlist.push({ satId: id, elevDeg });
      }
      vlist.sort((a, b) => b.elevDeg - a.elevDeg);
      gsVis[gs.name] = vlist;
    }
    const ids = Object.keys(satPos);
    const islEdges = [];
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const pa = satPos[ids[i]].ecef, pb = satPos[ids[j]].ecef;
        const dist = Math.hypot(pa.x - pb.x, pa.y - pb.y, pa.z - pb.z);
        if (dist <= config.islMaxRangeKm && !Geometry.segmentBlockedByEarth(pa, pb)) islEdges.push([ids[i], ids[j], dist]);
      }
    }
    return { satPos, gsVis, islEdges, gwDown };
  }

  /* Полный расчёт варианта за сутки: метрики для сравнения и Ганта. */
  function simulateVariant(config) {
    const durationHours = Number(config.durationHours);
    const stepSeconds = Number(config.stepSeconds);
    if (!Number.isFinite(durationHours) || durationHours <= 0) throw new Error('durationHours должно быть положительным числом');
    if (!Number.isFinite(stepSeconds) || stepSeconds <= 0) throw new Error('stepSeconds должно быть положительным числом');

    const totalSteps = Math.max(10, Math.min(2880, Math.round(durationHours * 3600 / stepSeconds)));
    const gsList = config.groundStations.filter(g => !g.gateway);
    if (gsList.length === 0) throw new Error('В конфигурации нет наземных пунктов (кроме шлюза)');
    const sats = generateSatellites(config); // список статичен для всей симуляции — считаем один раз
    if (sats.length === 0) throw new Error('Группировка не содержит спутников при текущей конфигурации');

    const arrByGs = {}; gsList.forEach(g => arrByGs[g.name] = []);
    for (let t = 0; t < totalSteps; t++) {
      const step = buildStep(config, t * stepSeconds, sats);
      for (const g of gsList) {
        const r = Routing.computeRoute(g.name, step, config);
        arrByGs[g.name].push(r.ok ? { ok: true, hops: r.hops, distKm: r.distKm } : { ok: false, reason: r.reason });
      }
    }
    const perGs = {};
    for (const name in arrByGs) {
      const arr = arrByGs[name];
      const okCount = arr.filter(x => x.ok).length;
      const segments = []; let start = null;
      arr.forEach((x, i) => {
        if (!x.ok && start === null) start = i;
        if (x.ok && start !== null) { segments.push([start, i - 1]); start = null; }
      });
      if (start !== null) segments.push([start, arr.length - 1]);
      const stepMin = stepSeconds / 60;
      const outageDurationsMin = segments.map(([a, b]) => (b - a + 1) * stepMin);
      const oks = arr.filter(x => x.ok);
      perGs[name] = {
        availability: okCount / arr.length * 100,
        segments, outageDurationsMin,
        maxOutageMin: outageDurationsMin.length ? Math.max(...outageDurationsMin) : 0,
        outageCount: segments.length,
        avgHops: oks.length ? oks.reduce((s, x) => s + x.hops, 0) / oks.length : null
      };
    }
    const overallAvailability = Object.values(perGs).reduce((s, m) => s + m.availability, 0) / Object.keys(perGs).length;
    return { totalSteps, perGs, overallAvailability, meetsTarget: Object.values(perGs).every(m => m.availability >= 90) };
  }

  /* Доп. функция: для каждого активного спутника оценивает падение доступности при его 24-часовом отказе. */
  function findVulnerableSatellites(config, options) {
    const opts = options || {};
    const topN = opts.topN || 8;
    const baseline = simulateVariant(config);
    const sats = generateSatellites(config);
    const results = [];
    for (const sat of sats) {
      const testConfig = JSON.parse(JSON.stringify(config));
      testConfig.outages = (testConfig.outages || []).concat([{ type: 'satellite', id: sat.id, startHour: 0, endHour: testConfig.durationHours }]);
      const metrics = simulateVariant(testConfig);
      results.push({
        satId: sat.id,
        plane: sat.plane,
        availabilityDrop: baseline.overallAvailability - metrics.overallAvailability,
        overallAvailability: metrics.overallAvailability
      });
    }
    results.sort((a, b) => b.availabilityDrop - a.availabilityDrop);
    return { baselineAvailability: baseline.overallAvailability, ranked: results.slice(0, topN) };
  }

  /* Доп. функция: простой перебор фазирования/RAAN-смещения/дальности МСС/порога угла места,
     ищет конфигурацию с максимальной минимальной доступностью среди пунктов. */
  function autoTuneConfig(config, options) {
    const opts = options || {};
    const raanGrid = opts.raanGrid || [-40, -20, 0, 20, 40];
    const phaseGrid = opts.phaseGrid || [0, 7.5, 15, 22.5];
    const islGrid = opts.islGrid || [2000, 3000, 4000, 5000];
    const elevGrid = opts.elevGrid || [10, 7, 5];
    const stepSeconds = opts.searchStepSeconds || 600; // грубая сетка времени для скорости поиска

    let best = null;
    for (const elev of elevGrid) {
      for (const isl of islGrid) {
        for (const raan of raanGrid) {
          for (const phase of phaseGrid) {
            const cand = JSON.parse(JSON.stringify(config));
            cand.customPlanes = null;
            cand.raanOffset = [raan, raan, raan];
            cand.phaseOffset = [phase, phase, phase];
            cand.islMaxRangeKm = isl;
            cand.minElevationDeg = elev;
            cand.stepSeconds = stepSeconds;
            let metrics;
            try { metrics = simulateVariant(cand); } catch (e) { continue; }
            const minAvail = Math.min(...Object.values(metrics.perGs).map(m => m.availability));
            if (!best || minAvail > best.minAvail) {
              best = { minAvail, raanOffset: [raan, raan, raan], phaseOffset: [phase, phase, phase], islMaxRangeKm: isl, minElevationDeg: elev, overallAvailability: metrics.overallAvailability };
            }
          }
        }
      }
    }
    return best;
  }

  const Simulation = {
    DEFAULT_CONFIG, PLANE_COLORS,
    generateSatellites, isSatDown, isGatewayDown, buildStep, simulateVariant,
    findVulnerableSatellites, autoTuneConfig
  };
  root.Simulation = Simulation;
  if (typeof module !== 'undefined' && module.exports) module.exports = Simulation;
})(typeof window !== 'undefined' ? window : globalThis);
