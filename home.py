# home.py: the main page.
#   Signed out -> a "Sign in with Google" button.
#   Signed in  -> connect WHOOP, download the latest data, and show it.
# The numbers in section 4 are read from the database, not straight from WHOOP.

from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

import db
import whoop


def show(value, unit=""):
    """Format a number for display, or a dash if it's missing."""
    return "–" if value is None else f"{value}{unit}"


def time_ago(saved_time):
    """Turn a saved timestamp into something like '5 min ago'."""
    if not saved_time:
        return "never"
    minutes = int((datetime.now(timezone.utc) - datetime.fromisoformat(saved_time)).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} hours ago"
    return f"{minutes // (24 * 60)} days ago"


def whoop_error(error):
    """Explain a failed WHOOP request in plain English."""
    st.error(
        f"WHOOP turned down the request (error {error.response.status_code}). "
        "Try **Disconnect WHOOP** in the sidebar, then connect again."
    )


# ---------- 1. Sign in ----------
if not st.user.is_logged_in:
    st.title("🏃 Training Coach")
    st.write(
        "An AI coach that reads your WHOOP recovery and sleep, checks the weather "
        "and your calendar, and plans your training each day."
    )
    st.button("Sign in with Google", type="primary", on_click=st.login)
    st.caption("Training suggestions only, not medical advice.")
    st.stop()

email = st.user.email
first_name = (st.user.get("given_name") or st.user.get("name") or "there").split()[0]

# Add the person to the database the first time we see them (once per visit).
if "profile_ready" not in st.session_state:
    db.ensure_profile(email, st.user.get("name"))
    st.session_state.profile_ready = True

with st.sidebar:
    st.write(f"Signed in as **{email}**")
    st.button("Sign out", on_click=st.logout)


# ---------- 2. Coming back from WHOOP ----------
# After someone approves on WHOOP's site, WHOOP sends them back to this page
# with ?code=...&state=... added to the web address.
params = st.query_params.to_dict()
if "state" in params:
    st.query_params.clear()  # tidy the address bar so this only happens once
    provider = db.use_oauth_state(email, params["state"])
    if provider != "whoop":
        st.error("That connection link expired or wasn't yours. Please press **Connect WHOOP** again.")
    elif "code" not in params:
        st.warning("WHOOP wasn't connected. You may have pressed Cancel.")
    else:
        try:
            whoop.finish_connecting(email, params["code"])
            with st.spinner("Connected! Downloading your last 2 weeks from WHOOP..."):
                whoop.sync(email)
            st.success("Your WHOOP is connected.")
        except requests.HTTPError as error:
            whoop_error(error)

st.title(f"Hi, {first_name} 👋")


# ---------- 3. Connect WHOOP ----------
if not db.is_connected(email, "whoop"):
    st.subheader("Connect your WHOOP")
    st.write("The coach needs your recovery, sleep, strain, and workouts. WHOOP will ask you to approve this.")
    # A random one-time ticket. When WHOOP sends someone back, the ticket
    # has to match, which proves it's really you finishing the connection.
    if "whoop_state" not in st.session_state:
        st.session_state.whoop_state = db.new_oauth_state(email, "whoop")
    st.link_button("Connect WHOOP", whoop.login_url(st.session_state.whoop_state), type="primary")
    st.caption("WHOOP opens in a new tab. When you're done, the coach opens there and you can close this tab.")
    st.stop()

with st.sidebar:
    if st.button("Disconnect WHOOP"):
        db.delete_tokens(email, "whoop")
        st.rerun()


# ---------- 4. Your WHOOP data (read from the database) ----------
if st.button("🔄 Get my latest WHOOP data"):
    try:
        with st.spinner("Downloading from WHOOP..."):
            saved = whoop.sync(email)
        st.toast(f"Saved {saved} days of WHOOP data.")
    except requests.HTTPError as error:
        whoop_error(error)

profile = db.get_profile(email)
st.caption(
    f"Last downloaded from WHOOP: {time_ago(profile['whoop_synced_at'])}. "
    "Everything below is read from the database."
)

days = db.get_daily_metrics(email, days=30)
if not days:
    st.info("No WHOOP data saved yet. Press the button above. (A brand-new WHOOP needs a few days before it scores you.)")
    st.stop()

# Number tiles for the most recent day.
latest = days[0]
latest_date = datetime.strptime(latest["date"], "%Y-%m-%d")
st.subheader(f"{latest_date:%A, %B} {latest_date.day}")
tiles = st.columns(5)
tiles[0].metric("Recovery", show(latest["recovery_score"], "%"))
tiles[1].metric("HRV", show(latest["hrv_ms"], " ms"))
tiles[2].metric("Resting HR", show(latest["resting_hr"], " bpm"))
tiles[3].metric("Sleep", show(latest["sleep_hours"], " h"))
tiles[4].metric("Day strain", show(latest["day_strain"]))

# Recovery over time. Hover over the line to see each day's number.
st.subheader("Recovery, last 30 days")
table = pd.DataFrame(days)
table["date"] = pd.to_datetime(table["date"])
st.line_chart(table, x="date", y="recovery_score", x_label="", y_label="Recovery (%)")

# Every day as a table (the same numbers the chart uses).
st.subheader("Daily numbers")
st.dataframe(
    table[["date", "recovery_score", "hrv_ms", "resting_hr", "sleep_hours", "sleep_performance", "day_strain"]]
    .rename(columns={
        "date": "Date",
        "recovery_score": "Recovery %",
        "hrv_ms": "HRV (ms)",
        "resting_hr": "Resting HR",
        "sleep_hours": "Sleep (h)",
        "sleep_performance": "Sleep performance %",
        "day_strain": "Strain",
    }),
    column_config={"Date": st.column_config.DateColumn(format="ddd, MMM D")},
    hide_index=True,
)

st.subheader("Recent workouts")
workouts = db.get_workouts(email, days=30)
if workouts:
    st.dataframe(
        pd.DataFrame(workouts)[["date", "sport", "minutes", "distance_miles", "strain", "avg_hr"]]
        .rename(columns={
            "date": "Date",
            "sport": "Workout",
            "minutes": "Minutes",
            "distance_miles": "Miles",
            "strain": "Strain",
            "avg_hr": "Avg HR",
        }),
        hide_index=True,
    )
else:
    st.caption("No workouts in the last 30 days.")
