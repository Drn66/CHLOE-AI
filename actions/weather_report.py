"""Clima de verdade, falado pela CHLOE — sem abrir o navegador.

Antes esta ferramenta abria uma busca do Google numa aba nova e devolvia só
"Showing the weather…": a CHLOE não recebia dado nenhum para falar. Agora ela
consulta a Open-Meteo (gratuita, sem chave — a mesma fonte da Ilha), devolve
os números para o modelo responder em voz e mostra um cartão no painel abaixo
da esfera com a fonte. A cidade é opcional: sem ela, usa a localização
aproximada pelo IP, como a Ilha.
"""
from __future__ import annotations

from core.weather_service import detect_location, fetch_daily, fetch_weather, geocode

_WHEN = {"now": 0, "agora": 0, "today": 0, "hoje": 0, "tomorrow": 1, "amanhã": 1, "amanha": 1}


def weather_action(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    city = str(params.get("city") or "").strip()
    when = str(params.get("time") or params.get("when") or "now").strip().lower()
    day = _WHEN.get(when, 0)

    try:
        loc = geocode(city) if city else detect_location()
    except LookupError:
        return (f"No city called '{city}' was found. Tell the user you could not find that city "
                "and ask which one they meant. Do not open a browser.")
    except Exception:
        return ("The weather service could not be reached (no internet or the service is down). "
                "Tell the user plainly that you could not get the weather right now. Do not open a browser.")

    try:
        now = fetch_weather(loc) if day == 0 else None
        daily = fetch_daily(loc, days=day + 1)
    except Exception:
        return ("The weather service could not be reached (no internet or the service is down). "
                "Tell the user plainly that you could not get the weather right now. Do not open a browser.")

    target = daily[day] if len(daily) > day else None
    place = loc.label
    lines, card = [], [f"📍 {place}"]
    if now is not None:
        lines.append(
            f"Current weather in {place}: {round(now.temperature)}°C, {now.condition} "
            f"(Portuguese label), feels like {round(now.apparent_temperature)}°C, "
            f"humidity {round(now.humidity)}%, wind {round(now.wind_speed)} km/h"
            + (f", UV index {round(now.uv_index)}" if now.uv_index is not None else "") + "."
        )
        card += [f"Agora: {round(now.temperature)}°C · {now.condition}",
                 f"Sensação {round(now.apparent_temperature)}°C · umidade {round(now.humidity)}% · "
                 f"vento {round(now.wind_speed)} km/h"
                 + (f" · UV {round(now.uv_index)}" if now.uv_index is not None else "")]
    if target is not None:
        label = "Today" if day == 0 else "Tomorrow"
        rain = f", {target.rain_chance}% chance of rain" if target.rain_chance is not None else ""
        lines.append(f"{label} in {place}: {target.condition} (Portuguese label), "
                     f"high {round(target.temp_max)}°C, low {round(target.temp_min)}°C{rain}.")
        card.append(f"{'Hoje' if day == 0 else 'Amanhã'}: máx {round(target.temp_max)}°C · "
                    f"mín {round(target.temp_min)}°C"
                    + (f" · chuva {target.rain_chance}%" if target.rain_chance is not None else ""))
    card.append("Fonte: Open-Meteo · também na Ilha Dinâmica → andar Clima")

    if player is not None:
        try:
            player.show_content(f"Clima — {place}", "\n".join(card))
        except Exception:
            pass

    if session_memory:
        try:
            session_memory.set_last_search(query=f"weather {place} {when}", response=" ".join(lines))
        except Exception:
            pass

    return (" ".join(lines)
            + " Answer the user directly by voice with these numbers, in their language. "
              "The same card is already on screen in the panel under the sphere (source: Open-Meteo); "
              "if it helps, mention that they can also see it there or on the Dynamic Island's weather floor. "
              "Do not open a browser.")


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "weather_report",
    "description": (
        "Gets the real current weather and today's/tomorrow's forecast (temperature, condition, "
        "feels-like, humidity, wind, rain chance) and returns the numbers for you to SAY. "
        "Use for ANY question about weather, outdoor temperature, rain or forecast. It never opens "
        "a browser. Leave 'city' empty for the user's current location."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {
                "type": "STRING",
                "description": "City name, only if the user named one; empty means where the user is",
            },
            "time": {
                "type": "STRING",
                "description": "now | today | tomorrow (default now)",
            },
        },
    },
    "handler": weather_action,
}
