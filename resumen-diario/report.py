"""Calcula los datos del informe diario a partir de garmin/data/garmin.db.

Uso:
  python garmin/report.py                 # informe de hoy (actividad de ayer + estado de esta mañana)
  python garmin/report.py --hoy 2026-10-25

Salida: garmin/informes/AAAA-MM-DD.json (datos + alertas automáticas) y AAAA-MM-DD.md (borrador legible).
La interpretación la añade después Claude en el campo "interpretacion" (ver publicar.py).
"""
import argparse
import collections
import datetime as dt
import json
import math
import pathlib
import sqlite3
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent
DB = ROOT / "data" / "garmin.db"
OUT = ROOT / "informes"

FAMILIA = {
    "running": "carrera", "trail_running": "carrera", "track_running": "carrera", "street_running": "carrera",
    "treadmill_running": "cinta",
    "cycling": "bici", "road_biking": "bici", "gravel_cycling": "bici", "mountain_biking": "bici",
    "virtual_ride": "rodillo", "indoor_cycling": "rodillo",
    "lap_swimming": "piscina", "open_water_swimming": "aguas_abiertas",
    "strength_training": "fuerza", "soccer": "futbol",
}
NOMBRE_FAMILIA = {"carrera": "Carrera", "cinta": "Cinta", "bici": "Bici", "rodillo": "Rodillo", "piscina": "Piscina",
                  "aguas_abiertas": "Aguas abiertas", "fuerza": "Fuerza", "futbol": "Fútbol", "otro": "Otro"}
INTENSIDAD = {"RECOVERY": "suave", "AEROBIC_BASE": "suave", "TEMPO": "tempo", "LACTATE_THRESHOLD": "umbral",
              "THRESHOLD": "umbral", "VO2MAX": "vo2", "ANAEROBIC_CAPACITY": "vo2", "SPRINT": "vo2"}
SENSACION = {0: "muy débil", 25: "débil", 50: "normal", 75: "fuerte", 100: "muy fuerte"}


def r(x, n=1):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(x, n)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def pace(s):
    if not s:
        return None
    m, s = divmod(round(s), 60)
    return f"{m}:{s:02d}"


# ---------------- carga de datos ----------------
def load():
    db = sqlite3.connect(DB)
    # actividades que no son del atleta (p. ej. el reloj lo llevaba otra persona): fuera de todos los cálculos
    excl = json.loads((ROOT / "config.json").read_text(encoding="utf-8")).get("excluir_actividades", {})
    acts = []
    for aid, start, date, typ, name, summary, fit in db.execute(
            "select id,start_local,date,type,name,summary,fit from activities order by start_local"):
        if str(aid) in excl:
            continue
        acts.append(norm(json.loads(summary), json.loads(fit) if fit else {}))
    daily = collections.defaultdict(dict)
    for date, kind, j in db.execute("select date,kind,json from daily where json!='null'"):
        daily[kind][date] = json.loads(j)
    meta = {k: v for k, v in db.execute("select key,value from meta")}
    return acts, daily, meta


def _merge(group):
    t = sum(l["t"] for l in group)
    d = sum(l.get("d") or 0 for l in group)
    wavg = lambda k: (sum((l.get(k) or 0) * l["t"] for l in group) / t) if all(l.get(k) for l in group) else None  # noqa: E731
    return {"t": t, "d": d, "v": d / t if d and t else None, "hr": r(wavg("hr"), 0), "p": r(wavg("p"), 0)}


def work_laps(laps, intensidad):
    laps = [l for l in laps if l.get("t")]
    tags = {l.get("int") for l in laps}
    if tags & {"warmup", "cooldown", "recovery", "rest"}:
        # entrenamiento estructurado: une las vueltas automáticas (cada km) que caen dentro de la misma serie
        reps, cur = [], []
        for l in laps:
            if l.get("int") in ("active", "interval") and (not cur or l.get("step") == cur[-1].get("step")):
                cur.append(l)
                continue
            if cur:
                reps.append(_merge(cur))
            cur = [l] if l.get("int") in ("active", "interval") else []
        if cur:
            reps.append(_merge(cur))
        return [x for x in reps if x["t"] >= 60]
    if intensidad not in ("umbral", "vo2", "tempo"):
        return []
    cand = [l for l in laps if l["t"] >= 60 and l.get("v")]
    if len(cand) < 3:
        return []
    lo, hi = min(l["v"] for l in cand), max(l["v"] for l in cand)
    if hi < lo * 1.15:
        return []
    return [l for l in cand if l["v"] >= (lo + hi) / 2]


def norm(s, f):
    typ = s["activityType"]["typeKey"]
    fam = FAMILIA.get(typ, "otro")
    dur = s.get("movingDuration") or s.get("duration") or 0
    dist = s.get("distance") or 0
    label = s.get("trainingEffectLabel")
    a = {
        "id": s["activityId"], "inicio": s["startTimeLocal"], "fecha": s["startTimeLocal"][:10],
        "hora": s["startTimeLocal"][11:16], "tipo": typ, "familia": fam, "nombre": s.get("activityName"),
        "dur_min": r(dur / 60), "dist_km": r(dist / 1000, 2) if dist else None,
        "fc_media": s.get("averageHR"), "fc_max": s.get("maxHR"),
        "carga": r(s.get("activityTrainingLoad"), 0), "te_aer": r(s.get("aerobicTrainingEffect")),
        "te_ana": r(s.get("anaerobicTrainingEffect")), "te_label": label,
        "intensidad": INTENSIDAD.get(label, "otra"),
        "rpe": (s.get("directWorkoutRpe") or 0) / 10 or None,
        "sensacion": SENSACION.get(s.get("directWorkoutFeel")),
        "temp": f.get("temp"), "bb_delta": s.get("differenceBodyBattery"), "sensor_fc": f.get("hr_sensor"),
        "kcal": s.get("calories"),
        "vo2max": f.get("vo2max"), "cond_rend": f.get("perf_condition"),
        "recup_h": r(f["recovery_min"] / 60, 0) if f.get("recovery_min") else None,
        "ef": f.get("ef"), "desacople": f.get("decoupling_pct"), "deriva_fc": f.get("hr_drift"),
        "zonas_fc_min": [r(x / 60, 0) for x in f["hr_zones_s"]] if f.get("hr_zones_s") else None,
    }
    if fam in ("carrera", "cinta") and dist:
        a["ritmo"] = r(dur / dist * 1000, 0)  # s/km
        a["ritmo_gap"] = r(1000 / s["avgGradeAdjustedSpeed"], 0) if s.get("avgGradeAdjustedSpeed") else None
        a["ritmo_fc_ref"] = f.get("pace_at_ref_hr")
        a["cadencia"] = f.get("cadence_spm") or r(s.get("averageRunningCadenceInStepsPerMinute"))
        a["potencia"] = s.get("avgPower")
        a["tcs_ms"] = r(s.get("avgGroundContactTime"), 0)
        a["osc_cm"] = r(s.get("avgVerticalOscillation"))
        a["mejor_1k"] = r(s.get("fastestSplit_1000"), 0)
        a["mejor_5k"] = r(s.get("fastestSplit_5000"), 0)
        a["mejor_10k"] = r(s.get("fastestSplit_10000"), 0)
    if fam in ("bici", "rodillo"):
        a["vel_kmh"] = r(dist / dur * 3.6) if dist and dur else None
        a["potencia"] = f.get("avg_power") or s.get("avgPower")
        a["np"] = f.get("np") or s.get("normPower")
        a["if"] = f.get("if")
        a["tss"] = f.get("tss")
        # con potencia, el IF clasifica mejor la sesión que la etiqueta de Garmin (basada en FC)
        if a["if"] and a["if"] >= 0.85:
            a["intensidad"] = "umbral"
        elif a["if"] and a["if"] >= 0.75 and a["intensidad"] == "suave":
            a["intensidad"] = "tempo"
        a["mejor_pot"] = f.get("best_power")
        a["zonas_pot_min"] = [r(x / 60, 0) for x in f["power_zones_s"]] if f.get("power_zones_s") else None
    if fam in ("piscina", "aguas_abiertas") and dist:
        a["ritmo_100m"] = r(dur / dist * 100, 0)
        a["swolf"] = s.get("averageSwolf")
        a["brazadas_largo"] = r(s.get("avgStrokes"))
    wl = work_laps(f.get("laps") or [], a["intensidad"]) if fam in ("carrera", "cinta", "rodillo", "bici") else []
    if len(wl) >= 2:
        def lv(l):
            if fam in ("carrera", "cinta"):
                return 1000 / l["v"] if l.get("v") else None
            return l.get("p")
        vals = [lv(l) for l in wl]
        a["series"] = {
            "n": len(wl), "dur_media_min": r(mean([l["t"] for l in wl]) / 60),
            "valores": [r(v, 0) for v in vals], "fc": [l.get("hr") for l in wl],
            "media": r(mean(vals), 0), "fc_media": r(mean([l.get("hr") for l in wl]), 0),
            "unidad": "s/km" if fam in ("carrera", "cinta") else "W",
        }
        if all(vals) and len(vals) >= 3:
            rest = mean(vals[1:])
            # en carrera menos es más rápido; en bici más vatios es más fuerte
            first_hot = vals[0] < rest * 0.97 if fam in ("carrera", "cinta") else vals[0] > rest * 1.03
            a["series"]["primera_rapida"] = first_hot
            a["series"]["caida_pct"] = r((vals[-1] - vals[0]) / vals[0] * 100)
    return a


# ---------------- comparación con sesiones similares ----------------
METRICAS = {
    "carrera": [("ritmo", "Ritmo", -1), ("fc_media", "FC media", 0), ("ef", "Eficiencia (m/min por latido)", 1),
                ("ritmo_fc_ref", "Ritmo a FC de referencia", -1), ("desacople", "Desacople FC/ritmo %", -1),
                ("cadencia", "Cadencia", 1), ("carga", "Carga", 0), ("te_aer", "TE aeróbico", 0),
                ("temp", "Temperatura", 0), ("dur_min", "Duración", 0)],
    "rodillo": [("potencia", "Potencia media", 1), ("np", "Potencia normalizada", 1), ("fc_media", "FC media", 0),
                ("ef", "Eficiencia (W por latido)", 1), ("desacople", "Desacople FC/potencia %", -1),
                ("carga", "Carga", 0), ("if", "IF", 0), ("dur_min", "Duración", 0)],
    "piscina": [("ritmo_100m", "Ritmo /100 m", -1), ("swolf", "SWOLF", -1), ("fc_media", "FC media", 0),
                ("carga", "Carga", 0), ("dist_km", "Distancia", 0)],
}
METRICAS["cinta"] = METRICAS["carrera"]
METRICAS["bici"] = METRICAS["rodillo"]
METRICAS["aguas_abiertas"] = METRICAS["piscina"]
DEFAULT_METRICAS = [("fc_media", "FC media", 0), ("carga", "Carga", 0), ("dur_min", "Duración", 0)]


def similares(a, acts):
    d0 = dt.date.fromisoformat(a["fecha"])
    pool = [b for b in acts if b["familia"] == a["familia"] and b["id"] != a["id"] and b["inicio"] < a["inicio"]
            and (d0 - dt.date.fromisoformat(b["fecha"])).days <= 365 and (b["dur_min"] or 0) >= 8]
    dur = a["dur_min"] or 1
    near = lambda b, k: (1 - k) <= (b["dur_min"] or 0) / dur <= (1 + k)  # noqa: E731
    pasos = [
        ("mismo tipo de sesión y duración parecida (±35 %)", lambda b: b["intensidad"] == a["intensidad"] and near(b, .35)),
        ("mismo tipo de sesión", lambda b: b["intensidad"] == a["intensidad"]),
        ("duración parecida (±35 %)", lambda b: near(b, .35)),
        ("mismo deporte", lambda b: True),
    ]
    # la FC de muñeca y la de banda no son comparables: si hay suficientes con el mismo sensor, solo esas
    mismo = [b for b in pool if b.get("sensor_fc") == a.get("sensor_fc")]
    for crit, fn in pasos:
        sel = [b for b in mismo if fn(b)]
        if len(sel) >= 3:
            crit += ", mismo sensor de FC"
            break
        sel = [b for b in pool if fn(b)]
        if len(sel) >= 3:
            break
    sel = sel[-10:]
    if not sel:
        return None
    sensores = collections.Counter(b.get("sensor_fc") or "desconocido" for b in sel)
    metr = METRICAS.get(a["familia"], DEFAULT_METRICAS)
    filas = []
    for key, label, sentido in metr:
        if key == "desacople" and (a["intensidad"] != "suave" or (a["dur_min"] or 0) < 40):
            continue
        vals = [b.get(key) for b in sel if b.get(key) is not None]
        v = a.get(key)
        if v is None or not vals:
            continue
        m = statistics.fmean(vals)
        allv = sorted(vals + [v], reverse=(sentido == 1))
        fila = {"clave": key, "metrica": label, "valor": v, "media": r(m, 3 if key == "ef" else 1),
                "min": min(vals), "max": max(vals), "n": len(vals), "sentido": sentido,
                "delta": r(v - m, 3 if key == "ef" else 1),
                "delta_pct": r((v - m) / m * 100) if m and key not in ("cond_rend", "desacople", "temp") else None}
        if sentido:
            fila["puesto"] = allv.index(v) + 1
            fila["de"] = len(allv)
        filas.append(fila)
    return {
        "criterio": crit, "n": len(sel), "sensores_fc": dict(sensores),
        "sesiones": [{k: b.get(k) for k in ("id", "fecha", "nombre", "dur_min", "dist_km", "ritmo", "potencia", "np",
                                            "fc_media", "ef", "desacople", "carga", "temp", "te_label", "ritmo_100m",
                                            "sensor_fc")}
                     for b in sel[::-1]],
        "metricas": filas,
    }


def mejores_marcas(a, acts):
    """Mejores parciales (1k/5k/10k) frente a los últimos 365 días."""
    if a["familia"] != "carrera":
        return []
    d0 = dt.date.fromisoformat(a["fecha"])
    prev = [b for b in acts if b["familia"] == "carrera" and b["inicio"] < a["inicio"]
            and (d0 - dt.date.fromisoformat(b["fecha"])).days <= 365]
    out = []
    for k, lab in (("mejor_1k", "1 km"), ("mejor_5k", "5 km"), ("mejor_10k", "10 km")):
        v = a.get(k)
        best = min((b[k] for b in prev if b.get(k)), default=None)
        if v and (best is None or v < best):
            out.append({"distancia": lab, "tiempo": v, "anterior": best})
    return out


# ---------------- carga de entrenamiento ----------------
def cargas(acts, ayer):
    dl = collections.defaultdict(float)
    dep = collections.defaultdict(lambda: collections.defaultdict(float))
    for a in acts:
        dl[a["fecha"]] += a["carga"] or 0
        dep[a["fecha"]][a["familia"]] += a["carga"] or 0
    d = dt.date.fromisoformat(min(dl)) if dl else ayer
    atl = ctl = 0.0
    serie = []
    ka, kc = 1 - math.exp(-1 / 7), 1 - math.exp(-1 / 42)
    while d <= ayer:
        l = dl.get(d.isoformat(), 0)
        atl += (l - atl) * ka
        ctl += (l - ctl) * kc
        serie.append((d, l, atl, ctl))
        d += dt.timedelta(days=1)
    last = {x[0]: x for x in serie}

    def suma(desde, hasta):
        return sum(dl.get((ayer - dt.timedelta(days=i)).isoformat(), 0) for i in range(desde, hasta))
    aguda = suma(0, 7)
    cronica = suma(0, 28) / 4
    ult7 = [dl.get((ayer - dt.timedelta(days=i)).isoformat(), 0) for i in range(7)]
    sd = statistics.pstdev(ult7)
    monot = statistics.fmean(ult7) / sd if sd > 0 else None

    # semanas lunes-domingo
    lunes = ayer - dt.timedelta(days=ayer.weekday())
    semanas = []
    for w in range(5, -1, -1):
        ini = lunes - dt.timedelta(weeks=w)
        tot, por, horas, km = 0, collections.defaultdict(float), 0.0, 0.0
        for a in acts:
            fd = dt.date.fromisoformat(a["fecha"])
            if ini <= fd <= min(ini + dt.timedelta(days=6), ayer):
                tot += a["carga"] or 0
                por[a["familia"]] += a["carga"] or 0
                horas += (a["dur_min"] or 0) / 60
                if a["familia"] in ("carrera", "cinta"):
                    km += a["dist_km"] or 0
        semanas.append({"lunes": ini.isoformat(), "carga": r(tot, 0), "horas": r(horas),
                        "km_carrera": r(km), "por_deporte": {k: r(v, 0) for k, v in por.items()},
                        "parcial": ini + dt.timedelta(days=6) > ayer})
    # distribución de intensidad en carrera, 28 días (zonas de FC del plan)
    z = [0] * 5
    for a in acts:
        if a["familia"] in ("carrera", "cinta") and a["zonas_fc_min"] and \
                (ayer - dt.date.fromisoformat(a["fecha"])).days < 28:
            z = [x + y for x, y in zip(z, a["zonas_fc_min"])]
    tz = sum(z) or 1
    hoy_row = last.get(ayer)
    return {
        "aguda_7d": r(aguda, 0), "cronica_semanal_28d": r(cronica, 0),
        "ratio": r(aguda / cronica, 2) if cronica else None,
        "atl": r(hoy_row[2], 0) if hoy_row else None, "ctl": r(hoy_row[3], 0) if hoy_row else None,
        "forma_tsb": r(hoy_row[3] - hoy_row[2], 0) if hoy_row else None,
        "ctl_hace_28d": r(last[ayer - dt.timedelta(days=28)][3], 0) if (ayer - dt.timedelta(days=28)) in last else None,
        "monotonia": r(monot, 2),
        "diaria_14d": [{"fecha": (ayer - dt.timedelta(days=i)).isoformat(),
                        "carga": r(dl.get((ayer - dt.timedelta(days=i)).isoformat(), 0), 0),
                        "por_deporte": {k: r(v, 0) for k, v in dep[(ayer - dt.timedelta(days=i)).isoformat()].items()}}
                       for i in range(13, -1, -1)],
        "semanas": semanas,
        "zonas_carrera_28d_pct": [r(x / tz * 100, 0) for x in z],
        "serie_ctl_atl_90d": [{"fecha": x[0].isoformat(), "atl": r(x[2], 0), "ctl": r(x[3], 0)}
                              for x in serie[-90:]],
    }


# ---------------- bienestar ----------------
def _sleep_ts(s, key):
    v = s.get(key)
    return dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc).replace(tzinfo=None) if v else None


def _bb_wake(daily, d):
    bb, sl = daily["bodybattery"].get(d), daily["sleep"].get(d)
    if not bb or not sl or not sl.get("sleepEndTimestampGMT"):
        return None
    arr = [x for x in bb.get("bodyBatteryValuesArray") or [] if x[1] is not None]
    end = sl["sleepEndTimestampGMT"]
    after = [x for x in arr if x[0] >= end - 15 * 60000]
    return after[0][1] if after else (arr[-1][1] if arr else None)


def _bb_sleep_gain(daily, d):
    bb = daily["bodybattery"].get(d) or {}
    ev = [e for e in bb.get("bodyBatteryActivityEvent") or [] if e.get("eventType") == "SLEEP"]
    return ev[-1].get("bodyBatteryImpact") if ev else None


def _hour(s, key):
    t = _sleep_ts(s, key)
    if not t:
        return None
    h = t.hour + t.minute / 60
    return h - 24 if key.startswith("sleepStart") and h > 15 else h  # 23:30 -> -0.5


SERIES = {
    # clave: (etiqueta, unidad, momento, extractor, sentido bueno)
    "hrv_noche": ("VFC (media nocturna)", "ms", "noche", lambda D, d: (D["hrv"].get(d) or {}).get("lastNightAvg"), 1),
    "hrv_7d": ("VFC (media 7 días)", "ms", "noche", lambda D, d: (D["hrv"].get(d) or {}).get("weeklyAvg"), 1),
    "fcr": ("FC en reposo", "ppm", "noche", lambda D, d: ((D["summary"].get(d) or {}).get("heartRate") or {}).get("restingValue"), -1),
    "fc_sueno": ("FC media durmiendo", "ppm", "noche", lambda D, d: (D["sleep"].get(d) or {}).get("avgHeartRate"), -1),
    "sueno_h": ("Sueño", "h", "noche", lambda D, d: r((D["sleep"].get(d) or {}).get("sleepTimeSeconds", 0) / 3600, 2) or None, 1),
    "sueno_score": ("Puntuación de sueño", "", "noche",
                    lambda D, d: (((D["sleep"].get(d) or {}).get("sleepScores") or {}).get("overall") or {}).get("value"), 1),
    "profundo_min": ("Sueño profundo", "min", "noche", lambda D, d: r((D["sleep"].get(d) or {}).get("deepSleepSeconds", 0) / 60, 0) or None, 1),
    "rem_min": ("Sueño REM", "min", "noche", lambda D, d: r((D["sleep"].get(d) or {}).get("remSleepSeconds", 0) / 60, 0) or None, 1),
    "despierto_min": ("Tiempo despierto", "min", "noche", lambda D, d: r((D["sleep"].get(d) or {}).get("awakeSleepSeconds", 0) / 60, 0) if D["sleep"].get(d) else None, -1),
    "estres_sueno": ("Estrés durante el sueño", "", "noche", lambda D, d: (D["sleep"].get(d) or {}).get("avgSleepStress"), -1),
    "resp_sueno": ("Respiración durmiendo", "rpm", "noche", lambda D, d: (D["sleep"].get(d) or {}).get("averageRespirationValue"), 0),
    "spo2_sueno": ("SpO2 durmiendo", "%", "noche", lambda D, d: (D["sleep"].get(d) or {}).get("averageSpO2Value"), 0),
    "hora_dormir": ("Hora de dormir", "h", "noche", lambda D, d: _hour(D["sleep"].get(d) or {}, "sleepStartTimestampLocal"), 0),
    "hora_despertar": ("Hora de despertar", "h", "noche", lambda D, d: _hour(D["sleep"].get(d) or {}, "sleepEndTimestampLocal"), 0),
    "bb_despertar": ("Body Battery al despertar", "", "noche", _bb_wake, 1),
    "bb_recarga": ("Body Battery recargada durmiendo", "", "noche", _bb_sleep_gain, 1),
    "estres_dia": ("Estrés medio (día)", "", "dia", lambda D, d: ((D["stress"].get(d) or {}).get("values") or {}).get("overallStressLevel"), -1),
    "estres_alto_min": ("Estrés alto", "min", "dia", lambda D, d: r(((D["stress"].get(d) or {}).get("values") or {}).get("highStressDuration", 0) / 60, 0) if D["stress"].get(d) else None, -1),
    "bb_min": ("Body Battery mínima", "", "dia", lambda D, d: ((D["summary"].get(d) or {}).get("bodyBattery") or {}).get("minValue"), 1),
    "pasos": ("Pasos", "", "dia", lambda D, d: (((D["summary"].get(d) or {}).get("movement") or {}).get("steps") or {}).get("value"), 0),
    "min_intensidad": ("Minutos de intensidad", "min", "dia",
                       lambda D, d: (lambda im: (im.get("moderate") or 0) + 2 * (im.get("vigorous") or 0) if im else None)(
                           (D["summary"].get(d) or {}).get("intensityMinutes") or {}), 0),
}


def bienestar(daily, hoy):
    ayer = hoy - dt.timedelta(days=1)
    out = {}
    for key, (label, unit, momento, fn, sentido) in SERIES.items():
        t = hoy if momento == "noche" else ayer
        def val(d):
            try:
                return fn(daily, d.isoformat())
            except (TypeError, KeyError, ZeroDivisionError):
                return None
        v = val(t)
        prev28 = [x for x in (val(t - dt.timedelta(days=i)) for i in range(1, 29)) if x is not None]
        prev7 = [x for x in (val(t - dt.timedelta(days=i)) for i in range(1, 8)) if x is not None]
        prev90 = [x for x in (val(t - dt.timedelta(days=i)) for i in range(1, 91)) if x is not None]
        m28 = statistics.fmean(prev28) if prev28 else None
        sd28 = statistics.pstdev(prev28) if len(prev28) >= 5 else None
        out[key] = {
            "etiqueta": label, "unidad": unit, "fecha": t.isoformat(), "valor": v, "sentido": sentido,
            "media7": r(statistics.fmean(prev7)) if prev7 else None, "media28": r(m28), "sd28": r(sd28, 2),
            "z": r((v - m28) / sd28, 2) if v is not None and sd28 else None,
            "min90": min(prev90) if prev90 else None, "max90": max(prev90) if prev90 else None,
            "percentil90": r(sum(1 for x in prev90 if x < v) / len(prev90) * 100, 0) if v is not None and prev90 else None,
            "serie14": [{"fecha": (t - dt.timedelta(days=i)).isoformat(), "v": val(t - dt.timedelta(days=i))}
                        for i in range(13, -1, -1)],
        }
    h = daily["hrv"].get(hoy.isoformat()) or {}
    out["_hrv_estado"] = {"estado": h.get("status"), "linea_base": h.get("baseline"), "frase": h.get("feedbackPhrase")}
    sl = daily["sleep"].get(hoy.isoformat()) or {}
    out["_sueno_extra"] = {"feedback": sl.get("sleepScoreFeedback"), "insight": sl.get("sleepScorePersonalizedInsight"),
                           "necesidad_min": (sl.get("sleepNeed") or {}).get("actual"),
                           "despertares": sl.get("awakeCount")}
    return out


def vo2(acts, hoy):
    pts = [(a["fecha"], a["vo2max"]) for a in acts if a.get("vo2max") and a["familia"] in ("carrera", "cinta")]
    if not pts:
        return None

    def at(days):
        lim = (hoy - dt.timedelta(days=days)).isoformat()
        p = [v for f, v in pts if f <= lim]
        return p[-1] if p else None
    return {"actual": pts[-1][1], "fecha": pts[-1][0], "hace_30d": at(30), "hace_90d": at(90),
            "max": max(v for _, v in pts), "serie": [{"fecha": f, "v": v} for f, v in pts[-40:]]}


# ---------------- alertas automáticas ----------------
def alertas(rep):
    A = []

    def add(nivel, area, texto):
        A.append({"nivel": nivel, "area": area, "texto": texto})
    b = rep["bienestar"]
    g = lambda k: b.get(k, {}).get("valor")  # noqa: E731
    hs = b["_hrv_estado"]
    base = hs.get("linea_base") or {}
    hrv = g("hrv_noche")
    if hrv is None:
        add("aviso", "datos", "No hay VFC de anoche (¿el reloj no ha sincronizado o no lo llevabas puesto?).")
    elif base.get("balancedLow"):
        if hrv < (base.get("lowUpper") or 0):
            add("alerta", "recuperación", f"VFC de anoche {hrv} ms, por debajo de tu rango bajo ({base['lowUpper']} ms).")
        elif hrv < base["balancedLow"]:
            add("aviso", "recuperación", f"VFC de anoche {hrv} ms, bajo tu rango habitual ({base['balancedLow']}–{base['balancedUpper']} ms).")
        elif hrv > base["balancedUpper"] * 1.12:
            add("info", "recuperación", f"VFC de anoche {hrv} ms, muy por encima de tu rango ({base['balancedUpper']} ms): suele ser buena señal, "
                "pero tras cargas muy altas puede indicar fatiga parasimpática.")
    if hs.get("estado") in ("UNBALANCED", "LOW", "POOR"):
        add("aviso", "recuperación", f"Estado de VFC de Garmin: {hs['estado']}.")
    fcr, m = g("fcr"), b["fcr"]["media28"]
    if fcr and m:
        if fcr >= m + 5:
            add("alerta", "recuperación", f"FC en reposo {fcr} ppm, +{r(fcr - m, 0)} sobre tu media de 28 días.")
        elif fcr >= m + 3:
            add("aviso", "recuperación", f"FC en reposo {fcr} ppm, +{r(fcr - m, 0)} sobre tu media de 28 días.")
    sh = g("sueno_h")
    if sh is None:
        add("aviso", "datos", "Sin datos de sueño de anoche.")
    elif sh < 5.5:
        add("alerta", "sueño", f"Solo {sh:.1f} h de sueño.")
    elif sh < 6.5:
        add("aviso", "sueño", f"Sueño corto: {sh:.1f} h.")
    sc = g("sueno_score")
    if sc is not None and sc < 60:
        add("aviso", "sueño", f"Puntuación de sueño baja ({sc}).")
    rs, mr = g("resp_sueno"), b["resp_sueno"]["media28"]
    fs, zfs = g("fc_sueno"), b["fc_sueno"]["z"]
    signos = 0
    if rs and mr and rs >= mr + 1.5:
        signos += 1
        add("aviso", "salud", f"Respiración nocturna {rs} rpm, por encima de lo habitual ({mr}). Es un marcador temprano de catarro o fatiga.")
    if zfs is not None and zfs >= 1.5:
        signos += 1
        add("aviso", "recuperación", f"FC durmiendo {fs} ppm, alta para ti (z={zfs}).")
    if hrv and base.get("balancedLow") and hrv < base["balancedLow"]:
        signos += 1
    if fcr and m and fcr >= m + 3:
        signos += 1
    if signos >= 2:
        add("alerta", "salud", "Varias señales a la vez (VFC, FC, respiración): posible fatiga acumulada o inicio de enfermedad. "
            "Mejor sesión suave o descanso hasta que se normalicen.")
    sp, msp = g("spo2_sueno"), b["spo2_sueno"]["media28"]
    if sp and msp and sp <= msp - 2:
        add("info", "salud", f"SpO2 nocturna {sp} %, algo por debajo de lo habitual ({msp} %).")
    z = b["estres_dia"]["z"]
    if z is not None and z >= 1.5:
        add("aviso", "estrés", f"Estrés diario alto para ti ({g('estres_dia')}, z={z}).")
    bbw = g("bb_despertar")
    if bbw is not None and bbw < 50:
        add("aviso", "recuperación", f"Body Battery al despertar baja ({bbw}).")
    hd, md = g("hora_dormir"), b["hora_dormir"]["media28"]
    if hd is not None and md is not None and hd - md >= 1.5:
        add("info", "sueño", "Te acostaste bastante más tarde de lo habitual.")

    c = rep["carga"]
    ratio = c.get("ratio")
    if ratio:
        if ratio > 1.5:
            add("alerta", "carga", f"Ratio carga aguda/crónica {ratio}: subida brusca de carga (>1,5), riesgo de lesión elevado.")
        elif ratio > 1.3:
            add("aviso", "carga", f"Ratio carga aguda/crónica {ratio}: por encima de la zona óptima (0,8–1,3).")
        elif ratio < 0.8:
            add("info", "carga", f"Ratio carga aguda/crónica {ratio}: carga reciente baja (descarga o pérdida de forma si se prolonga).")
    if c.get("monotonia") and c["monotonia"] > 2:
        add("aviso", "carga", f"Monotonía {c['monotonia']}: días muy parecidos entre sí, con poco contraste entre duros y suaves.")

    for a in rep["actividades"]:
        nm = f"{a['nombre']} ({a['hora']})"
        comp = {m["clave"]: m for m in (a.get("comparacion") or {}).get("metricas", [])}
        ef = comp.get("ef")
        if ef and ef.get("delta_pct") is not None and ef["n"] >= 3:
            if ef["delta_pct"] <= -5:
                add("aviso", "actividad", f"{nm}: eficiencia {abs(ef['delta_pct'])} % peor que en tus sesiones similares.")
            elif ef["delta_pct"] >= 5:
                add("info", "actividad", f"{nm}: eficiencia {ef['delta_pct']} % mejor que en tus sesiones similares.")
        if a.get("desacople") is not None and a["intensidad"] == "suave" and (a["dur_min"] or 0) >= 45 and a["desacople"] > 5:
            add("aviso", "actividad", f"{nm}: desacople FC/ritmo de {a['desacople']} % (>5 %): la FC subió con el tiempo a ritmo constante "
                "(calor, deshidratación o falta de base aeróbica).")
        if a["familia"] == "carrera" and a["intensidad"] == "suave" and a.get("fc_media") and a["fc_media"] > rep["_cfg"]["zonas_fc_carrera"][1]:
            add("aviso", "actividad", f"{nm}: FC media {a['fc_media']} en un rodaje suave, por encima de tu Z2 ({rep['_cfg']['zonas_fc_carrera'][1]}).")
        s = a.get("series")
        if s and s.get("primera_rapida"):
            add("aviso", "actividad", f"{nm}: la primera serie salió más rápida que el resto ({pace(s['valores'][0]) if s['unidad'] == 's/km' else s['valores'][0]}"
                f" frente a {pace(mean(s['valores'][1:])) if s['unidad'] == 's/km' else r(mean(s['valores'][1:]), 0)}).")
        if a["familia"] == "carrera" and a.get("cadencia") and a["cadencia"] < 148:
            add("info", "actividad", f"{nm}: cadencia {a['cadencia']} ppm, por debajo del objetivo (150–152).")
        for mm in a.get("mejores_marcas", []):
            add("info", "actividad", f"{nm}: mejor {mm['distancia']} del último año ({pace(mm['tiempo']) if mm['distancia'] == '1 km' else dt.timedelta(seconds=mm['tiempo'])}).")
    v = rep.get("vo2max")
    if v and v.get("hace_30d") and abs(v["actual"] - v["hace_30d"]) >= 1:
        add("info", "forma", f"VO2max {v['actual']} ({'+' if v['actual'] > v['hace_30d'] else ''}{r(v['actual'] - v['hace_30d'])} en 30 días).")
    orden = {"alerta": 0, "aviso": 1, "info": 2}
    return sorted(A, key=lambda x: orden[x["nivel"]])


# ---------------- informe ----------------
def informes_previos(hoy):
    ids = set()
    for p in OUT.glob("????-??-??.json"):
        if p.stem >= hoy.isoformat():
            continue
        try:
            ids |= {a["id"] for a in json.loads(p.read_text(encoding="utf-8")).get("actividades", [])}
        except (ValueError, KeyError):
            pass
    return ids


def build(hoy):
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    acts, daily, meta = load()
    ayer = hoy - dt.timedelta(days=1)
    del_dia = [a for a in acts if a["fecha"] == ayer.isoformat()]
    nota_act = None
    if not del_dia:
        # si ayer fue descanso, se analiza el último día con actividad (de los 3 anteriores), solo si ningún
        # informe anterior lo había comentado; nunca se retrocede a sesiones más antiguas
        vistos = informes_previos(hoy)
        prev = [a for a in acts if a["fecha"] < ayer.isoformat() and (ayer - dt.date.fromisoformat(a["fecha"])).days <= 3]
        if prev:
            f = prev[-1]["fecha"]
            dia = [a for a in prev if a["fecha"] == f]
            if not any(a["id"] in vistos for a in dia):
                del_dia = dia
                nota_act = f"Ayer no hubo actividad; se analiza la última sesión ({f}), que no había salido en ningún informe."
    for a in del_dia:
        a["comparacion"] = similares(a, acts)
        a["mejores_marcas"] = mejores_marcas(a, acts)
    ultima = next((a for a in reversed(acts) if a["fecha"] <= ayer.isoformat()), None)
    rep = {
        "fecha": hoy.isoformat(), "ayer": ayer.isoformat(),
        "generado": dt.datetime.now().isoformat(timespec="minutes"), "ultima_sync": meta.get("last_sync"),
        "actividades": del_dia, "nota_actividades": nota_act,
        "dias_sin_actividad": (ayer - dt.date.fromisoformat(ultima["fecha"])).days if ultima else None,
        "carga": cargas(acts, ayer),
        "bienestar": bienestar(daily, hoy),
        "vo2max": vo2(acts, hoy),
        "edad_fitness": (daily["fitnessage"].get(hoy.isoformat()) or {}).get("fitnessAge"),
        "config": {"ftp": cfg["ftp"], "zonas_fc_carrera": cfg["zonas_fc_carrera"]},
        "_cfg": cfg,
    }
    rep["alertas"] = alertas(rep)
    del rep["_cfg"]
    return rep


def fmt_val(key, v):
    if v is None:
        return "—"
    if key in ("ritmo", "ritmo_fc_ref", "ritmo_gap"):
        return pace(v) + "/km"
    if key == "ritmo_100m":
        return pace(v) + "/100m"
    if key in ("hora_dormir", "hora_despertar"):
        h = v % 24
        return f"{int(h):02d}:{round(h % 1 * 60):02d}"
    if key == "ef":
        return f"{v:.3f}"
    return str(v)


def markdown(rep):
    L = [f"# Datos del informe {rep['fecha']}", ""]
    if rep["nota_actividades"]:
        L += [f"_{rep['nota_actividades']}_", ""]
    if not rep["actividades"]:
        L += [f"**Ayer ({rep['ayer']}) no hubo actividad registrada.** Días desde la última: {rep['dias_sin_actividad']}", ""]
    for a in rep["actividades"]:
        L += [f"## {a['fecha']} {a['hora']} · {a['nombre']} ({NOMBRE_FAMILIA[a['familia']]}, {a['intensidad']}, Garmin: {a['te_label']})", ""]
        bits = [f"{a['dur_min']} min"]
        if a.get("dist_km"):
            bits.append(f"{a['dist_km']} km")
        if a.get("ritmo"):
            bits.append(f"ritmo {pace(a['ritmo'])}/km")
        if a.get("np"):
            bits.append(f"{a['potencia']} W medios · NP {a['np']} W · IF {a.get('if')}")
        if a.get("mejor_pot"):
            bits.append("mejor potencia " + ", ".join(f"{k} {v} W" for k, v in a["mejor_pot"].items() if v))
        bits += [f"FC {a['fc_media']}/{a['fc_max']}", f"carga {a['carga']}", f"TE {a['te_aer']}/{a['te_ana']}"]
        if a.get("ef"):
            bits.append(f"EF {a['ef']}")
        if a.get("desacople") is not None:
            bits.append(f"desacople {a['desacople']} %")
        if a.get("cadencia"):
            bits.append(f"cadencia {a['cadencia']}")
        if a.get("vo2max"):
            bits.append(f"VO2max {a['vo2max']}")
        if a.get("temp"):
            bits.append(f"{a['temp']} °C")
        if a.get("recup_h"):
            bits.append(f"recuperación Garmin {a['recup_h']} h")
        if a.get("sensor_fc"):
            bits.append(f"FC de {a['sensor_fc']}")
        if a.get("rpe"):
            bits.append(f"RPE {a['rpe']}")
        if a.get("sensacion"):
            bits.append(f"sensación {a['sensacion']}")
        L += [" · ".join(bits), ""]
        if a.get("zonas_fc_min"):
            L += ["Zonas FC (min): " + " · ".join(f"Z{i + 1} {m}" for i, m in enumerate(a["zonas_fc_min"])), ""]
        if a.get("series"):
            s = a["series"]
            vals = [(pace(v) if s["unidad"] == "s/km" else f"{v} W") if v else "—" for v in s["valores"]]
            L += [f"Series: {s['n']} × {s['dur_media_min']} min → " + ", ".join(vals) + f" · FC {s['fc']}", ""]
        c = a.get("comparacion")
        if c:
            L += [f"Comparación con {c['n']} sesiones ({c['criterio']}):", "",
                  "| Métrica | Hoy | Media | Δ | Puesto |", "|---|---|---|---|---|"]
            for m in c["metricas"]:
                L.append(f"| {m['metrica']} | {fmt_val(m['clave'], m['valor'])} | {fmt_val(m['clave'], m['media'])} | "
                         f"{m['delta']} ({m['delta_pct']} %) | {m.get('puesto', '')}/{m.get('de', '')} |")
            L.append("")
    b = rep["bienestar"]
    L += ["## Estado", "", "| Métrica | Valor | Media 7d | Media 28d | z | Rango 90d |", "|---|---|---|---|---|---|"]
    for k, v in b.items():
        if k.startswith("_"):
            continue
        L.append(f"| {v['etiqueta']} ({v['fecha'][5:]}) | {fmt_val(k, v['valor'])} | {fmt_val(k, v['media7'])} | "
                 f"{fmt_val(k, v['media28'])} | {v['z'] if v['z'] is not None else '—'} | "
                 f"{fmt_val(k, v['min90'])}–{fmt_val(k, v['max90'])} |")
    L += ["", f"VFC Garmin: {b['_hrv_estado']}", ""]
    c = rep["carga"]
    L += ["## Carga", "",
          f"Aguda 7d {c['aguda_7d']} · crónica (media semanal 28d) {c['cronica_semanal_28d']} · ratio {c['ratio']} · "
          f"ATL {c['atl']} · CTL {c['ctl']} (hace 28 d: {c['ctl_hace_28d']}) · TSB {c['forma_tsb']} · monotonía {c['monotonia']}", "",
          "Semanas: " + " | ".join(f"{w['lunes'][5:]}: {w['carga']} ({w['horas']} h, {w['km_carrera']} km)" for w in c["semanas"]), "",
          f"Zonas FC carrera 28 d (%): {c['zonas_carrera_28d_pct']}", ""]
    if rep["vo2max"]:
        v = rep["vo2max"]
        L += [f"VO2max {v['actual']} ({v['fecha']}) · hace 30 d {v['hace_30d']} · hace 90 d {v['hace_90d']} · máx {v['max']}", ""]
    L += ["## Alertas automáticas", ""] + [f"- [{x['nivel']}] {x['area']}: {x['texto']}" for x in rep["alertas"]]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hoy", default=dt.date.today().isoformat())
    args = ap.parse_args()
    hoy = dt.date.fromisoformat(args.hoy)
    OUT.mkdir(parents=True, exist_ok=True)
    rep = build(hoy)
    p = OUT / f"{hoy.isoformat()}.json"
    if p.exists():  # conserva la interpretación si ya se había escrito
        old = json.loads(p.read_text(encoding="utf-8"))
        if old.get("interpretacion"):
            rep["interpretacion"] = old["interpretacion"]
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT / f"{hoy.isoformat()}.md").write_text(markdown(rep), encoding="utf-8")
    print(p)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
