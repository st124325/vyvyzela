/* Маршрутизация: кратчайший путь от наземного пункта до шлюза по графу МСС (Дейкстра). */
(function (root) {
  const REASON_TEXT = {
    no_visible_sat: 'Нет видимого спутника над пунктом',
    no_gateway_contact: 'Отсутствует контакт со шлюзом — над ним сейчас нет спутника',
    isl_break: 'Разрыв межспутниковой сети — нет цепочки МСС до шлюза',
    gateway_down: 'Шлюз недоступен (плановый перерыв на наземном сегменте)'
  };

  function computeRoute(gsName, step, config) {
    const gateway = config.groundStations.find(g => g.gateway);
    if (!gateway) return { ok: false, reason: 'gateway_down' };
    if (step.gwDown) return { ok: false, reason: 'gateway_down' };
    const visGs = step.gsVis[gsName] || [];
    if (visGs.length === 0) return { ok: false, reason: 'no_visible_sat' };
    const visGw = step.gsVis[gateway.name] || [];
    if (visGw.length === 0) return { ok: false, reason: 'no_gateway_contact' };

    const adj = {};
    for (const [a, b, d] of step.islEdges) {
      (adj[a] = adj[a] || []).push([b, d]);
      (adj[b] = adj[b] || []).push([a, d]);
    }
    const dist = {}, prev = {}, visited = new Set(), pq = [];
    for (const v of visGs) { dist[v.satId] = 0; prev[v.satId] = 'GS'; pq.push([0, v.satId]); }
    while (pq.length) {
      pq.sort((a, b) => a[0] - b[0]);
      const [dd, u] = pq.shift();
      if (visited.has(u)) continue;
      visited.add(u);
      for (const [v, w] of (adj[u] || [])) {
        const nd = dd + w;
        if (dist[v] === undefined || nd < dist[v]) { dist[v] = nd; prev[v] = u; pq.push([nd, v]); }
      }
    }
    let best = null, bestDist = Infinity;
    for (const v of visGw) { if (dist[v.satId] !== undefined && dist[v.satId] < bestDist) { bestDist = dist[v.satId]; best = v.satId; } }
    if (best === null) return { ok: false, reason: 'isl_break' };

    const chain = [best];
    let cur = best;
    let guard = 0;
    const maxHops = Object.keys(prev).length + 2;
    while (prev[cur] !== 'GS') {
      cur = prev[cur];
      if (cur === undefined) return { ok: false, reason: 'isl_break' };
      chain.push(cur);
      guard++;
      if (guard > maxHops) return { ok: false, reason: 'isl_break' };
    }
    chain.reverse();
    return { ok: true, distKm: bestDist, hops: chain.length, path: [gsName, ...chain, gateway.name] };
  }

  const Routing = { computeRoute, REASON_TEXT };
  root.Routing = Routing;
  if (typeof module !== 'undefined' && module.exports) module.exports = Routing;
})(typeof window !== 'undefined' ? window : globalThis);
