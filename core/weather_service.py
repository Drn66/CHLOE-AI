"""
core/weather_service.py — Clima ao vivo para a Ilha Dinâmica.

* Localização automática por IP: ipinfo.io (HTTPS) com ip-api.com de reserva.
* Tempo atual: Open-Meteo (gratuito, sem API key).
* UV e qualidade do ar: Open-Meteo Air Quality (opcional; falha em silêncio).
* Roda numa QThread própria e atualiza a cada 15 minutos. Só usa a biblioteca padrão.

Uso:
    weather = WeatherService()                      # ou WeatherService(location=Location(...))
    weather.weather_updated.connect(island.update_weather)
    weather.start()
    ...
    weather.refresh_now()   # força atualização
    weather.stop()
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger("chloe.weather")

USER_AGENT = "CHLOE-AI/1.0 (desktop assistant)"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# Código WMO → (descrição em PT-BR, tipo de ícone)
WMO: dict[int, tuple[str, str]] = {
    0: ("Céu limpo", "clear"),
    1: ("Predominantemente limpo", "clear"),
    2: ("Parcialmente nublado", "partly"),
    3: ("Nublado", "cloudy"),
    45: ("Neblina", "fog"),
    48: ("Neblina com geada", "fog"),
    51: ("Garoa fraca", "rain"),
    53: ("Garoa", "rain"),
    55: ("Garoa intensa", "rain"),
    56: ("Garoa congelante", "rain"),
    57: ("Garoa congelante intensa", "rain"),
    61: ("Chuva fraca", "rain"),
    63: ("Chuva", "rain"),
    65: ("Chuva forte", "rain"),
    66: ("Chuva congelante", "rain"),
    67: ("Chuva congelante forte", "rain"),
    71: ("Neve fraca", "snow"),
    73: ("Neve", "snow"),
    75: ("Neve forte", "snow"),
    77: ("Grãos de neve", "snow"),
    80: ("Pancadas de chuva fracas", "rain"),
    81: ("Pancadas de chuva", "rain"),
    82: ("Pancadas de chuva fortes", "rain"),
    85: ("Pancadas de neve", "snow"),
    86: ("Pancadas de neve fortes", "snow"),
    95: ("Tempestade", "storm"),
    96: ("Tempestade com granizo", "storm"),
    99: ("Tempestade com granizo forte", "storm"),
}

BR_UF = {
    "Acre": "AC", "Alagoas": "AL", "Amapá": "AP", "Amazonas": "AM", "Bahia": "BA", "Ceará": "CE",
    "Distrito Federal": "DF", "Federal District": "DF", "Espírito Santo": "ES", "Goiás": "GO",
    "Maranhão": "MA", "Mato Grosso": "MT", "Mato Grosso do Sul": "MS", "Minas Gerais": "MG",
    "Pará": "PA", "Paraíba": "PB", "Paraná": "PR", "Pernambuco": "PE", "Piauí": "PI",
    "Rio de Janeiro": "RJ", "Rio Grande do Norte": "RN", "Rio Grande do Sul": "RS", "Rondônia": "RO",
    "Roraima": "RR", "Santa Catarina": "SC", "São Paulo": "SP", "Sergipe": "SE", "Tocantins": "TO",
}


@dataclass(frozen=True)
class Location:
    city: str
    region: str
    country: str
    latitude: float
    longitude: float
    region_code: str = ""

    @property
    def label(self) -> str:
        code = self.region_code or BR_UF.get(self.region, "")
        tail = code or self.region or self.country
        return f"{self.city}, {tail}" if self.city and tail else (self.city or tail or "Local desconhecido")


@dataclass(frozen=True)
class WeatherData:
    location: Location
    temperature: float
    apparent_temperature: float
    humidity: float
    wind_speed: float            # km/h
    weather_code: int
    is_day: bool
    uv_index: Optional[float]
    aqi: Optional[int]           # European AQI
    updated_at: datetime

    @property
    def condition(self) -> str:
        return WMO.get(self.weather_code, ("Condição desconhecida", "cloudy"))[0]

    @property
    def kind(self) -> str:
        return WMO.get(self.weather_code, ("", "cloudy"))[1]

    @property
    def uv_label(self) -> str:
        uv = self.uv_index or 0
        if uv < 3:
            return "Baixo"
        if uv < 6:
            return "Moderado"
        if uv < 8:
            return "Alto"
        if uv < 11:
            return "Muito alto"
        return "Extremo"

    @property
    def aqi_label(self) -> str:
        a = self.aqi or 0
        for limit, label in ((20, "Boa"), (40, "Razoável"), (60, "Moderada"), (80, "Ruim"), (100, "Muito ruim")):
            if a <= limit:
                return label
        return "Péssima"


# ── HTTP ─────────────────────────────────────────────────────────────────────
def _get_json(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def detect_location() -> Location:
    """Localização aproximada pelo IP público."""
    try:
        d = _get_json("https://ipinfo.io/json")
        lat, lon = (float(v) for v in d["loc"].split(","))
        return Location(city=d.get("city", ""), region=d.get("region", ""), country=d.get("country", ""),
                        latitude=lat, longitude=lon)
    except Exception as exc:
        log.info("ipinfo.io falhou (%s); tentando ip-api.com", exc)

    fields = "status,message,country,countryCode,regionName,region,city,lat,lon"
    d = _get_json(f"http://ip-api.com/json/?fields={fields}&lang=pt-BR")
    if d.get("status") != "success":
        raise RuntimeError(f"ip-api.com: {d.get('message', 'falha')}")
    return Location(city=d.get("city", ""), region=d.get("regionName", ""), country=d.get("countryCode", ""),
                    latitude=float(d["lat"]), longitude=float(d["lon"]), region_code=d.get("region", ""))


def fetch_weather(loc: Location) -> WeatherData:
    q = urllib.parse.urlencode({
        "latitude": f"{loc.latitude:.4f}",
        "longitude": f"{loc.longitude:.4f}",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,wind_speed_10m",
        "wind_speed_unit": "kmh",
        "timezone": "auto",
    })
    cur = _get_json(f"{FORECAST_URL}?{q}")["current"]

    uv: Optional[float] = None
    aqi: Optional[int] = None
    try:
        aq_q = urllib.parse.urlencode({
            "latitude": f"{loc.latitude:.4f}",
            "longitude": f"{loc.longitude:.4f}",
            "current": "uv_index,european_aqi",
            "timezone": "auto",
        })
        aq = _get_json(f"{AIR_URL}?{aq_q}").get("current", {})
        uv = aq.get("uv_index")
        aqi = int(aq["european_aqi"]) if aq.get("european_aqi") is not None else None
    except Exception as exc:
        log.debug("Qualidade do ar indisponível: %s", exc)

    return WeatherData(
        location=loc,
        temperature=float(cur["temperature_2m"]),
        apparent_temperature=float(cur["apparent_temperature"]),
        humidity=float(cur["relative_humidity_2m"]),
        wind_speed=float(cur["wind_speed_10m"]),
        weather_code=int(cur["weather_code"]),
        is_day=bool(cur.get("is_day", 1)),
        uv_index=float(uv) if uv is not None else None,
        aqi=aqi,
        updated_at=datetime.now(),
    )


# ── Serviço em segundo plano ─────────────────────────────────────────────────
class WeatherService(QThread):
    weather_updated = pyqtSignal(object)   # WeatherData
    error = pyqtSignal(str)

    LOCATION_TTL_S = 6 * 3600              # redetecta a cidade a cada 6 h (notebooks viajam)

    def __init__(self, interval_s: int = 15 * 60, location: Optional[Location] = None, parent=None) -> None:
        super().__init__(parent)
        self._interval = interval_s
        self._fixed_location = location
        self._location: Optional[Location] = location
        self._location_at: Optional[datetime] = None
        self._stop_evt = threading.Event()
        self._wake_evt = threading.Event()
        self.last: Optional[WeatherData] = None

    def run(self) -> None:
        retry = 60
        while not self._stop_evt.is_set():
            try:
                loc = self._resolve_location()
                data = fetch_weather(loc)
                self.last = data
                self.weather_updated.emit(data)
                wait, retry = self._interval, 60
            except Exception as exc:
                log.warning("Clima: %s", exc)
                self.error.emit(str(exc))
                if self._fixed_location is None:
                    self._location = None          # tenta detectar de novo
                wait, retry = retry, min(retry * 2, self._interval)
            self._wake_evt.wait(wait)
            self._wake_evt.clear()

    def _resolve_location(self) -> Location:
        if self._fixed_location is not None:
            return self._fixed_location
        stale = (self._location_at is None
                 or (datetime.now() - self._location_at).total_seconds() > self.LOCATION_TTL_S)
        if self._location is None or stale:
            self._location = detect_location()
            self._location_at = datetime.now()
            log.info("Local detectado: %s", self._location.label)
        return self._location

    def refresh_now(self) -> None:
        self._wake_evt.set()

    def stop(self, wait_ms: int = 4000) -> None:
        self._stop_evt.set()
        self._wake_evt.set()
        if not self.wait(wait_ms):
            # Só acontece se o app fecha no meio de uma requisição HTTP lenta.
            # Encerrar a thread aqui evita que o Qt aborte o processo ao destruí-la rodando.
            log.warning("Clima: requisição ainda em andamento ao encerrar; finalizando a thread.")
            self.terminate()
            self.wait(1000)
