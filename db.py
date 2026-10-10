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
