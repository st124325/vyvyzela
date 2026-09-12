/* Проверка файла сценария перед загрузкой в приложение. */
(function (root) {
  function isFiniteNumber(v) { return typeof v === 'number' && Number.isFinite(v); }

  function validateScenario(obj) {
    const errors = [];
    if (!obj || typeof obj !== 'object') { errors.push('Файл не является корректным JSON-объектом.'); return errors; }

    if (!Array.isArray(obj.ground_stations) || obj.ground_stations.length < 2) {
      errors.push('Поле "ground_stations" должно быть массивом минимум из 2 пунктов (включая шлюз).');
    } else {
      if (!obj.ground_stations.some(g => g.gateway === true)) errors.push('В "ground_stations" должен быть хотя бы один пункт с "gateway": true.');
      obj.ground_stations.forEach((g, i) => {
        if (typeof g.name !== 'string' || !g.name) errors.push(`ground_stations[${i}]: отсутствует текстовое поле "name".`);
        if (!isFiniteNumber(g.lat) || g.lat < -90 || g.lat > 90) errors.push(`ground_stations[${i}]: "lat" должно быть числом от -90 до 90.`);
        if (!isFiniteNumber(g.lon) || g.lon < -180 || g.lon > 180) errors.push(`ground_stations[${i}]: "lon" должно быть числом от -180 до 180.`);
      });
    }

    if (!Array.isArray(obj.planes) || obj.planes.length === 0) {
      errors.push('Поле "planes" должно быть непустым массивом орбитальных плоскостей.');
    } else {
      obj.planes.forEach((p, i) => {
        if (!isFiniteNumber(p.raan)) errors.push(`planes[${i}]: отсутствует числовое поле "raan".`);
        if (!isFiniteNumber(p.inclination)) errors.push(`planes[${i}]: отсутствует числовое поле "inclination".`);
        if (!isFiniteNumber(p.satellites) || p.satellites < 1) errors.push(`planes[${i}]: "satellites" должно быть положительным числом.`);
        if (p.phase_offset !== undefined && !isFiniteNumber(p.phase_offset)) errors.push(`planes[${i}]: "phase_offset" должно быть числом.`);
        if (p.id !== undefined && (!isFiniteNumber(p.id) || p.id < 1)) errors.push(`planes[${i}]: "id" (необязательное) должно быть положительным числом.`);
      });
    }

    if (obj.isl_max_range_km !== undefined && (!isFiniteNumber(obj.isl_max_range_km) || obj.isl_max_range_km <= 0)) {
      errors.push('"isl_max_range_km" должно быть положительным числом.');
    }
    if (obj.min_elevation_deg !== undefined && (!isFiniteNumber(obj.min_elevation_deg) || obj.min_elevation_deg < 0 || obj.min_elevation_deg > 90)) {
      errors.push('"min_elevation_deg" должно быть числом от 0 до 90.');
    }
    if (obj.duration_hours !== undefined && (!isFiniteNumber(obj.duration_hours) || obj.duration_hours <= 0)) {
      errors.push('"duration_hours" должно быть положительным числом.');
    }
    if (obj.step_seconds !== undefined && (!isFiniteNumber(obj.step_seconds) || obj.step_seconds <= 0)) {
      errors.push('"step_seconds" должно быть положительным числом.');
    }

    if (obj.outages !== undefined) {
      if (!Array.isArray(obj.outages)) {
        errors.push('"outages" должно быть массивом.');
      } else {
        obj.outages.forEach((o, i) => {
          if (o.type !== 'satellite' && o.type !== 'gateway') errors.push(`outages[${i}]: "type" должно быть "satellite" или "gateway".`);
          if (o.type === 'satellite' && (typeof o.id !== 'string' || !o.id)) errors.push(`outages[${i}]: для типа "satellite" требуется текстовое поле "id".`);
          if (!isFiniteNumber(o.start_hour) || o.start_hour < 0) errors.push(`outages[${i}]: "start_hour" должно быть неотрицательным числом.`);
          if (!isFiniteNumber(o.end_hour) || o.end_hour <= (o.start_hour ?? 0)) errors.push(`outages[${i}]: "end_hour" должно быть числом больше "start_hour".`);
        });
      }
    }

    return errors;
  }

  const Validate = { validateScenario, isFiniteNumber };
  root.Validate = Validate;
  if (typeof module !== 'undefined' && module.exports) module.exports = Validate;
})(typeof window !== 'undefined' ? window : globalThis);
