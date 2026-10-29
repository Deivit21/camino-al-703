"""Exporta las tablas del panel "Resumen diario de forma" a garmin/dash/*.json.

Uso: python garmin/export_dash.py
Lee garmin.db y garmin/informes/AAAA-MM-DD.json (+ .interp.json con la interpretación de Claude).
"""
import datetime as dt
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import report as R  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "dash"

GRUPO = {
    "hrv_noche": "recuperacion", "hrv_7d": "recuperacion", "fcr": "recuperacion", "fc_sueno": "recuperacion",
    "bb_despertar": "recuperacion", "bb_recarga": "recuperacion",
    "sueno_h": "sueno", "sueno_score": "sueno", "profundo_min": "sueno", "rem_min": "sueno",
    "despierto_min": "sueno", "estres_sueno": "sueno", "hora_dormir": "sueno", "hora_despertar": "sueno",
    "resp_sueno": "salud", "spo2_sueno": "salud",
    "estres_dia": "dia", "estres_alto_min": "dia", "bb_min": "dia", "pasos": "dia", "min_intensidad": "dia",
}


def txt(key, v):
    if v is None:
        return None
    if key in ("ritmo", "ritmo_fc_ref", "ritmo_gap"):
        return R.pace(v)
    if key == "ritmo_100m":
        return R.pace(v)
    if key in ("hora_dormir", "hora_despertar"):
        h = v % 24
        m = round(h % 1 * 60)
        return f"{int(h) + (m == 60):02d}:{m % 60:02d}"
    if key == "sueno_h":
        h, m = divmod(round(v * 60), 60)
        return f"{h} h {m:02d}"
    if key == "ef":
        return f"{v:.3f}".replace(".", ",")
    if key in ("pasos", "kcal"):
        return f"{round(v):,}".replace(",", ".")
    if key in ("ratio", "if", "monotonia"):
        return f"{v:.2f}".replace(".", ",")
    if key in ("dist_km",):
        return f"{v:.2f}".replace(".", ",")
    if isinstance(v, float):
        s = f"{v:.1f}".rstrip("0").rstrip(".") if abs(v) < 100 else str(round(v))
        return s.replace(".", ",")
    return str(v)


def dtxt(key, d):
    if d is None:
        return None
    if key in ("ritmo", "ritmo_fc_ref", "ritmo_100m"):
        return ("+" if d > 0 else "−") + R.pace(abs(d))
    if key in ("hora_dormir", "hora_despertar", "sueno_h"):
        m = round(d * 60)
        return ("+" if m >= 0 else "−") + f"{abs(m)} min"
    s = txt(key, abs(d))
    return ("+" if d >= 0 else "−") + s


def dur_txt(m):
    if m is None:
        return None
    h, mm = divmod(round(m), 60)
    return f"{h} h {mm:02d} min" if h else f"{mm} min"


def diario(daily, first, last):
    rows = []
    for key, (label, unit, momento, fn, sentido) in R.SERIES.items():
        vals = {}
        d = first
        while d <= last:
            try:
                vals[d] = fn(daily, d.isoformat())
            except (TypeError, KeyError, ZeroDivisionError):
                vals[d] = None
            d += dt.timedelta(days=1)
        for d, v in vals.items():
            if v is None:
                continue
            prev = lambda n: [x for x in (vals.get(d - dt.timedelta(days=i)) for i in range(1, n + 1)) if x is not None]  # noqa: E731
            p28, p90 = prev(28), prev(90)
            m28 = statistics.fmean(p28) if p28 else None
            sd = statistics.pstdev(p28) if len(p28) >= 5 else None
            fi = d if momento == "noche" else d + dt.timedelta(days=1)
            delta = v - m28 if m28 is not None else None
            # etiquetas, unidades y sentido de cada clave van como constantes en la página
            rows.append({
                "fecha": d.isoformat(), "fecha_informe": fi.isoformat(), "clave": key,
                "valor": R.r(v, 2) if isinstance(v, float) else v, "valor_txt": txt(key, v),
                "media28": R.r(m28, 2), "media28_txt": txt(key, m28), "sd28": R.r(sd, 2),
                "z": R.r(delta / sd, 2) if sd and delta is not None else None,
                "delta28": R.r(delta, 2), "delta28_txt": dtxt(key, delta) if delta is not None else None,
                "min90": min(p90) if p90 else None, "max90": max(p90) if p90 else None,
            })
            if key == "hrv_noche":  # rango "equilibrado" que calcula Garmin
                base = (daily["hrv"].get(d.isoformat()) or {}).get("baseline") or {}
                rows[-1]["banda_lo"] = base.get("balancedLow")
                rows[-1]["banda_hi"] = base.get("balancedUpper")
    return rows


def carga(acts, first, last):
    by = {}
    for a in acts:
        f = a["fecha"]
        by.setdefault(f, {}).setdefault(a["familia"], 0)
        by[f][a["familia"]] += a["carga"] or 0
    rows = []
    c = R.cargas(acts, last)
    serie = {x["fecha"]: x for x in c["serie_ctl_atl_90d"]}
    # serie completa ATL/CTL para todo el periodo
    import math
    ka, kc = 1 - math.exp(-1 / 7), 1 - math.exp(-1 / 42)
    atl = ctl = 0.0
    d0 = dt.date.fromisoformat(min(by)) if by else first
    d = d0
    hist = []
    while d <= last:
        day = by.get(d.isoformat(), {})
        l = sum(day.values())
        atl += (l - atl) * ka
        ctl += (l - ctl) * kc
        hist.append(l)
        a7 = sum(hist[-7:])
        c28 = sum(hist[-28:]) / 4
        sd = statistics.pstdev(hist[-7:])
        if d >= first:
            rows.append({
                "fecha": d.isoformat(), "carga": round(l),
                "carrera": round(day.get("carrera", 0) + day.get("cinta", 0)),
                "bici": round(day.get("rodillo", 0) + day.get("bici", 0)),
                "natacion": round(day.get("piscina", 0) + day.get("aguas_abiertas", 0)),
                "otros": round(l - sum(day.get(k, 0) for k in ("carrera", "cinta", "rodillo", "bici", "piscina", "aguas_abiertas"))),
                "aguda7": round(a7), "cronica_sem": round(c28), "ratio": R.r(a7 / c28, 2) if c28 else None,
                "atl": round(atl), "ctl": round(ctl), "tsb": round(ctl - atl),
                "monotonia": R.r(statistics.fmean(hist[-7:]) / sd, 2) if sd > 0 else None,
            })
            rows[-1]["ratio_txt"] = txt("ratio", rows[-1]["ratio"])
            rows[-1]["monotonia_txt"] = txt("monotonia", rows[-1]["monotonia"])
        d += dt.timedelta(days=1)
    del serie
    return rows


def actividades(acts):
    rows, comps, reps = [], [], []
    for a in acts:
        c = R.similares(a, acts)
        mm = R.mejores_marcas(a, acts)
        row = {k: a.get(k) for k in (
            "id", "fecha", "hora", "inicio", "tipo", "familia", "nombre", "intensidad", "te_label", "dur_min",
            "dist_km", "ritmo", "ritmo_fc_ref", "vel_kmh", "potencia", "np", "if", "tss", "fc_media", "fc_max",
            "carga", "te_aer", "te_ana", "ef", "desacople", "deriva_fc", "cadencia", "vo2max", "cond_rend",
            "recup_h", "temp", "rpe", "sensacion", "sensor_fc", "ritmo_100m", "swolf", "bb_delta", "kcal")}
        row["id"] = str(a["id"])
        row["familia_txt"] = R.NOMBRE_FAMILIA.get(a["familia"], a["familia"])
        row["dur_txt"] = dur_txt(a.get("dur_min"))
        row["ritmo_txt"] = txt("ritmo", a.get("ritmo"))
        row["ritmo_fc_ref_txt"] = txt("ritmo", a.get("ritmo_fc_ref"))
        row["ritmo_100m_txt"] = txt("ritmo", a.get("ritmo_100m"))
        row["ef_txt"] = txt("ef", a.get("ef"))
        for k in ("dist_km", "if", "te_aer", "te_ana", "vo2max", "desacople", "temp", "vel_kmh", "cadencia", "kcal"):
            row[f"{k}_txt"] = txt(k, a.get(k))
        for i, m in enumerate(a.get("zonas_fc_min") or []):
            row[f"z{i + 1}"] = m
        row["criterio"] = c["criterio"] if c else None
        row["n_similares"] = c["n"] if c else 0
        row["mejores"] = "; ".join(f"mejor {x['distancia']} del año" for x in mm) or None
        rows.append(row)
        if c:
            for i, m in enumerate(c["metricas"]):
                k = m["clave"]
                comps.append({
                    "actividad_id": str(a["id"]), "orden": i, "clave": k, "metrica": m["metrica"],
                    "valor": m["valor"], "valor_txt": txt(k, m["valor"]), "media": m["media"],
                    "media_txt": txt(k, m["media"]), "n": m["n"], "delta": m["delta"],
                    "delta_txt": dtxt(k, m["delta"]), "delta_pct": m["delta_pct"],
                    "puesto": m.get("puesto"), "de": m.get("de"), "sentido": m["sentido"],
                    "puesto_txt": f"{m['puesto']}.º de {m['de']}" if m.get("puesto") else None,
                })
            for i, b in enumerate(c["sesiones"]):
                comps.append({"actividad_id": str(a["id"]), "orden": 100 + i, "clave": f"_sim{i + 1}",
                              "metrica": b["fecha"], "similar_id": str(b["id"]), "valor": b.get("ef"),
                              "valor_txt": b.get("nombre")})
        s = a.get("series")
        if s:
            for i, (v, f) in enumerate(zip(s["valores"], s["fc"])):
                reps.append({"actividad_id": str(a["id"]), "serie": i + 1, "valor": v,
                             "valor_txt": (R.pace(v) if s["unidad"] == "s/km" else f"{v} W") if v else None,
                             "fc": f, "unidad": s["unidad"], "dur_media_min": s["dur_media_min"]})
    return rows, comps, reps


def informes():
    rows = []
    for p in sorted((ROOT / "informes").glob("????-??-??.json")):
        rep = json.loads(p.read_text(encoding="utf-8"))
        ip = p.with_suffix(".interp.json")
        it = json.loads(ip.read_text(encoding="utf-8")) if ip.exists() else {}
        rows.append({
            "fecha": rep["fecha"], "ayer": rep["ayer"],
            "titular": it.get("titular"), "estado": it.get("estado"),
            "resumen": it.get("resumen"), "actividad": it.get("actividad"),
            "estado_fisico": it.get("estado_fisico"), "carga": it.get("carga"),
            "anomalias": "\n".join(it.get("anomalias") or []) or None,
            "recomendacion": it.get("recomendacion"),
            "actividades": ",".join(str(a["id"]) for a in rep["actividades"]) or None,
            "nota_actividades": rep.get("nota_actividades"),
            "dias_sin_actividad": rep.get("dias_sin_actividad"),
            "alertas": json.dumps(rep.get("alertas") or [], ensure_ascii=False),
            "generado": rep.get("generado"),
        })
    return rows


def forma_web():
    """Resumen compacto del último informe para la web Camino al 70.3 (colección `forma`, doc = fecha)."""
    ps = sorted((ROOT / "informes").glob("????-??-??.json"))
    if not ps:
        return None
    rep = json.loads(ps[-1].read_text(encoding="utf-8"))
    ip = ps[-1].with_suffix(".interp.json")
    it = json.loads(ip.read_text(encoding="utf-8")) if ip.exists() else {}
    b = rep["bienestar"]

    def met(label, key, unit):
        x = b.get(key) or {}
        v, m, z = x.get("valor"), x.get("media28"), x.get("z")
        if v is None:
            return None
        d = v - m if m is not None else None
        tono = ""
        if d is not None and z is not None and abs(z) >= 1 and x.get("sentido"):
            tono = "good" if (d > 0) == (x["sentido"] > 0) else "bad"
        return {"k": label, "v": txt(key, v), "u": unit, "d": f"{dtxt(key, d)} vs media" if d is not None else "",
                "tono": tono}
    metricas = [met("VFC", "hrv_noche", "ms"), met("FC reposo", "fcr", "ppm"), met("Sueño", "sueno_h", ""),
                met("Puntuación sueño", "sueno_score", ""), met("Body Battery", "bb_despertar", ""),
                met("Estrés ayer", "estres_dia", "")]
    c = rep["carga"]
    if c.get("ratio") is not None:
        metricas.append({"k": "Ratio de carga", "v": txt("ratio", c["ratio"]), "u": "", "d": "óptimo 0,8–1,3",
                         "tono": "bad" if c["ratio"] > 1.3 or c["ratio"] < 0.8 else ""})
    if c.get("ctl") is not None:
        d = c["ctl"] - c["ctl_hace_28d"] if c.get("ctl_hace_28d") is not None else None
        metricas.append({"k": "Forma de fondo", "v": str(round(c["ctl"])), "u": "",
                         "d": f"{'+' if d >= 0 else '−'}{abs(round(d))} en 28 días" if d is not None else "", "tono": ""})
    ayer = []
    for a in rep["actividades"]:
        bits = [dur_txt(a.get("dur_min"))]
        if a.get("dist_km"):
            bits.append(txt("dist_km", a["dist_km"]) + " km")
        if a.get("ritmo"):
            bits.append(R.pace(a["ritmo"]) + "/km")
        elif a.get("np"):
            bits.append(f"NP {a['np']} W")
        if a.get("fc_media"):
            bits.append(f"FC {round(a['fc_media'])}")
        if a.get("carga") is not None:
            bits.append(f"carga {round(a['carga'])}")
        ayer.append({"fecha": a["fecha"], "nombre": a.get("nombre") or R.NOMBRE_FAMILIA.get(a["familia"], ""),
                     "linea": " · ".join(bits)})
    return {"fecha": rep["fecha"], "titular": it.get("titular"), "estado": it.get("estado"),
            "resumen": it.get("resumen"), "recomendacion": it.get("recomendacion"),
            "anomalias": it.get("anomalias") or [], "actividad": it.get("actividad"),
            "estado_fisico": it.get("estado_fisico"), "carga": it.get("carga"),
            "metricas": [m for m in metricas if m], "ayer": ayer, "nota_actividades": rep.get("nota_actividades"),
            "generado": rep.get("generado")}


def main():
    acts, daily, meta = R.load()
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    first = dt.date.fromisoformat(cfg["inicio_historial"])
    last = dt.date.today()
    OUT.mkdir(exist_ok=True)
    a, c, s = actividades(acts)
    tablas = {"informes": informes(), "diario": diario(daily, first, last),
              "carga": carga(acts, first, last - dt.timedelta(days=1)),
              "actividades": a, "comparaciones": c, "series": s}
    for name, rows in tablas.items():
        (OUT / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str),
                                          encoding="utf-8")
    fw = forma_web()
    if fw:  # documento para la web del plan: garmin/dash/forma_web.json → colección `forma`, doc = fecha
        (OUT / "forma_web.json").write_text(json.dumps(fw, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: len(v) for k, v in tablas.items()}))


if __name__ == "__main__":
    main()
