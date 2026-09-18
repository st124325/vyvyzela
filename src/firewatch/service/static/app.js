/* Веб-интерфейс сервиса: запрос по территории и периоду, карта и справка.
 *
 * Карта строится на Leaflet с картографической подложкой OpenStreetMap. Если
 * библиотека недоступна (демонстрация без выхода в интернет), включается
 * встроенный SVG-рендер: подложки не будет, но геометрия, легенда и выгрузки
 * работают одинаково.
 */

const SEVERITY_COLORS = { 1: "#ffd166", 2: "#f4763b", 3: "#c1121f" };
const HOTSPOT_COLOR = "#ff3b30";

const $ = (id) => document.getElementById(id);
const state = { map: null, layers: null, result: null, bounds: null };

/* ------------------------------------------------------------------ запрос */

function readQuery() {
  const numbers = ["min-lon", "min-lat", "max-lon", "max-lat"].map((id) => parseFloat($(id).value));
  const request = { clip: $("clip").checked };
  if (numbers.every((value) => Number.isFinite(value))) request.bbox = numbers;
  const polygon = $("polygon").value.trim();
  if (polygon) {
    try {
      request.geometry = JSON.parse(polygon);
    } catch (error) {
      throw new Error("полигон не разбирается как GeoJSON: " + error.message);
    }
  }
  if ($("date-from").value) request.date_from = $("date-from").value;
  if ($("date-to").value) request.date_to = $("date-to").value;
  return request;
}

function queryString() {
  const request = readQuery();
  const parameters = new URLSearchParams();
  if (request.bbox) parameters.set("bbox", request.bbox.join(","));
  if (request.date_from) parameters.set("date_from", request.date_from);
  if (request.date_to) parameters.set("date_to", request.date_to);
  parameters.set("clip", request.clip ? "true" : "false");
  return parameters.toString();
}

async function runQuery() {
  const status = $("status");
  let request;
  try {
    request = readQuery();
  } catch (error) {
    status.textContent = error.message;
    return;
  }
  status.textContent = "запрос выполняется…";
  const started = performance.now();
  try {
    const response = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    if (!response.ok) throw new Error("сервис ответил " + response.status);
    state.result = await response.json();
    render(state.result);
    const elapsed = Math.round(performance.now() - started);
    status.textContent = `термоточек: ${state.result.hotspots.features.length}, `
      + `контуров: ${state.result.burn_scars.features.length}, время ${elapsed} мс`;
  } catch (error) {
    status.textContent = "ошибка: " + error.message;
  }
}

/* ------------------------------------------------------------------ справка */

function render(result) {
  const body = $("summary").querySelector("tbody");
  body.innerHTML = "";
  for (const row of result.summary.by_severity) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><i class="box sev${row.severity_class}"></i> ${row.severity_label}</td>`
      + `<td class="num">${row.area_ha.toFixed(2)}</td><td class="num">${row.share_percent.toFixed(1)}</td>`;
    body.appendChild(tr);
  }
  $("totals").textContent = `Всего гари: ${result.summary.total_area_ha.toFixed(2)} га · `
    + `контуров: ${result.summary.contours} · термоточек: ${result.summary.hotspots}`;
  drawFeatures(result);
}

/* --------------------------------------------------------------------- карта */

function initMap() {
  if (window.L && !window.leafletFailed) {
    state.map = L.map("map", { preferCanvas: true }).setView([48.5, 44.0], 6);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18, attribution: "© OpenStreetMap",
    }).addTo(state.map);
    state.layers = L.layerGroup().addTo(state.map);
    enableBoxSelection();
  } else {
    $("status").textContent = "Leaflet недоступен — карта показана без подложки";
  }
}

function enableBoxSelection() {
  // Shift + перетаскивание: выбор прямоугольника запроса прямо на карте.
  const map = state.map;
  let origin = null;
  let rectangle = null;
  map.dragging.disable();
  map.on("mousedown", (event) => {
    if (!event.originalEvent.shiftKey) { map.dragging.enable(); return; }
    map.dragging.disable();
    origin = event.latlng;
    rectangle = L.rectangle([origin, origin], { color: "#ff7043", weight: 1, fill: false }).addTo(map);
  });
  map.on("mousemove", (event) => {
    if (origin && rectangle) rectangle.setBounds(L.latLngBounds(origin, event.latlng));
  });
  map.on("mouseup", (event) => {
    map.dragging.enable();
    if (!origin) return;
    const bounds = L.latLngBounds(origin, event.latlng);
    $("min-lon").value = bounds.getWest().toFixed(4);
    $("min-lat").value = bounds.getSouth().toFixed(4);
    $("max-lon").value = bounds.getEast().toFixed(4);
    $("max-lat").value = bounds.getNorth().toFixed(4);
    origin = null;
    if (rectangle) { map.removeLayer(rectangle); rectangle = null; }
  });
  map.dragging.enable();
}

function drawFeatures(result) {
  if (state.map) { drawLeaflet(result); } else { drawSvg(result); }
}

function drawLeaflet(result) {
  state.layers.clearLayers();
  L.geoJSON(result.burn_scars, {
    style: (feature) => ({
      color: SEVERITY_COLORS[feature.properties.severity_class] || "#888",
      weight: 1, fillOpacity: 0.45,
    }),
    onEachFeature: (feature, layer) => layer.bindPopup(describe(feature.properties)),
  }).addTo(state.layers);
  L.geoJSON(result.hotspots, {
    pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
      radius: 4, color: HOTSPOT_COLOR, fillColor: HOTSPOT_COLOR, fillOpacity: 0.9, weight: 1,
    }),
    onEachFeature: (feature, layer) => layer.bindPopup(describe(feature.properties)),
  }).addTo(state.layers);
  const bounds = state.layers.getLayers().length ? L.featureGroup(state.layers.getLayers()).getBounds() : null;
  if (bounds && bounds.isValid()) state.map.fitBounds(bounds.pad(0.15));
}

function describe(properties) {
  return Object.entries(properties)
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => `<b>${key}</b>: ${value}`)
    .join("<br>");
}

/* Резервный рендер без Leaflet: равнопромежуточная проекция в пределах данных. */
function drawSvg(result) {
  const features = result.burn_scars.features.concat(result.hotspots.features);
  const container = $("map");
  container.innerHTML = "";
  if (!features.length) { container.textContent = ""; return; }

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const walk = (coordinates, visit) => {
    if (typeof coordinates[0] === "number") { visit(coordinates); return; }
    coordinates.forEach((item) => walk(item, visit));
  };
  features.forEach((feature) => walk(feature.geometry.coordinates, ([x, y]) => {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  }));
  const padX = (maxX - minX) * 0.08 || 0.01;
  const padY = (maxY - minY) * 0.08 || 0.01;
  minX -= padX; maxX += padX; minY -= padY; maxY += padY;

  const width = container.clientWidth || 800;
  const height = container.clientHeight || 500;
  const project = ([x, y]) => [
    ((x - minX) / (maxX - minX)) * width,
    height - ((y - minY) / (maxY - minY)) * height,
  ];

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const draw = (tag, attributes) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
    svg.appendChild(node);
  };

  result.burn_scars.features.forEach((feature) => {
    const color = SEVERITY_COLORS[feature.properties.severity_class] || "#888";
    const rings = feature.geometry.type === "Polygon"
      ? feature.geometry.coordinates
      : feature.geometry.coordinates.flat();
    rings.forEach((ring) => {
      const points = ring.map(project).map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
      draw("polygon", { points, fill: color, "fill-opacity": 0.45, stroke: color, "stroke-width": 1 });
    });
  });
  result.hotspots.features.forEach((feature) => {
    const [x, y] = project(feature.geometry.coordinates);
    draw("circle", { cx: x.toFixed(1), cy: y.toFixed(1), r: 4, fill: HOTSPOT_COLOR });
  });
  container.appendChild(svg);
}

/* ------------------------------------------------------------------ загрузки */

function download(path) {
  window.location.href = path + (path.includes("?") ? "&" : "?") + queryString();
}

async function loadCatalog() {
  try {
    const response = await fetch("/api/catalog");
    const catalog = await response.json();
    $("catalog-info").textContent = `сцен: ${catalog.scenes.length} · `
      + `термоточек: ${catalog.hotspots_total} · контуров: ${catalog.contours_total}`;
    const dates = catalog.scenes.map((scene) => scene.date).filter(Boolean).sort();
    if (dates.length) {
      $("date-from").value = dates[0];
      $("date-to").value = dates[dates.length - 1];
    }
  } catch (error) {
    $("catalog-info").textContent = "каталог недоступен: " + error.message;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initMap();
  loadCatalog();
  $("run").addEventListener("click", runQuery);
  $("reset").addEventListener("click", () => {
    ["min-lon", "min-lat", "max-lon", "max-lat", "polygon"].forEach((id) => ($(id).value = ""));
    $("status").textContent = "";
  });
  $("download-geojson").addEventListener("click", () => download("/api/burn-scars?download=true"));
  $("download-csv").addEventListener("click", () => download("/api/summary?format=csv"));
  $("download-json").addEventListener("click", () => download("/api/summary?format=json"));
});
