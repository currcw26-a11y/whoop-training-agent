# whoop.py: everything that talks to WHOOP.
#   1. Sends people to WHOOP's site to approve this app ("Connect WHOOP").
#   2. Trades the code WHOOP sends back for tokens, and refreshes them later.
#   3. Downloads recovery, sleep, strain, and workouts and saves them to the database.
# WHOOP's API docs: https://developer.whoop.com/api

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
import streamlit as st

import db

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_URL = "https://api.prod.whoop.com/developer/v2"

# What we ask permission to read. "offline" gets us a refresh token, so we can
# keep downloading new data without the person logging in to WHOOP again.
SCOPES = "offline read:recovery read:sleep read:cycles read:workout"


# ---------- Connecting ----------

def login_url(state):
    """The WHOOP page that asks the person to let this app read their data."""
    params = {
        "response_type": "code",
        "client_id": st.secrets["whoop"]["client_id"],
        "redirect_uri": st.secrets["app_url"],  # where WHOOP sends them back
        "scope": SCOPES,
        "state": state,
    }
    return AUTH_URL + "?" + urlencode(params)


def _ask_for_tokens(form):
    """Send a request to WHOOP's token address (used for connecting and refreshing)."""
    form["client_id"] = st.secrets["whoop"]["client_id"]
    form["client_secret"] = st.secrets["whoop"]["client_secret"]
    response = requests.post(TOKEN_URL, data=form, timeout=20)
    response.raise_for_status()  # stop with an error if WHOOP said no
    return response.json()


def finish_connecting(email, code):
    """WHOOP sent the person back with a one-time code. Trade it for tokens and save them."""
    tokens = _ask_for_tokens({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": st.secrets["app_url"],
    })
    db.save_tokens(email, "whoop", tokens)


def _access_token(email):
    """Return a working access token, refreshing it first if it has expired."""
    saved = db.get_tokens(email, "whoop")
    if saved["expires_at"] > datetime.now(timezone.utc) + timedelta(minutes=2):
        return saved["access_token"]  # still good

    # It expired, so trade the refresh token for a new pair. WHOOP sends a NEW
    # refresh token every time and the old one stops working, so save it right away.
    tokens = _ask_for_tokens({
        "grant_type": "refresh_token",
        "refresh_token": saved["refresh_token"],
        "scope": "offline",
    })
    db.save_tokens(email, "whoop", tokens)
    return tokens["access_token"]


# ---------- Downloading ----------

def _download(token, path, start):
    """Get every record from one WHOOP list. WHOOP sends 25 at a time, so keep asking for the next page."""
    records = []
    params = {"limit": 25, "start": start}
    while True:
        response = requests.get(
            API_URL + path,
            params=params,
            headers={"Authorization": "Bearer " + token},
            timeout=20,
        )
        response.raise_for_status()
        page = response.json()
        records.extend(page["records"])
        if not page.get("next_token"):
            return records
        params["nextToken"] = page["next_token"]


def sync(email, days=14):
    """Download the last `days` days from WHOOP and save them. Returns how many days were saved."""
    token = _access_token(email)
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    recoveries = _download(token, "/recovery", start)
    sleeps = _download(token, "/activity/sleep", start)
    cycles = _download(token, "/cycle", start)  # a "cycle" is one WHOOP day; it holds day strain
    workouts = _download(token, "/activity/workout", start)

    day_rows, workout_rows = build_rows(email, recoveries, sleeps, cycles, workouts)
    db.save_daily_metrics(day_rows)
    db.save_workouts(workout_rows)
    db.mark_whoop_synced(email)
    return len(day_rows)


# ---------- Turning WHOOP's format into our tables ----------

def _round(value, digits=None):
    """Like round(), but leaves a missing value as None instead of crashing."""
    return None if value is None else round(value, digits)


def _local_date(utc_time, offset):
    """WHOOP gives times in UTC plus the person's offset (like "-05:00").
    Return the date where they are, like "2026-10-09"."""
    moment = datetime.fromisoformat(utc_time)
    their_zone = datetime.strptime(offset, "%z").tzinfo
    return moment.astimezone(their_zone).date().isoformat()


def build_rows(email, recoveries, sleeps, cycles, workouts):
    """Turn WHOOP's raw records into rows for our daily_metrics and workouts tables.

    Each recovery points to the sleep and the cycle it belongs to, so we use it
    to join all three. A day is labeled with the date the person woke up.
    """
    sleeps_by_id = {sleep["id"]: sleep for sleep in sleeps}
    cycles_by_id = {cycle["id"]: cycle for cycle in cycles}

    days = {}  # date -> row (a dict makes sure each date appears only once)
    for recovery in recoveries:
        sleep = sleeps_by_id.get(recovery["sleep_id"])
        if recovery["score_state"] != "SCORED" or sleep is None:
            continue  # WHOOP hasn't scored this one yet, or its sleep is outside our window
        day = _local_date(sleep["end"], sleep["timezone_offset"])
        if day in days:
            continue

        score = recovery["score"]
        sleep_score = sleep.get("score") or {}
        stages = sleep_score.get("stage_summary") or {}
        asleep_ms = (
            stages.get("total_light_sleep_time_milli", 0)
            + stages.get("total_slow_wave_sleep_time_milli", 0)
            + stages.get("total_rem_sleep_time_milli", 0)
        )
        cycle_score = (cycles_by_id.get(recovery["cycle_id"]) or {}).get("score") or {}

        days[day] = {
            "email": email,
            "date": day,
            "recovery_score": _round(score.get("recovery_score")),
            "hrv_ms": _round(score.get("hrv_rmssd_milli"), 1),
            "resting_hr": _round(score.get("resting_heart_rate")),
            "sleep_hours": round(asleep_ms / 3_600_000, 1) if asleep_ms else None,
            "sleep_performance": _round(sleep_score.get("sleep_performance_percentage")),
            "day_strain": _round(cycle_score.get("strain"), 1),
        }

    workout_rows = []
    for workout in workouts:
        if not workout.get("end"):
            continue  # still in progress
        score = workout.get("score") or {}
        seconds = (datetime.fromisoformat(workout["end"]) - datetime.fromisoformat(workout["start"])).total_seconds()
        meters = score.get("distance_meter")
        workout_rows.append({
            "id": workout["id"],
            "email": email,
            "date": _local_date(workout["start"], workout["timezone_offset"]),
            "sport": (workout.get("sport_name") or "workout").replace("-", " ").title(),
            "minutes": round(seconds / 60),
            "strain": _round(score.get("strain"), 1),
            "distance_miles": round(meters / 1609.344, 2) if meters else None,
            "avg_hr": _round(score.get("average_heart_rate")),
        })

    return list(days.values()), workout_rows
