# home.py: the main page.
#   Signed out -> a "Sign in with Google" button.
#   Signed in  -> connect WHOOP, set your goal and city, press "Plan my day",
#                 and see your WHOOP numbers.
# The numbers on this page are read from the database, not straight from WHOOP.

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic
import pandas as pd
import requests
import streamlit as st

import agent
import config
import db
import weather
import whoop

RUN_TIMES = ["Early morning (6-9am)", "Late morning (9am-12pm)", "Midday (12-3pm)", "Afternoon (3-6pm)", "Evening (6-9pm)"]

TOOL_ICONS = {
    "get_whoop_recovery": "💚", "get_whoop_sleep": "😴", "get_whoop_strain": "🔥", "get_whoop_workouts": "🏃",
    "get_weather": "🌤️", "get_training_plan": "📅", "get_training_history": "📜",
    "update_training_plan": "✏️", "save_daily_log": "💾",
}


def show(value, unit=""):
    """Format a number for display, or a dash if it's missing."""
    return "–" if value is None else f"{value}{unit}"


def plain(text):
    """Streamlit treats $...$ as math. Escape dollar signs so text shows as written."""
    return text.replace("$", "\\$")


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


def tools_used(steps):
    return sum(1 for step in steps if step["kind"] == "tool_call")


def summarize(tool, tool_input, output):
    """One short, plain-English line about what a tool sent back to Claude."""
    try:
        if tool == "get_whoop_recovery":
            day, normal = output["days"][0], output["normal_30_day"]
            return f"latest recovery {day['recovery_score']}%, HRV {day['hrv_ms']} ms (normal {normal['hrv_ms']})"
        if tool == "get_whoop_sleep":
            night = output["nights"][0]
            return f"last night {night['sleep_hours']} h asleep, {night['sleep_need_hours']} h needed"
        if tool == "get_whoop_strain":
            return f"latest day strain {output['days'][0]['day_strain']}"
        if tool == "get_whoop_workouts":
            return (f"{len(output['workouts'])} workouts, {output['running_miles_last_7_days']} running miles this week "
                    f"({output['running_miles_7_to_14_days_ago']} the week before)")
        if tool == "get_weather":
            today = output["days"][0]
            return f"{today['sky']}, {today['low_f']}–{today['high_f']}°F, {today['max_rain_chance_pct']}% chance of rain"
        if tool == "get_training_plan":
            return f"{len(output['plan'])} days already planned" if output["plan"] else "no plan yet"
        if tool == "get_training_history":
            return f"{len(output['history'])} past days" if output["history"] else "no history yet"
        if tool == "update_training_plan":
            return f"wrote {output['saved']} days of the plan"
        if tool == "save_daily_log":
            return f"saved today's decision: {tool_input['workout']}"
    except (KeyError, IndexError, TypeError):
        pass  # no data, or an older format: fall back to a plain "done"
    return "done"


def show_steps(steps, raw=False):
    """Draw the agent's steps. With raw=True, also show the exact data each tool sent Claude."""
    asked = {}  # what Claude asked each tool for (older saved runs only have it on the call step)
    for step in steps:
        kind = step["kind"]
        if kind == "sync":
            st.write(f"⌚ {step['text']}")
        elif kind == "thinking":
            st.caption(f"💭 {plain(step['text'])}")
        elif kind == "note":
            st.write(plain(step["text"]))
        elif kind == "tool_call":
            asked[step["tool"]] = step["input"]  # shown together with its result, below
        elif kind == "tool_result":
            tool_input = step.get("input", asked.get(step["tool"])) or {}
            days = f" (last {tool_input['days']} days)" if "days" in tool_input else ""
            if step["failed"]:
                st.error(f"{step['tool']} failed: {step['output'].get('error')}")
            else:
                st.markdown(
                    f"{TOOL_ICONS.get(step['tool'], '🔧')} **{step['tool']}**{days} → "
                    f"{plain(summarize(step['tool'], tool_input, step['output']))}"
                )
            if raw:
                st.json({"Claude asked for": tool_input, "The tool sent back": step["output"]}, expanded=1)
        elif kind == "decision":
            st.success(plain(step["text"]))
        else:  # "stopped"
            st.warning(plain(step["text"]))


def show_step(step):
    """Draw one step live, while the agent is running."""
    show_steps([step])


@st.cache_data(ttl=1800)  # remember the forecast for 30 minutes instead of asking on every click
def cached_forecast(latitude, longitude):
    return weather.forecast(latitude, longitude)


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


# ---------- 4. Your coaching profile ----------
profile = db.get_profile(email)
ready = bool(profile["goal"]) and profile["latitude"] is not None

with st.expander("⚙️ Your coaching profile", expanded=not ready):
    if not ready:
        st.info("Tell the coach your goal and your city so it can plan for you.")
    with st.form("profile"):
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
                st.rerun()
    if profile["place_name"]:
        st.caption(f"Weather location: {profile['place_name']} · time zone: {profile['timezone']}")


# ---------- 5. Today's plan (the agent) ----------
st.header("🧠 Today's plan")

if profile["latitude"] is not None:
    try:
        sky = cached_forecast(profile["latitude"], profile["longitude"])["days"][0]
        st.caption(
            f"🌤️ {profile['place_name']} today: {sky['sky']}, {sky['low_f']}–{sky['high_f']}°F, "
            f"{sky['max_rain_chance_pct']}% chance of rain"
        )
    except requests.RequestException:
        st.caption("The weather is unavailable right now.")

runs_left = config.RUNS_PER_PERSON_PER_DAY - db.count_agent_runs(email)
site_full = db.count_agent_runs() >= config.RUNS_PER_SITE_PER_DAY
pressed = st.button("✨ Plan my day", type="primary", disabled=not ready or runs_left <= 0 or site_full)

# The agent ONLY runs when the button is pressed. Streamlit re-runs this whole
# file on every click, so running it anywhere else would waste Claude credits.
if pressed:
    with st.status("The coach is working...", expanded=True) as status:
        try:
            result = agent.plan_my_day(email, show_step)
            runs_left -= 1
            status.update(
                label=f"Done · used {tools_used(result['steps'])} tools · cost ${result['cost']:.4f}",
                state="complete", expanded=False,
            )
        except anthropic.AuthenticationError:
            status.update(label="Claude didn't accept the API key", state="error")
            st.error("The `anthropic_api_key` in secrets.toml isn't valid.")
        except anthropic.BadRequestError as error:
            status.update(label="Claude couldn't run", state="error")
            if "credit" in error.message.lower():
                st.error("The Claude account is out of credits. Add more at console.anthropic.com.")
            else:
                st.error(f"Claude said the request was invalid: {error.message}")
        except anthropic.RateLimitError:
            status.update(label="Claude is busy", state="error")
            st.error("Too many requests to Claude right now. Wait a minute and try again.")
        except anthropic.APIConnectionError:
            status.update(label="Couldn't reach Claude", state="error")
            st.error("Couldn't connect to Claude. Check the internet connection and try again.")
        except anthropic.APIStatusError as error:
            status.update(label="Claude had a problem", state="error")
            st.error(f"Claude returned an error ({error.status_code}). Try again in a minute.")

if site_full:
    st.caption("The coach has reached today's limit for the whole site. Try again tomorrow.")
else:
    st.caption(f"{max(runs_left, 0)} of {config.RUNS_PER_PERSON_PER_DAY} runs left today · model: {config.MODEL}")

latest = db.latest_agent_run(email)
if latest:
    with st.container(border=True):
        first_line, _, rest = latest["final_decision"].partition("\n")
        st.markdown(f"#### {plain(first_line)}")
        if rest.strip():
            st.write(plain(rest.strip()))
        st.caption(
            f"Planned {time_ago(latest['created_at'])} · used {tools_used(latest['steps'])} tools · "
            f"cost ${float(latest['cost_usd']):.4f} · {latest['model']}"
        )
    with st.expander("🔍 See every step the agent took"):
        raw = st.toggle("Show the raw data Claude saw")
        show_steps(latest["steps"], raw)

today = datetime.now(ZoneInfo(profile["timezone"] or "America/New_York")).date()
week = db.get_plan(email, today.isoformat(), (today + timedelta(days=6)).isoformat())
if week:
    st.subheader("This week's plan")
    st.dataframe(
        pd.DataFrame([{
            "Day": f"{date.fromisoformat(row['date']):%a, %b} {date.fromisoformat(row['date']).day}",
            "Planned": row["planned_workout"],
            "Coach's change": row["adjusted_workout"] or "",
        } for row in week]),
        hide_index=True,
    )


# ---------- 6. Your WHOOP data (read from the database) ----------
st.header("⌚ Your WHOOP data")
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
latest_day = days[0]
latest_date = datetime.strptime(latest_day["date"], "%Y-%m-%d")
st.subheader(f"{latest_date:%A, %B} {latest_date.day}")
tiles = st.columns(5)
tiles[0].metric("Recovery", show(latest_day["recovery_score"], "%"))
tiles[1].metric("HRV", show(latest_day["hrv_ms"], " ms"))
tiles[2].metric("Resting HR", show(latest_day["resting_hr"], " bpm"))
tiles[3].metric("Sleep", show(latest_day["sleep_hours"], " h"))
tiles[4].metric("Day strain", show(latest_day["day_strain"]))

# Recovery over time. Hover over the line to see each day's number.
st.subheader("Recovery, last 30 days")
table = pd.DataFrame(days)
table["date"] = pd.to_datetime(table["date"])
st.line_chart(table, x="date", y="recovery_score", x_label="", y_label="Recovery (%)")

# Every day as a table (the same numbers the chart uses, plus more).
st.subheader("Daily numbers")
columns = {
    "date": "Date",
    "recovery_score": "Recovery %",
    "hrv_ms": "HRV (ms)",
    "resting_hr": "Resting HR",
    "sleep_hours": "Sleep (h)",
    "sleep_need_hours": "Sleep needed (h)",
    "deep_sleep_hours": "Deep (h)",
    "rem_sleep_hours": "REM (h)",
    "sleep_performance": "Sleep performance %",
    "day_strain": "Strain",
    "calories": "Calories",
    "weather_summary": "Weather",
}
st.dataframe(
    table[[column for column in columns if column in table]].rename(columns=columns),
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
