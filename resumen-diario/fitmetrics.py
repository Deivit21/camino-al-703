"""Métricas derivadas de un archivo .fit de actividad.

Además de los mensajes estándar, lee el mensaje no documentado 140 de Garmin
(métricas fisiológicas): campo 7 = VO2max x 65536 / 3.5, campo 9 = tiempo de
recuperación (min), campo 17 = condición de rendimiento al final (no oficial).
"""
import math
import pathlib
import statistics
import sys

try:
    import fitparse
except ImportError:  # en la nube no se puede instalar con pip: copia incluida en garmin/vendor (licencia MIT)
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "vendor"))
    import fitparse

INTENSITY = {4: "recovery", 5: "interval", 6: "other"}


def _zone_times(values, weights, floors):
    """floors = límites inferiores de Z2..Z5. Devuelve segundos por zona (Z1..Z5)."""
    out = [0.0] * 5
    for v, w in zip(values, weights):
        if v is None:
            continue
        z = sum(1 for f in floors if v >= f)
        out[z] += w
    return [round(x) for x in out]


def _rolling(vals, n):
    if len(vals) < n:
        return []
    s = sum(vals[:n])
    out = [s / n]
    for i in range(n, len(vals)):
        s += vals[i] - vals[i - n]
        out.append(s / n)
    return out


def _best(vals, n):
    r = _rolling(vals, n)
    return round(max(r)) if r else None


def _np(power):
    r = _rolling(power, 30)
    return round(statistics.fmean(x ** 4 for x in r) ** 0.25) if r else None


def parse(path, cfg):
    f = fitparse.FitFile(str(path))
    session, m140, laps, recs = {}, {}, [], []
    creator, hr_strap = None, False
    for msg in f.get_messages():
        name = msg.name
        if name == "device_info":
            v = msg.get_values()
            if v.get("device_index") == "creator":
                creator = v.get("manufacturer")
            if v.get("antplus_device_type") == "heart_rate" or v.get("ble_device_type") == "heart_rate":
                hr_strap = True
        elif name == "file_id" and creator is None:
            creator = msg.get_values().get("manufacturer")
        elif name == "session" and not session:
            session = {k: v for k, v in msg.get_values().items() if v is not None and not k.startswith("unknown")}
        elif name == "unknown_140" and not m140:
            m140 = {d.def_num: d.value for d in msg.fields if d.value is not None}
        elif name == "lap":
            v = msg.get_values()
            t, d = v.get("total_timer_time") or 0, v.get("total_distance") or 0
            intensity = v.get("intensity")
            laps.append({
                "t": round(t),
                "d": round(d),
                "hr": v.get("avg_heart_rate"),
                "hrmax": v.get("max_heart_rate"),
                "v": v.get("enhanced_avg_speed") or v.get("avg_speed") or (round(d / t, 3) if t and d else None),
                "p": v.get("avg_power"),
                "cad": v.get("avg_running_cadence") or v.get("avg_cadence"),
                # fitparse no conoce los valores nuevos del enum: 4 recovery, 5 interval, 6 other
                "int": INTENSITY.get(intensity, intensity),
                "step": v.get("wkt_step_index"),
            })
        elif name == "record":
            v = msg.get_values()
            recs.append((v.get("timestamp"), v.get("heart_rate"), v.get("enhanced_speed", v.get("speed")),
                         v.get("power"), v.get("cadence"), v.get("fractional_cadence"), v.get("temperature")))

    sport = session.get("sport")
    out = {"sport": sport, "sub_sport": session.get("sub_sport"),
           # sensor de FC: banda (ANT+/BLE), muñeca (reloj Garmin sin banda) o desconocido (p. ej. Zwift)
           "hr_sensor": "banda" if hr_strap else ("muñeca" if creator == "garmin" else None)}

    vo2 = m140.get(7)
    if vo2 and 20 < vo2 * 3.5 / 65536 < 95:
        out["vo2max"] = round(vo2 * 3.5 / 65536, 1)
    if m140.get(9) is not None:
        out["recovery_min"] = m140.get(9)
    if m140.get(17) is not None and -30 <= m140[17] <= 30:
        out["perf_condition"] = m140[17]

    if not recs:
        return out

    # pesos temporales (s) para cada registro, con tope para pausas
    ts = [r[0] for r in recs]
    w = [1.0] + [max(0.0, min(5.0, (b - a).total_seconds())) for a, b in zip(ts, ts[1:])]
    hr = [r[1] for r in recs]
    spd = [r[2] for r in recs]
    pw = [r[3] for r in recs]
    temps = [r[6] for r in recs if r[6] is not None]
    if temps:
        out["temp"] = round(statistics.fmean(temps), 1)

    is_run = sport in ("running", "walking", "hiking")
    is_bike = sport == "cycling"
    zones = cfg["zonas_fc_carrera"] if is_run else cfg["zonas_fc_bici"]
    out["hr_zones_s"] = _zone_times(hr, w, zones)

    if is_run:
        cad = [((r[4] or 0) + (r[5] or 0)) * 2 for r in recs if r[4]]
        if cad:
            out["cadence_spm"] = round(statistics.fmean(c for c in cad if c > 120), 1) if any(c > 120 for c in cad) else None

    has_power = sum(1 for p in pw if p) > len(pw) * 0.5
    if is_bike and has_power:
        p1 = [p or 0 for p in pw]  # asume 1 registro/s en rodillo
        out["np"] = _np(p1)
        out["avg_power"] = round(statistics.fmean(p1))
        out["best_power"] = {k: _best(p1, s) for k, s in (("1m", 60), ("5m", 300), ("20m", 1200), ("60m", 3600))}
        ftp = cfg.get("ftp")
        if ftp:
            out["power_zones_s"] = _zone_times(pw, w, [ftp * x for x in (0.56, 0.76, 0.91, 1.06)])
            if out["np"]:
                dur = sum(w)
                out["if"] = round(out["np"] / ftp, 2)
                out["tss"] = round(dur * out["np"] * out["if"] / (ftp * 3600) * 100)

    # tramo "en movimiento" tras los primeros 10 minutos (calentamiento)
    t0 = ts[0]
    idx = [i for i, t in enumerate(ts) if (t - t0).total_seconds() >= 600 and hr[i]
           and ((is_run and spd[i] and spd[i] > 1.2) or (is_bike and pw[i] and pw[i] > 30)
                or (not is_run and not is_bike))]
    if len(idx) > 600:
        def ratio(ii):
            hw = sum(w[i] for i in ii)
            h = sum(hr[i] * w[i] for i in ii) / hw
            if is_run:
                return sum(spd[i] * w[i] for i in ii) / hw * 60 / h  # m/min por latido
            if is_bike and has_power:
                return sum((pw[i] or 0) * w[i] for i in ii) / hw / h  # W por latido
            return None
        half = len(idx) // 2
        r1, r2 = ratio(idx[:half]), ratio(idx[half:])
        if r1 and r2:
            out["ef"] = round(ratio(idx), 3)
            out["decoupling_pct"] = round((r1 - r2) / r1 * 100, 1)
            hs = [hr[i] for i in idx]
            out["hr_drift"] = round(statistics.fmean(hs[half:]) - statistics.fmean(hs[:half]), 1)

    if is_run:
        # velocidad mediana con FC en la banda aeróbica de referencia (indicador de forma aeróbica)
        lo, hi = cfg["banda_ref_carrera"]
        band = [spd[i] for i in range(len(recs)) if hr[i] and lo <= hr[i] <= hi and spd[i] and spd[i] > 1.5
                and (ts[i] - t0).total_seconds() >= 600]
        if len(band) >= 300:
            v = statistics.median(band)
            out["pace_at_ref_hr"] = round(1000 / v)  # s/km
            out["ref_hr_samples_s"] = len(band)

    out["laps"] = laps[:40]
    return out


def fmt_pace(sec_per_km):
    if not sec_per_km or not math.isfinite(sec_per_km):
        return "—"
    m, s = divmod(round(sec_per_km), 60)
    return f"{m}:{s:02d}"
