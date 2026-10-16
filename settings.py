# settings.py: the "Coaching profile" page.
# Your goal, city, and run times (the coach uses all of them), what the coach remembers
# about you, and your connections.

from datetime import date

import requests
import streamlit as st

import config
import db
import google_calendar
import ui
import weather
import whoop

RUN_TIMES = list(config.RUN_TIME_WINDOWS)  # "Early morning (6-9am)", ... (defined in config.py)

email = st.user.email
profile = db.get_profile(email)

ui.label("Settings", "tune", "settings")
st.title("Coaching profile")
st.caption("The coach reads this before every plan and every message.")

with st.container(key="card-profile"):
    with st.form("profile", border=False):
        goal = st.text_input(
            "What are you training for?", profile["goal"] or "",
            placeholder="Run my first 10K, finish a half marathon, get fitter...",
        )
        goal_date = st.date_input(
            "Goal date (optional)",
            value=date.fromisoformat(profile["goal_date"]) if profile["goal_date"] else None,
        )
        experience = st.text_area(
            "What do you do now?", profile["experience"] or "",
            placeholder="I run 2-3 miles a few times a week",
        )
        city = st.text_input("Your city (for the weather)", profile["city"] or "", placeholder="Winston-Salem")
        saved_times = [time for time in (profile["run_times"] or "").split(", ") if time in RUN_TIMES]
        run_times = st.multiselect("When are you willing to run?", RUN_TIMES, default=saved_times)

        if st.form_submit_button("Save", type="primary"):
            changes = {
                "goal": goal.strip(),
                "goal_date": goal_date.isoformat() if goal_date else None,
                "experience": experience.strip(),
                "run_times": ", ".join(run_times),
            }
            city_ok = True
            if city.strip() and (city.strip() != profile["city"] or profile["latitude"] is None):
                place = weather.find_city(city)  # also tells us the person's time zone
                if place:
                    changes.update(place, city=city.strip())
                else:
                    city_ok = False
                    st.error(f'Couldn\'t find a city called "{city}". Try just the city name.')
            if city_ok:
                db.update_profile(email, changes)
                st.toast("Saved.")
                st.rerun()

    if profile["place_name"]:
        st.caption(f"Weather location: {profile['place_name']} · time zone: {profile['timezone']}")

# What the coach has learned about you from your messages (agent.remember_about_them).
ui.label("What the coach remembers", "bookmark", "memory")
with st.container(key="card-memory"):
    notes = db.get_coach_notes(email)
    if notes:
        st.caption("The coach saved these from your messages and reads them every time. Edit or delete any line.")
        edited = st.text_area("Notes", "\n".join(notes), height=max(110, 28 * len(notes) + 30),
                              label_visibility="collapsed")
        with st.container(horizontal=True):
            if st.button("Save notes", icon=":material/check:"):
                db.save_coach_notes(email, [line.strip() for line in edited.splitlines() if line.strip()])
                st.toast("Saved.")
                st.rerun()
            if st.button("Forget everything", icon=":material/delete:", type="tertiary"):
                db.save_coach_notes(email, [])
                st.rerun()
    else:
        st.markdown("Nothing yet. Tell the coach about an injury, what you like, or your weekly schedule on "
                    "**Ask the coach**, and it'll remember.")

ui.label("Connections", "link", "connections")
with st.container(key="card-whoop-link"):
    left, right = st.columns([3, 1], vertical_alignment="center")
    if db.is_connected(email, "whoop"):
        left.markdown(":material/watch: **WHOOP** · connected")
        left.caption(f"Your data downloads by itself when you open the site. Last download: "
                     f"{ui.time_ago(profile['whoop_synced_at'])}.")
        if left.button("Download all 90 days again", icon=":material/download:", type="tertiary"):
            try:
                with st.spinner("Downloading from WHOOP..."):
                    saved = whoop.sync(email, days=90)
                st.toast(f"Saved {saved} days.")
            except requests.HTTPError as error:
                st.error(f"WHOOP turned down the request (error {error.response.status_code}). "
                         "Try Disconnect, then connect again.")
        if right.button("Disconnect", icon=":material/link_off:", key="disconnect-whoop"):
            db.delete_tokens(email, "whoop")
            st.rerun()
    else:
        left.markdown(":material/watch: **WHOOP** · not connected")
        right.page_link("home.py", label="Connect")
with st.container(key="card-calendar-link"):
    left, right = st.columns([3, 1], vertical_alignment="center")
    if db.is_connected(email, "google"):
        left.markdown(":material/calendar_month: **Google Calendar** · connected")
        left.caption("The coach sees your events to find free time. It only adds a workout when you approve it.")
        if right.button("Disconnect", icon=":material/link_off:", key="disconnect-google"):
            google_calendar.disconnect(email)
            st.rerun()
    else:
        left.markdown(":material/calendar_month: **Google Calendar** · not connected")
        left.caption("Lets the coach see your classes and free time and suggest a workout time. "
                     "Nothing is added unless you approve it.")
        # A one-time ticket, like the WHOOP one, so only you can finish connecting.
        if "google_state" not in st.session_state:
            st.session_state.google_state = db.new_oauth_state(email, "google")
        right.link_button("Connect", google_calendar.login_url(st.session_state.google_state, email),
                          icon=":material/link:", type="primary")

ui.label("Account", "account_circle", "account")
with st.container(key="card-account"):
    left, right = st.columns([3, 1], vertical_alignment="center")
    left.markdown(f":material/account_circle: **{st.user.get('name') or email}** · {email}")
    right.button("Sign out", icon=":material/logout:", on_click=st.logout, key="signout-settings")

