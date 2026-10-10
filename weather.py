# weather.py: weather from Open-Meteo (free, no API key needed).
#   find_city("Winston-Salem")  -> the city's full name, location, and time zone
#   forecast(latitude, longitude) -> today's and tomorrow's weather, hour by hour
# Docs: https://open-meteo.com/en/docs

import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo describes the sky with number codes (the "WMO weather codes").
CONDITIONS = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy",
    45: "fog", 48: "fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "heavy showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


def icon(sky):
    """An emoji for a sky description like "light rain"."""
    for words, emoji in [("thunder", "⛈️"), ("snow", "❄️"), ("freezing", "🧊"), ("rain", "🌧️"), ("shower", "🌧️"),
                         ("drizzle", "🌦️"), ("fog", "🌫️"), ("partly", "⛅"), ("mostly clear", "🌤️"),
                         ("cloudy", "☁️"), ("clear", "☀️")]:
        if words in sky:
            return emoji
    return "🌡️"


def _round(value):
    return None if value is None else round(value)


def find_city(name):
    """Look up a city by name. Returns a dict, or None if nothing matched."""
    # Open-Meteo only understands the city name, so drop anything after a comma ("Winston-Salem, NC").
    response = requests.get(
        GEOCODE_URL,
        params={"name": name.split(",")[0].strip(), "count": 1, "language": "en", "format": "json"},
        timeout=15,
    )
    response.raise_for_status()
    results = response.json().get("results")
    if not results:
        return None
    place = results[0]
    label = ", ".join(part for part in [place["name"], place.get("admin1"), place.get("country_code")] if part)
    return {
        "place_name": label,
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "timezone": place["timezone"],
    }


def forecast(latitude, longitude):
    """Today's and tomorrow's weather where the person lives (°F, mph).
    Hourly details cover 5am to 9pm, the hours someone might go for a run."""
    response = requests.get(
        FORECAST_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m,apparent_temperature,precipitation_probability,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "sunrise,sunset,uv_index_max",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "timezone": "auto",  # give times in the city's own time zone
            "forecast_days": 2,
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    daily, hourly = data["daily"], data["hourly"]

    days = []
    for i, day in enumerate(daily["time"]):
        hours = []
        for j, time in enumerate(hourly["time"]):  # times look like "2026-10-10T07:00"
            if time.startswith(day) and 5 <= int(time[11:13]) <= 21:
                hours.append({
                    "time": time[11:16],
                    "temp_f": _round(hourly["temperature_2m"][j]),
                    "feels_like_f": _round(hourly["apparent_temperature"][j]),
                    "rain_chance_pct": hourly["precipitation_probability"][j],
                    "wind_mph": _round(hourly["wind_speed_10m"][j]),
                    "sky": CONDITIONS.get(hourly["weather_code"][j], "unknown"),
                })
        days.append({
            "date": day,
            "sky": CONDITIONS.get(daily["weather_code"][i], "unknown"),
            "high_f": _round(daily["temperature_2m_max"][i]),
            "low_f": _round(daily["temperature_2m_min"][i]),
            "max_rain_chance_pct": daily["precipitation_probability_max"][i],
            "sunrise": daily["sunrise"][i][11:16],
            "sunset": daily["sunset"][i][11:16],
            "uv_index_max": daily["uv_index_max"][i],
            "hourly": hours,
        })
    return {"days": days}
