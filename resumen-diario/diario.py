"""Paso automático del informe diario: sincroniza Garmin y calcula los datos de hoy.

Uso: python garmin/diario.py [--hoy AAAA-MM-DD]
Imprime un JSON con el estado: si hay sueño/VFC de anoche, qué actividades se analizan y dónde está el borrador.
Después, Claude escribe garmin/informes/<hoy>.interp.json y ejecuta garmin/export_dash.py.
"""
import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hoy", default=dt.date.today().isoformat())
    args = ap.parse_args()
    py = sys.executable
    sync = subprocess.run([py, str(ROOT / "sync.py")], capture_output=True, text=True, encoding="utf-8")
    estado = {"sync_ok": sync.returncode == 0, "sync": (sync.stdout or "").strip()[-600:],
              "sync_error": (sync.stderr or "").strip()[-800:] or None}
    rep = subprocess.run([py, str(ROOT / "report.py"), "--hoy", args.hoy], capture_output=True, text=True, encoding="utf-8")
    estado["report_ok"] = rep.returncode == 0
    if rep.returncode != 0:
        estado["report_error"] = (rep.stderr or "").strip()[-800:]
    else:
        data = json.loads((ROOT / "informes" / f"{args.hoy}.json").read_text(encoding="utf-8"))
        b = data["bienestar"]
        estado.update({
            "borrador": str(ROOT / "informes" / f"{args.hoy}.md"),
            "datos": str(ROOT / "informes" / f"{args.hoy}.json"),
            "interpretacion_a_escribir": str(ROOT / "informes" / f"{args.hoy}.interp.json"),
            "hay_sueno_anoche": b["sueno_h"]["valor"] is not None,
            "hay_vfc_anoche": b["hrv_noche"]["valor"] is not None,
            "actividades": [f"{a['fecha']} {a['hora']} {a['nombre']} ({a['familia']}, {a['dur_min']} min)" for a in data["actividades"]],
            "nota_actividades": data.get("nota_actividades"),
            "alertas": len(data["alertas"]),
        })
    print(json.dumps(estado, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
