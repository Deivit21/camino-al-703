"""Sincroniza la cuenta de Garmin con la base local garmin/data/garmin.db.

Uso:
  python garmin/sync.py            # incremental (actividades nuevas + últimos días)
  python garmin/sync.py --full     # todo el historial desde config.inicio_historial
  python garmin/sync.py --reparse  # recalcula las métricas de todos los .fit (p. ej. tras cambiar FTP o zonas)
"""
import argparse
import datetime as dt
import json
import pathlib
import sqlite3
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fitmetrics  # noqa: E402
from gapi import Garmin, GarminError  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent
DB = ROOT / "data" / "garmin.db"
FITDIR = ROOT.parent / "fit" / "api"
REFRESH_DAYS = 3  # los últimos días se vuelven a pedir siempre (Garmin los completa tarde)

SCHEMA = """
create table if not exists activities(
  id integer primary key, start_local text, date text, type text, name text,
  summary text, fit_status text, fit text, updated_at text);
create table if not exists daily(
  date text, kind text, json text, fetched_at text, primary key(date, kind));
create table if not exists meta(key text primary key, value text);
"""

# tipo -> (función del cliente, campo con la fecha, días por petición)
DAILY = {
    "summary": ("daily_summaries", "calendarDate", 90),
    "sleep": ("sleep_range", "calendarDate", 28),
    "hrv": ("hrv_range", "calendarDate", 90),
    "stress": ("stress_range", "calendarDate", 90),
    "bodybattery": ("body_battery_range", "date", 90),
    "weight": ("weight_range", "summaryDate", 365),
}


def load_cfg():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)
    return db


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def days(a, b):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


# ---------------- actividades ----------------
def sync_activities(g, db, full):
    known = {r[0] for r in db.execute("select id from activities")}
    cutoff = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    new = upd = 0
    start = 0
    while True:
        page = g.activities(start=start, limit=100)
        if not page:
            break
        for a in page:
            aid = a["activityId"]
            row = (aid, a["startTimeLocal"], a["startTimeLocal"][:10], a["activityType"]["typeKey"],
                   a.get("activityName"), json.dumps(a), now())
            if aid in known:
                db.execute("update activities set start_local=?, date=?, type=?, name=?, summary=?, updated_at=? where id=?",
                           row[1:] + (aid,))
                upd += 1
            else:
                db.execute("insert into activities(id,start_local,date,type,name,summary,updated_at) values(?,?,?,?,?,?,?)", row)
                new += 1
        oldest = page[-1]["startTimeLocal"][:10]
        if len(page) < 100 or (not full and oldest < cutoff and all(a["activityId"] in known for a in page)):
            break
        start += 100
    db.commit()
    return new, upd


def sync_fits(g, db, cfg, reparse=False):
    FITDIR.mkdir(parents=True, exist_ok=True)
    q = "select id, summary, fit_status from activities"
    if not reparse:
        q += " where fit_status is null or fit_status = 'error'"
    done = errors = 0
    for aid, summary, status in db.execute(q).fetchall():
        s = json.loads(summary)
        path = FITDIR / f"{aid}.fit"
        try:
            if s.get("manualActivity") or s.get("isManualActivity"):
                db.execute("update activities set fit_status='none' where id=?", (aid,))
                continue
            if not path.exists():
                if g is None:
                    continue
                data = g.download_fit(aid)
                if not data:
                    db.execute("update activities set fit_status='none' where id=?", (aid,))
                    continue
                path.write_bytes(data)
            m = fitmetrics.parse(path, cfg)
            db.execute("update activities set fit_status='ok', fit=? where id=?", (json.dumps(m, default=str), aid))
            done += 1
        except Exception as e:  # un .fit raro no debe parar la sincronización
            errors += 1
            db.execute("update activities set fit_status='error', fit=? where id=?",
                       (json.dumps({"error": repr(e)[:300]}), aid))
        db.commit()
    return done, errors


# ---------------- datos diarios ----------------
def fetch_range(g, kind, a, b):
    fn, key, chunk = DAILY[kind]
    items = {}
    d = a
    while d <= b:
        e = min(b, d + dt.timedelta(days=chunk - 1))
        try:
            res = getattr(g, fn)(d.isoformat(), e.isoformat())
        except GarminError as err:
            if err.status == 400 and (e - d).days > 0:
                DAILY[kind] = (fn, key, max(1, chunk // 2))
                return {**items, **fetch_range(g, kind, d, b)}
            if err.status in (400, 404):
                res = []
            else:
                raise
        for it in res or []:
            k = it.get(key)
            if k:
                items[k[:10]] = it
        d = e + dt.timedelta(days=1)
    return items


def sync_daily(g, db, start, end):
    stats = {}
    recent = end - dt.timedelta(days=REFRESH_DAYS - 1)
    for kind in DAILY:
        have = {r[0] for r in db.execute("select date from daily where kind=?", (kind,))}
        want = [d for d in days(start, end) if d.isoformat() not in have or d >= recent]
        if not want:
            stats[kind] = 0
            continue
        # agrupa en tramos contiguos
        spans, s0, prev = [], want[0], want[0]
        for d in want[1:]:
            if (d - prev).days > 1:
                spans.append((s0, prev))
                s0 = d
            prev = d
        spans.append((s0, prev))
        n = 0
        for a, b in spans:
            items = fetch_range(g, kind, a, b)
            for d in days(a, b):
                it = items.get(d.isoformat())
                if it is None and d >= recent:
                    continue  # quizá aún no ha sincronizado: se vuelve a intentar
                db.execute("insert or replace into daily values(?,?,?,?)",
                           (d.isoformat(), kind, json.dumps(it) if it is not None else "null", now()))
                n += it is not None
        db.commit()
        stats[kind] = n
    return stats


def sync_extras(g, db, today):
    for kind, fn, arg in (("fitnessage", g.fitness_age, today.isoformat()),):
        try:
            db.execute("insert or replace into daily values(?,?,?,?)",
                       (today.isoformat(), kind, json.dumps(fn(arg)), now()))
        except GarminError:
            pass
    for key, fn in (("hr_zones", g.hr_zones), ("profile", g.profile)):
        try:
            db.execute("insert or replace into meta values(?,?)", (key, json.dumps(fn())))
        except GarminError:
            pass
    db.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--reparse", action="store_true")
    args = ap.parse_args()
    cfg = load_cfg()
    db = connect()
    if args.reparse:  # vuelve a calcular las métricas de los .fit; los que falten (p. ej. en la nube) se descargan
        try:
            g = Garmin()
        except GarminError:
            g = None
        print(json.dumps(dict(zip(("fit_procesados", "fit_errores"), sync_fits(g, db, cfg, reparse=True)))))
        return
    g = Garmin()
    today = dt.date.today()
    first = db.execute("select value from meta where key='last_sync'").fetchone() is None
    full = args.full or first
    start = dt.date.fromisoformat(cfg["inicio_historial"]) if full else today - dt.timedelta(days=10)
    try:
        new, upd = sync_activities(g, db, full)
        fits, ferr = sync_fits(g, db, cfg, reparse=args.reparse)
        stats = sync_daily(g, db, start, today)
        sync_extras(g, db, today)
    except GarminError as e:
        print("ERROR Garmin:", e, file=sys.stderr)
        sys.exit(2)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    db.execute("insert or replace into meta values('last_sync', ?)", (now(),))
    db.commit()
    print(json.dumps({"actividades_nuevas": new, "actividades_actualizadas": upd, "fit_procesados": fits,
                      "fit_errores": ferr, "diarios": stats, "llamadas_api": g.calls}, ensure_ascii=False))


if __name__ == "__main__":
    main()
