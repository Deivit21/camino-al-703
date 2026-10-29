"""Cliente mínimo de la Garmin Connect Personal API (solo lectura).

La clave personal nunca está en el proyecto: se lee de la variable de entorno GARMIN_PERSONAL_API_KEY
(ejecución en la nube) o de ~/.config/garmin/connect-personal-api-key (este PC).
Se cambia por un token Bearer (JWT, 24 h) que se cachea en ~/.config/garmin/bearer-token.json.
"""
import hashlib
import io
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE = "https://personal.connectapi.garmin.com"
TOKEN_URL = BASE + "/personalapitoken-service/personal-api/personal-api-key/token"
CFG = pathlib.Path.home() / ".config" / "garmin"
KEY_FILE = CFG / "connect-personal-api-key"
TOKEN_FILE = CFG / "bearer-token.json"
UA = "deporte-garmin-sync/1.0"


class GarminError(Exception):
    def __init__(self, status, url, body=""):
        super().__init__(f"HTTP {status} {url} {body[:200]}")
        self.status = status
        self.url = url
        self.body = body


def _jwt_exp(token):
    import base64
    p = token.split(".")[1]
    p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p))["exp"]


class Garmin:
    def __init__(self, timeout=60):
        self.timeout = timeout
        self.key = (os.environ.get("GARMIN_PERSONAL_API_KEY") or "").strip()
        if not self.key and KEY_FILE.exists():
            self.key = KEY_FILE.read_text(encoding="utf-8").strip()
        if not self.key:
            raise GarminError(0, "", "Falta la clave: define GARMIN_PERSONAL_API_KEY o crea " + str(KEY_FILE))
        self.key_hash = hashlib.sha256(self.key.encode()).hexdigest()[:16]
        self.token = None
        self.calls = 0

    # ---- token ----
    def _token(self, force=False):
        if self.token and not force:
            return self.token
        if not force and TOKEN_FILE.exists():
            try:
                c = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
                if c.get("key") == self.key_hash and c["exp"] - time.time() > 3600:
                    self.token = c["token"]
                    return self.token
            except (ValueError, KeyError):
                pass
        req = urllib.request.Request(TOKEN_URL, method="POST", data=b"",
                                     headers={"Authorization": "ApiKey " + self.key, "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode().strip()
        except urllib.error.HTTPError as e:
            raise GarminError(e.code, TOKEN_URL, "No se pudo obtener el token: ¿clave caducada o revocada? "
                              + e.read().decode(errors="replace")) from None
        if raw.startswith("{"):
            d = json.loads(raw)
            raw = d.get("access_token") or d.get("accessToken") or d.get("token")
        raw = raw.strip('"')
        CFG.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps({"key": self.key_hash, "token": raw, "exp": _jwt_exp(raw)}), encoding="utf-8")
        try:
            TOKEN_FILE.chmod(0o600)
        except OSError:
            pass
        self.token = raw
        return raw

    # ---- peticiones ----
    def _request(self, path, params=None, raw=False):
        url = BASE + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        refreshed = False
        for attempt in range(6):
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self._token(), "User-Agent": UA,
                                                       "Accept": "*/*" if raw else "application/json"})
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read()
                if raw:
                    return body
                return json.loads(body) if body.strip() else None
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")
                if e.code == 401 and not refreshed:
                    self._token(force=True)
                    refreshed = True
                    continue
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(min(60, 2 ** attempt * 2))
                    continue
                raise GarminError(e.code, url, text) from None
            except (urllib.error.URLError, TimeoutError):
                time.sleep(2 ** attempt)
        raise GarminError(0, url, "demasiados reintentos")

    def get(self, path, **params):
        return self._request(path, params or None)

    # ---- endpoints ----
    def activities(self, start=0, limit=100, **kw):
        return self.get("/activitylist-service/activities/search/activities", start=start, limit=limit, **kw)

    def activity(self, aid):
        return self.get(f"/activity-service/activity/{aid}")

    def download_fit(self, aid):
        """Devuelve los bytes del .fit original (la API lo entrega dentro de un zip)."""
        data = self._request(f"/download-service/files/activity/{aid}", raw=True)
        if data[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".fit")]
                if not names:
                    return None
                return z.read(names[0])
        return data

    def daily_summaries(self, start, end):
        return (self.get("/usersummary-service/v2/dailysummaries", startDate=start, endDate=end) or {}).get("data", [])

    def sleep_range(self, start, end):
        return self.get("/sleep-service/sleep/dailySleepSummariesByDate", startDate=start, endDate=end) or []

    def sleep_day(self, date):
        return self.get("/sleep-service/sleep/dailySleep", date=date)

    def hrv_range(self, start, end):
        return (self.get(f"/hrv-service/hrv/daily/{start}/{end}") or {}).get("hrvSummaries", [])

    def stress_range(self, start, end):
        return self.get(f"/usersummary-service/stats/stress/daily/{start}/{end}") or []

    def body_battery_range(self, start, end):
        return self.get("/wellness-service/wellness/bodyBattery/reports/daily", startDate=start, endDate=end) or []

    def weight_range(self, start, end):
        return (self.get(f"/weight-service/weight/range/{start}/{end}", includeAll="true") or {}).get("dailyWeightSummaries", [])

    def fitness_age(self, date):
        return self.get(f"/fitnessage-service/fitnessage/v2/{date}")

    def hr_zones(self):
        return self.get("/biometric-service/heartRateZones/v2")

    def profile(self):
        return self.get("/userprofile-service/userprofile/settings")
