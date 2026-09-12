/* Орбитальная механика: невозмущённые круговые орбиты, вращение Земли, видимость. */
(function (root) {
  const MU = 398600.4418;          // км^3/с^2
  const R_E = 6371;                // км
  const ALT = 550;                 // км
  const A = R_E + ALT;
  const T_PERIOD = 2 * Math.PI * Math.sqrt(Math.pow(A, 3) / MU); // сек
  const MEAN_MOTION = 360 / T_PERIOD;                             // град/с
  const OMEGA_E = 360 / 86164.0905;                                // град/с (звёздные сутки)

  const d2r = d => d * Math.PI / 180;
  const r2d = r => r * 180 / Math.PI;
  const norm360 = d => ((d % 360) + 360) % 360;

  function satECI(sat, tSec) {
    const u = d2r(norm360(sat.u0 + MEAN_MOTION * tSec));
    const raan = d2r(sat.raan);
    const inc = d2r(sat.inc);
    const cu = Math.cos(u), su = Math.sin(u);
    const cr = Math.cos(raan), sr = Math.sin(raan);
    const ci = Math.cos(inc), si = Math.sin(inc);
    return {
      x: A * (cr * cu - sr * su * ci),
      y: A * (sr * cu + cr * su * ci),
      z: A * (su * si)
    };
  }
  function eciToEcef(p, tSec) {
    const th = d2r(norm360(OMEGA_E * tSec));
    const c = Math.cos(th), s = Math.sin(th);
    return { x: p.x * c + p.y * s, y: -p.x * s + p.y * c, z: p.z };
  }
  function geodeticToECEF(latDeg, lonDeg) {
    const lat = d2r(latDeg), lon = d2r(lonDeg), r = R_E;
    return { x: r * Math.cos(lat) * Math.cos(lon), y: r * Math.cos(lat) * Math.sin(lon), z: r * Math.sin(lat) };
  }
  function ecefToGeodetic(p) {
    const r = Math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z);
    return { lat: r2d(Math.asin(p.z / r)), lon: r2d(Math.atan2(p.y, p.x)) };
  }
  function elevationAngle(gsLat, gsLon, satEcef) {
    const gs = geodeticToECEF(gsLat, gsLon);
    const dx = satEcef.x - gs.x, dy = satEcef.y - gs.y, dz = satEcef.z - gs.z;
    const rangeMag = Math.sqrt(dx * dx + dy * dy + dz * dz);
    const upMag = Math.sqrt(gs.x * gs.x + gs.y * gs.y + gs.z * gs.z);
    const dot = (dx * gs.x + dy * gs.y + dz * gs.z) / upMag;
    return { elevDeg: r2d(Math.asin(Math.max(-1, Math.min(1, dot / rangeMag)))), rangeKm: rangeMag };
  }
  function segmentBlockedByEarth(p1, p2) {
    const d = { x: p2.x - p1.x, y: p2.y - p1.y, z: p2.z - p1.z };
    const dLen2 = d.x * d.x + d.y * d.y + d.z * d.z;
    let t = dLen2 === 0 ? 0 : -(p1.x * d.x + p1.y * d.y + p1.z * d.z) / dLen2;
    t = Math.max(0, Math.min(1, t));
    const c = { x: p1.x + d.x * t, y: p1.y + d.y * t, z: p1.z + d.z * t };
    return Math.sqrt(c.x * c.x + c.y * c.y + c.z * c.z) < (R_E - 2);
  }

  const Geometry = {
    MU, R_E, ALT, A, T_PERIOD, MEAN_MOTION, OMEGA_E,
    d2r, r2d, norm360,
    satECI, eciToEcef, geodeticToECEF, ecefToGeodetic, elevationAngle, segmentBlockedByEarth
  };

  root.Geometry = Geometry;
  if (typeof module !== 'undefined' && module.exports) module.exports = Geometry;
})(typeof window !== 'undefined' ? window : globalThis);
