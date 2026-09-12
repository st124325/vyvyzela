# Независимый Python-повтор орбитальной физики (см. js/geometry.js) для офлайн-подбора
# конфигурации без браузера. Использовался, чтобы найти вариант, реально достигающий
# целевого ориентира доступности >=90% — см. README, раздел "Как подобран рекомендованный вариант".
import math, itertools, sys

MU = 398600.4418
R_E = 6371.0
ALT = 550.0
A = R_E + ALT
T_PERIOD = 2*math.pi*math.sqrt(A**3/MU)
MEAN_MOTION = 360.0/T_PERIOD
OMEGA_E = 360.0/86164.0905

def d2r(d): return d*math.pi/180
def r2d(r): return r*180/math.pi
def norm360(d): return (d % 360 + 360) % 360

def satECI(u0, raan, inc, tSec):
    u = d2r(norm360(u0 + MEAN_MOTION*tSec))
    raan_r = d2r(raan); inc_r = d2r(inc)
    cu, su = math.cos(u), math.sin(u)
    cr, sr = math.cos(raan_r), math.sin(raan_r)
    ci, si = math.cos(inc_r), math.sin(inc_r)
    return (A*(cr*cu - sr*su*ci), A*(sr*cu + cr*su*ci), A*(su*si))

def eciToEcef(p, tSec):
    th = d2r(norm360(OMEGA_E*tSec))
    c, s = math.cos(th), math.sin(th)
    x, y, z = p
    return (x*c + y*s, -x*s + y*c, z)

def geodeticToECEF(lat, lon):
    latr, lonr = d2r(lat), d2r(lon)
    r = R_E
    return (r*math.cos(latr)*math.cos(lonr), r*math.cos(latr)*math.sin(lonr), r*math.sin(latr))

def elevationAngle(gsLat, gsLon, satEcef):
    gx, gy, gz = geodeticToECEF(gsLat, gsLon)
    sx, sy, sz = satEcef
    dx, dy, dz = sx-gx, sy-gy, sz-gz
    rangeMag = math.sqrt(dx*dx+dy*dy+dz*dz)
    upMag = math.sqrt(gx*gx+gy*gy+gz*gz)
    dot = (dx*gx+dy*gy+dz*gz)/upMag
    return r2d(math.asin(max(-1,min(1,dot/rangeMag)))), rangeMag

def segBlocked(p1, p2):
    x1,y1,z1 = p1; x2,y2,z2 = p2
    dx,dy,dz = x2-x1,y2-y1,z2-z1
    dLen2 = dx*dx+dy*dy+dz*dz
    t = 0 if dLen2==0 else -(x1*dx+y1*dy+z1*dz)/dLen2
    t = max(0,min(1,t))
    cx,cy,cz = x1+dx*t, y1+dy*t, z1+dz*t
    return math.sqrt(cx*cx+cy*cy+cz*cz) < (R_E-2)

GS = [
    ('Мурманск', 68.97, 33.09, False),
    ('Салехард', 66.53, 66.60, False),
    ('Норильск', 69.35, 88.20, False),
    ('Тикси', 71.64, 128.87, False),
    ('Певек', 69.70, 170.31, False),
    ('Москва', 55.75, 37.62, True),
]

def gen_sats(raan_offsets, phase_offsets, inc=86.0, raan_base=(0,120,240)):
    sats = []
    for pi in range(3):
        raan = raan_base[pi] + raan_offsets[pi]
        for s in range(16):
            u0 = (360.0/16)*s + phase_offsets[pi]
            sats.append({'id': f'P{pi+1}-S{s+1}', 'plane': pi+1, 'raan': raan, 'inc': inc, 'u0': u0})
    return sats

def simulate(raan_offsets, phase_offsets, isl_range, min_elev=10.0, duration_h=24, step_s=120):
    total_steps = int(duration_h*3600/step_s)
    sats = gen_sats(raan_offsets, phase_offsets)
    gw = [g for g in GS if g[3]][0]
    gs_list = [g for g in GS if not g[3]]
    ok_counts = {g[0]: 0 for g in gs_list}
    for t in range(total_steps):
        tSec = t*step_s
        pos = {}
        for sat in sats:
            eci = satECI(sat['u0'], sat['raan'], sat['inc'], tSec)
            ecef = eciToEcef(eci, tSec)
            pos[sat['id']] = ecef
        vis = {}
        for g in GS:
            name, lat, lon, _ = g
            lst = []
            for sid, ecef in pos.items():
                elev, _ = elevationAngle(lat, lon, ecef)
                if elev >= min_elev:
                    lst.append(sid)
            vis[name] = lst
        ids = list(pos.keys())
        adj = {}
        for i in range(len(ids)):
            for j in range(i+1, len(ids)):
                pa, pb = pos[ids[i]], pos[ids[j]]
                dist = math.dist(pa, pb)
                if dist <= isl_range and not segBlocked(pa, pb):
                    adj.setdefault(ids[i], []).append((ids[j], dist))
                    adj.setdefault(ids[j], []).append((ids[i], dist))
        gw_vis = vis[gw[0]]
        for g in gs_list:
            name = g[0]
            v = vis[name]
            if not v or not gw_vis:
                continue
            # BFS reachability (unweighted, enough for availability check)
            from collections import deque
            dist = {sid: 0 for sid in v}
            dq = deque(v)
            visited = set(v)
            reached = False
            while dq:
                u = dq.popleft()
                if u in gw_vis:
                    reached = True
                    break
                for (w, d) in adj.get(u, []):
                    if w not in visited:
                        visited.add(w)
                        dq.append(w)
            if reached:
                ok_counts[name] += 1
    avail = {name: c/total_steps*100 for name, c in ok_counts.items()}
    return avail

if __name__ == '__main__':
    # baseline
    avail = simulate((0,0,0),(0,0,0), 3000)
    print('baseline', avail, 'min=', min(avail.values()), 'avg=', sum(avail.values())/len(avail))
