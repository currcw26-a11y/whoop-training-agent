# db.py: everything that reads from or writes to the Supabase database.
#
# The website connects with the SECRET key, which can see every row. So every
# function here takes the person's email and only touches that person's rows.

import secrets
from datetime import date, datetime, timedelta, timezone

import streamlit as st
from cryptography.fernet import Fernet
from supabase import create_client


@st.cache_resource
def _client():
    """Connect to Supabase once, then reuse the same connection."""
    return create_client(st.secrets["supabase_url"], st.secrets["supabase_secret_key"])


def _now():
    return datetime.now(timezone.utc)


# ---------- Encrypting tokens ----------
# Login tokens are scrambled with our secret key before they're saved, so
# someone who only gets a copy of the database still can't use them.

def _lock(text):
    return Fernet(st.secrets["token_encryption_key"]).encrypt(text.encode()).decode()


def _unlock(text):
    return Fernet(st.secrets["token_encryption_key"]).decrypt(text.encode()).decode()


# ---------- Profiles ----------

def ensure_profile(email, name):
    """Add the person to the profiles table (or update their name if they're already there)."""
    _client().table("profiles").upsert({"email": email, "name": name}).execute()


def get_profile(email):
    rows = _client().table("profiles").select("*").eq("email", email).execute().data
    return rows[0] if rows else None


def update_profile(email, changes):
    """Save changes to the person's coaching profile (goal, city, ...)."""
    _client().table("profiles").update(changes).eq("email", email).execute()


def mark_whoop_synced(email):
    _client().table("profiles").update({"whoop_synced_at": _now().isoformat()}).eq("email", email).execute()


def delete_everything(email):
    """Delete the person's profile. Every other table deletes their rows automatically."""
    _client().table("profiles").delete().eq("email", email).execute()


# ---------- Connection tickets ("state") ----------

def new_oauth_state(email, provider):
    """Make a random 8-character ticket for connecting WHOOP or Google, and remember it."""
    state = secrets.token_urlsafe(6)  # 6 random bytes = 8 characters
    _client().table("oauth_states").upsert({
        "email": email,
        "provider": provider,
        "state": state,
        "created_at": _now().isoformat(),
    }).execute()
    return state


def use_oauth_state(email, state):
    """Check a ticket that came back from WHOOP or Google.

    Returns the provider ('whoop' or 'google') if the ticket belongs to this
    person and is less than an hour old. Otherwise returns None.
    Each ticket only works once.
    """
    rows = _client().table("oauth_states").select("*").eq("email", email).eq("state", state).execute().data
    if not rows:
        return None
    ticket = rows[0]
    _client().table("oauth_states").delete().eq("email", email).eq("provider", ticket["provider"]).execute()
    if _now() - datetime.fromisoformat(ticket["created_at"]) > timedelta(hours=1):
        return None
    return ticket["provider"]


# ---------- Tokens ----------

def save_tokens(email, provider, tokens):
    """Save the tokens WHOOP or Google sent us (encrypted)."""
    row = {
        "email": email,
        "provider": provider,
        "access_token": _lock(tokens["access_token"]),
        "expires_at": (_now() + timedelta(seconds=tokens["expires_in"])).isoformat(),
    }
    # WHOOP always sends a new refresh token. Google sometimes doesn't, and
    # then we keep the old one.
    if tokens.get("refresh_token"):
        row["refresh_token"] = _lock(tokens["refresh_token"])
    _client().table("oauth_tokens").upsert(row).execute()


def get_tokens(email, provider):
    """Return the person's saved tokens (unencrypted), or None if they haven't connected."""
    rows = _client().table("oauth_tokens").select("*").eq("email", email).eq("provider", provider).execute().data
    if not rows:
        return None
    row = rows[0]
    return {
        "access_token": _unlock(row["access_token"]),
        "refresh_token": _unlock(row["refresh_token"]) if row["refresh_token"] else None,
        "expires_at": datetime.fromisoformat(row["expires_at"]),
    }


def is_connected(email, provider):
    rows = _client().table("oauth_tokens").select("provider").eq("email", email).eq("provider", provider).execute().data
    return len(rows) > 0


def delete_tokens(email, provider):
    _client().table("oauth_tokens").delete().eq("email", email).eq("provider", provider).execute()


# ---------- WHOOP data ----------

def save_daily_metrics(rows):
    """Save daily numbers. If a day is already saved, it gets updated."""
    if rows:
        _client().table("daily_metrics").upsert(rows).execute()


def save_workouts(rows):
    if rows:
        _client().table("workouts").upsert(rows).execute()


def get_daily_metrics(email, days=30):
    """The person's daily numbers for the last `days` days, newest first."""
    since = (date.today() - timedelta(days=days)).isoformat()
    return (
        _client().table("daily_metrics").select("*")
        .eq("email", email).gte("date", since)
        .order("date", desc=True).execute().data
    )


def get_workouts(email, days=30):
    """The person's workouts for the last `days` days, newest first."""
    since = (date.today() - timedelta(days=days)).isoformat()
    return (
        _client().table("workouts").select("*")
        .eq("email", email).gte("date", since)
        .order("date", desc=True).execute().data
    )


def set_weather_summary(email, day, summary):
    _client().table("daily_metrics").update({"weather_summary": summary}).eq("email", email).eq("date", day).execute()


# ---------- Training plan ----------

def get_plan(email, first_day, last_day):
    """Training plan rows between two dates (like "2026-10-10"), both included, oldest first."""
    return (
        _client().table("training_plan").select("*")
        .eq("email", email).gte("date", first_day).lte("date", last_day)
        .order("date").execute().data
    )


def save_planned_workouts(rows):
    """Save planned workouts. Each row: email, date, planned_workout. A date that's already planned gets replaced."""
    if rows:
        _client().table("training_plan").upsert(rows).execute()


def save_decision(email, day, workout, reason):
    """Save what the coach decided for one day, and why. The originally planned workout is kept."""
    _client().table("training_plan").upsert({
        "email": email, "date": day, "adjusted_workout": workout, "reason": reason,
    }).execute()


def save_event_proposal(email, day, start, minutes, title):
    """Remember the calendar time the coach suggested. It waits here until the person approves it."""
    _client().table("training_plan").upsert({
        "email": email, "date": day, "event_start": start, "event_minutes": minutes,
        "event_title": title, "event_status": "proposed",
    }).execute()


def set_event_status(email, day, status, event_id=None):
    """Mark a suggested calendar time as 'added' (with Google's event ID) or 'declined'."""
    changes = {"event_status": status}
    if event_id:
        changes["calendar_event_id"] = event_id
    _client().table("training_plan").update(changes).eq("email", email).eq("date", day).execute()


# ---------- Agent runs ----------

def save_agent_run(email, model, steps, final_decision, cost_usd, kind="plan"):
    """Save one run of the coach. kind is 'plan' (the Plan my day button) or 'chat' (one message)."""
    _client().table("agent_runs").insert({
        "email": email,
        "model": model,
        "kind": kind,
        "steps": steps,
        "final_decision": final_decision,
        "cost_usd": round(cost_usd, 5),
    }).execute()


def latest_agent_run(email):
    """The newest Plan my day run (not chat messages)."""
    rows = (
        _client().table("agent_runs").select("*").eq("email", email).eq("kind", "plan")
        .order("created_at", desc=True).limit(1).execute().data
    )
    return rows[0] if rows else None


def recent_chats(email, since):
    """Chat messages with the coach since a time (ISO text), oldest first. Each row's first
    step holds the person's question, and final_decision holds the coach's answer."""
    return (
        _client().table("agent_runs").select("*").eq("email", email).eq("kind", "chat")
        .gte("created_at", since).order("created_at").limit(40).execute().data
    )


# ---------- What the coach remembers ----------
# Short notes the coach saves about a person (like "left knee sore after long runs").
# They're kept in profiles.coach_notes, one note per line, and people can edit them
# on their Coaching profile page.

def get_coach_notes(email):
    profile = get_profile(email) or {}
    return [line for line in (profile.get("coach_notes") or "").splitlines() if line.strip()]


def save_coach_notes(email, notes):
    _client().table("profiles").update({"coach_notes": "\n".join(notes) or None}).eq("email", email).execute()


def count_agent_runs(email=None):
    """How many agent runs in the last 24 hours, for one person (or everyone if email is None)."""
    since = (_now() - timedelta(days=1)).isoformat()
    query = _client().table("agent_runs").select("id", count="exact").gte("created_at", since)
    if email:
        query = query.eq("email", email)
    return query.limit(1).execute().count
