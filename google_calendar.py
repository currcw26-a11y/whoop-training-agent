# google_calendar.py: everything that talks to Google Calendar.
#   1. Sends people to Google to approve calendar access ("Connect Google Calendar").
#   2. Trades the code Google sends back for tokens, and refreshes them later.
#   3. Reads events (to find free time) and adds workouts the person approved.
# It reuses the Google OAuth client from "Sign in with Google" (the [auth] section of secrets.toml).
# Google Calendar API docs: https://developers.google.com/workspace/calendar/api

from datetime import datetime, time, timedelta, timezone
from urllib.parse import quote, urlencode

import requests
import streamlit as st

import config
import db

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_URL = "https://www.googleapis.com/calendar/v3"

# The three permissions we ask for, as narrow as Google allows:
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events.readonly",       # see events on all your calendars (free time)
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",  # see which calendars you have
    "https://www.googleapis.com/auth/calendar.events.owned",           # add workouts to calendars you own
]


# ---------- Connecting ----------

def login_url(state, email):
    """The Google page that asks the person to let this app use their calendar."""
    params = {
        "client_id": st.secrets["auth"]["client_id"],
        "redirect_uri": st.secrets["app_url"],  # where Google sends them back
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",   # gives us a refresh token, so this keeps working
        "prompt": "consent",        # always show the screen, so Google always sends that refresh token
        "include_granted_scopes": "true",
        "login_hint": email,        # suggest the account they signed in with (they can pick another)
        "state": state,
    }
    return AUTH_URL + "?" + urlencode(params)


def _ask_for_tokens(form):
    """Send a request to Google's token address (used for connecting and refreshing)."""
    form["client_id"] = st.secrets["auth"]["client_id"]
    form["client_secret"] = st.secrets["auth"]["client_secret"]
    response = requests.post(TOKEN_URL, data=form, timeout=20)
    response.raise_for_status()
    return response.json()


def finish_connecting(email, code):
    """Google sent the person back with a one-time code. Trade it for tokens and save them.
    Returns the permissions the person unchecked on Google's screen (empty if they allowed everything)."""
    tokens = _ask_for_tokens({"grant_type": "authorization_code", "code": code, "redirect_uri": st.secrets["app_url"]})
    db.save_tokens(email, "google", tokens)
    granted = tokens.get("scope", "").split()
    return [scope for scope in SCOPES if scope not in granted]


def disconnect(email):
    """Tell Google to cancel our access, then forget the tokens."""
    saved = db.get_tokens(email, "google")
    if saved and saved["refresh_token"]:
        try:
            requests.post(REVOKE_URL, data={"token": saved["refresh_token"]}, timeout=10)
        except requests.RequestException:
            pass  # even if Google can't be reached, we still delete our copy
    db.delete_tokens(email, "google")


def _access_token(email):
    """Return a working access token, refreshing it first if it has expired."""
    saved = db.get_tokens(email, "google")
    if saved["expires_at"] > datetime.now(timezone.utc) + timedelta(minutes=2):
        return saved["access_token"]
    # Google usually keeps the same refresh token (so it doesn't send a new one);
    # save_tokens() keeps the old one when that happens.
    try:
        tokens = _ask_for_tokens({"grant_type": "refresh_token", "refresh_token": saved["refresh_token"]})
    except requests.HTTPError as error:
        if error.response.status_code in (400, 401):
            # Google says the refresh token no longer works. That happens every 7 days while the
            # Google app is in "Testing" mode, or if the person removed access. Forget it, so the
            # site shows the Connect button again.
            db.delete_tokens(email, "google")
        raise
    db.save_tokens(email, "google", tokens)
    return tokens["access_token"]


def _call(email, method, path, **options):
    response = requests.request(
        method, API_URL + path, headers={"Authorization": "Bearer " + _access_token(email)}, timeout=20, **options,
    )
    response.raise_for_status()
    return response.json()


# ---------- Reading the calendar ----------

def _when(text, time_zone):
    """Google gives '2026-10-11T09:00:00-04:00' for timed events and '2026-10-11' for all-day ones."""
    if "T" in text:
        return datetime.fromisoformat(text).astimezone(time_zone)
    return datetime.combine(datetime.fromisoformat(text).date(), time(0), time_zone)


def list_events(email, start, end, time_zone):
    """Every event between two times, from each calendar the person shows in Google Calendar."""
    calendars = _call(email, "GET", "/users/me/calendarList")["items"]
    # Calendars the person has hidden in Google Calendar are skipped. The main calendar comes first,
    # so its "rank" is 0; the website uses the rank to give each calendar the same color every week.
    shown = [c for c in calendars if c.get("selected") or c.get("primary")]
    shown.sort(key=lambda c: not c.get("primary"))
    events = []
    for rank, calendar in enumerate(shown):
        page = _call(email, "GET", f"/calendars/{quote(calendar['id'], safe='')}/events", params={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 250,
        })
        for item in page.get("items", []):
            declined = any(person.get("self") and person.get("responseStatus") == "declined"
                           for person in item.get("attendees", []))
            if item.get("status") == "cancelled" or declined:
                continue
            events.append({
                "title": item.get("summary", "(busy)"),
                "calendar": calendar.get("summaryOverride") or calendar.get("summary", ""),
                "calendar_rank": rank,
                "start": _when(item["start"].get("dateTime") or item["start"]["date"], time_zone),
                "end": _when(item["end"].get("dateTime") or item["end"]["date"], time_zone),
                "all_day": "date" in item["start"],
                "busy": item.get("transparency") != "transparent",  # events marked "free" don't block time
                "from_coach": item.get("extendedProperties", {}).get("private", {}).get("trainingCoach") == "yes",
            })
    return sorted(events, key=lambda event: event["start"])


def _next_quarter_hour(moment):
    moment = moment.replace(second=0, microsecond=0)
    return moment + timedelta(minutes=(-moment.minute) % 15)


def free_windows(events, day, time_zone, now, preferred_hours):
    """Open stretches of at least MIN_FREE_MINUTES on one day, between DAY_START_HOUR and DAY_END_HOUR.
    preferred_hours is a list of (start hour, end hour) the person likes to run in (empty = any time)."""
    day_start = datetime.combine(day, time(config.DAY_START_HOUR), time_zone)
    day_end = datetime.combine(day, time(config.DAY_END_HOUR), time_zone)
    if day == now.date():
        day_start = max(day_start, _next_quarter_hour(now))  # can't schedule in the past

    # Busy times on this day (all-day events like "Fall break" don't block hours).
    busy = sorted(
        (max(event["start"], day_start), min(event["end"], day_end))
        for event in events
        if event["busy"] and not event["all_day"] and not event["from_coach"]
        and event["start"] < day_end and event["end"] > day_start
    )
    gaps, cursor = [], day_start
    for busy_start, busy_end in busy:
        if busy_start > cursor:
            gaps.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
    if cursor < day_end:
        gaps.append((cursor, day_end))

    windows = []
    for gap_start, gap_end in gaps:
        minutes = int((gap_end - gap_start).total_seconds() // 60)
        if minutes < config.MIN_FREE_MINUTES:
            continue
        # Does this gap overlap any of the times the person said they like to run?
        liked = not preferred_hours or any(
            gap_start < datetime.combine(day, time(last), time_zone)
            and gap_end > datetime.combine(day, time(first), time_zone)
            for first, last in preferred_hours
        )
        windows.append({"start": f"{gap_start:%H:%M}", "end": f"{gap_end:%H:%M}", "minutes": minutes,
                        "in_their_run_times": liked})
    return windows


# ---------- Adding a workout ----------

def save_workout_event(email, event_id, start, minutes, title, description, time_zone_name):
    """Put the workout on the person's main calendar (or move it, if we added one before).
    Returns Google's ID for the event."""
    body = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": time_zone_name},
        "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat(), "timeZone": time_zone_name},
        # A hidden tag, so the coach knows this event is its own workout and not a class.
        "extendedProperties": {"private": {"trainingCoach": "yes"}},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]},
    }
    if event_id:
        try:
            return _call(email, "PATCH", f"/calendars/primary/events/{quote(event_id, safe='')}", json=body)["id"]
        except requests.HTTPError as error:
            if error.response.status_code not in (404, 410):
                raise  # 404/410 means the person deleted it, so we make a new one below
    return _call(email, "POST", "/calendars/primary/events", json=body)["id"]
