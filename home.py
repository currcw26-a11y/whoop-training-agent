# home.py: the main page.
#   Signed out -> what the site does, and "Sign in with Google".
#   Signed in  -> 1. today's numbers  2. the coach  3. this week's plan  4. charts.
# Everything on this page is read from the database. WHOOP data gets downloaded into it first.

import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic
import pandas as pd
import requests
import streamlit as st

import agent
import charts
import config
import db
import ui
import weather
import whoop


def whoop_error(error):
    """Explain a failed WHOOP request in plain English."""
    st.error(
        f"WHOOP turned down the request (error {error.response.status_code}). "
        "Try **Disconnect** on the Coaching profile page, then connect again."
    )


@st.cache_data(ttl=1800)  # remember the forecast for 30 minutes instead of asking on every click
def cached_forecast(latitude, longitude):
    return weather.forecast(latitude, longitude)


def tile(spot, label, value, unit, compare, compare_text, history, digits=0, colors="normal"):
    """A number tile: the value, how it compares (green/red arrow), and a small trend line.
    colors: "normal" (higher is better), "inverse" (lower is better), or "off" (neither)."""
    missing = value is None or pd.isna(value)
    delta, arrow = None, "auto"
    if not missing and compare is not None and not pd.isna(compare):
        difference = round(float(value - compare), digits)
        delta = f"{difference:+.{digits}f}{unit}"
        if difference == 0:  # no change: no arrow, no color
            delta, arrow, colors = "same", "off", "off"
    trend = [v for v in history if not pd.isna(v)]
    spot.metric(
        label, ui.show(None if missing else value, unit, digits), delta=delta, delta_color=colors, delta_arrow=arrow,
        delta_description=compare_text if delta else None, chart_data=trend or None, border=True,
    )


# ---------- Signed out: the welcome page ----------
if not st.user.is_logged_in:
    st.title("🏃 Training Coach")
    st.markdown("#### An AI coach that plans your training around how your body is actually doing.")
    features = st.columns(3)
    for column, (icon, title, text) in zip(features, [
        ("⌚", "Reads your WHOOP", "Recovery, heart rate variability, sleep stages, strain, and every workout."),
        ("🌤️", "Checks the weather", "Finds a safe, comfortable time to train where you live."),
        ("🧠", "Decides today's workout", "Keeps it, eases it, swaps days, or rests, and shows every step of its reasoning."),
    ]):
        with column.container(border=True):
            st.markdown(f"### {icon}\n**{title}**")
            st.caption(text)
    st.button("Sign in with Google", type="primary", on_click=st.login)
    st.caption("Free · training suggestions only, not medical advice")
    st.page_link("privacy.py", label="Privacy policy", icon="🔒")
    st.stop()

email = st.user.email
first_name = (st.user.get("given_name") or st.user.get("name") or "there").split()[0]


# ---------- Coming back from WHOOP ----------
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
            with st.spinner("Connected! Downloading your last 90 days from WHOOP..."):
                whoop.sync(email)
            st.success("Your WHOOP is connected.")
        except requests.HTTPError as error:
            whoop_error(error)


# ---------- Connect WHOOP (first visit) ----------
if not db.is_connected(email, "whoop"):
    st.title(f"Welcome, {first_name} 👋")
    with st.container(border=True):
        st.markdown("#### Step 1: Connect your WHOOP")
        st.write("The coach needs your recovery, sleep, strain, and workouts. WHOOP will ask you to approve this.")
        # A random one-time ticket. When WHOOP sends someone back, the ticket
        # has to match, which proves it's really you finishing the connection.
        if "whoop_state" not in st.session_state:
            st.session_state.whoop_state = db.new_oauth_state(email, "whoop")
        st.link_button("Connect WHOOP", whoop.login_url(st.session_state.whoop_state), type="primary")
        st.caption("WHOOP opens in a new tab. When you're done, the coach opens there and you can close this tab.")
    st.stop()


# ---------- Header ----------
profile = db.get_profile(email)
time_zone = ZoneInfo(profile["timezone"] or "America/New_York")
now = datetime.now(time_zone)
today = now.date()
ready = bool(profile["goal"]) and profile["latitude"] is not None

part_of_day = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
st.title(f"Good {part_of_day}, {first_name}")
header = f"{now:%A, %B} {now.day}"
if profile["latitude"] is not None:
    try:
        sky = cached_forecast(profile["latitude"], profile["longitude"])["days"][0]
        header += (f" · {weather.icon(sky['sky'])} {profile['city'] or profile['place_name']}: {sky['sky']}, "
                   f"{sky['low_f']}–{sky['high_f']}°F, {sky['max_rain_chance_pct']}% chance of rain")
    except requests.RequestException:
        pass  # the weather line is a nice extra; skip it if Open-Meteo is down
st.caption(header)

if not ready:
    with st.container(border=True):
        st.markdown("**Finish setting up:** tell the coach your goal and your city so it can plan for you.")
        st.page_link("settings.py", label="Open your coaching profile", icon="⚙️")


# ---------- Load the WHOOP data (from the database) ----------
days = pd.DataFrame(db.get_daily_metrics(email, days=90))
workouts = pd.DataFrame(db.get_workouts(email, days=90))
if days.empty:
    st.info("No WHOOP data saved yet. (A brand-new WHOOP needs a few days before it scores you.)")
    if st.button("🔄 Download my WHOOP data"):
        try:
            with st.spinner("Downloading from WHOOP..."):
                whoop.sync(email)
            st.rerun()
        except requests.HTTPError as error:
            whoop_error(error)
    st.stop()

days["date"] = pd.to_datetime(days["date"])
days = charts.add_normal_range(days, "hrv_ms")
days = charts.add_normal_range(days, "resting_hr")  # both also sort oldest -> newest
if not workouts.empty:
    workouts["date"] = pd.to_datetime(workouts["date"])

latest = days.iloc[-1]
before = days.iloc[:-1].tail(30)  # the 30 days before the latest one = this person's "normal"


def normal(column):
    return before[column].mean() if column in before and before[column].notna().any() else None


# ---------- 1. Today ----------
zone_label, zone_icon, advice = charts.zone(latest["recovery_score"])
latest_day = "today" if latest["date"].date() == today else f"{latest['date']:%a, %b} {latest['date'].day}"
with st.container(border=True):
    hero, tiles = st.columns([1, 3], vertical_alignment="center", gap="large")
    with hero:
        st.caption(f"Recovery · {latest_day}")
        st.markdown(
            f"<div style='font-size:3.5rem;font-weight:650;line-height:1.1'>"
            f"{ui.show(latest['recovery_score'], '%', 0)}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(f"{zone_icon} **{(zone_label or '').split(' ')[0]}** · {advice}")
    with tiles:
        row = st.columns(4)
        recent = days.tail(14)
        tile(row[0], "HRV", latest["hrv_ms"], " ms", normal("hrv_ms"), "vs normal", recent["hrv_ms"])
        tile(row[1], "Resting HR", latest["resting_hr"], " bpm", normal("resting_hr"), "vs normal",
             recent["resting_hr"], colors="inverse")
        tile(row[2], "Sleep", latest["sleep_hours"], " h", latest.get("sleep_need_hours"), "vs needed",
             recent["sleep_hours"], digits=1)
        tile(row[3], "Day strain", latest["day_strain"], "", normal("day_strain"), "vs average",
             recent["day_strain"], digits=1, colors="off")


# ---------- 2. The coach (the agent) ----------
st.subheader("🧠 Your coach")
card = st.container(border=True)  # filled in below, after the button, so a new plan shows right away
runs_left = config.RUNS_PER_PERSON_PER_DAY - db.count_agent_runs(email)
site_full = db.count_agent_runs() >= config.RUNS_PER_SITE_PER_DAY
button_spot, note_spot = st.columns([1, 4], vertical_alignment="center")
pressed = button_spot.button(
    "✨ Plan my day", type="primary", width="stretch", disabled=not ready or runs_left <= 0 or site_full,
)

# The agent ONLY runs when the button is pressed. Streamlit re-runs this whole
# file on every click, so running it anywhere else would waste Claude credits.
if pressed:
    with st.status("The coach is working...", expanded=True) as status:
        try:
            result = agent.plan_my_day(email, ui.show_step)
            runs_left -= 1
            status.update(
                label=f"Done · used {ui.tools_used(result['steps'])} tools · cost ${result['cost']:.4f}",
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
    note_spot.caption("The coach has reached today's limit for the whole site. Try again tomorrow.")
else:
    note_spot.caption(f"{max(runs_left, 0)} of {config.RUNS_PER_PERSON_PER_DAY} runs left today · model: {config.MODEL}")

latest_run = db.latest_agent_run(email)
with card:
    if latest_run:
        first_line, _, rest = latest_run["final_decision"].partition("\n")
        st.markdown(f"#### {ui.plain(first_line)}")
        if rest.strip():
            st.write(ui.plain(rest.strip()))
        st.caption(
            f"Planned {ui.time_ago(latest_run['created_at'])} · used {ui.tools_used(latest_run['steps'])} tools · "
            f"cost ${float(latest_run['cost_usd']):.4f} · {latest_run['model']}"
        )
    else:
        st.markdown("#### Ready when you are")
        st.write("Press **Plan my day** and the coach will read your WHOOP data, check the weather, "
                 "and decide today's workout.")
if latest_run:
    with st.expander("🔍 See every step the agent took"):
        raw = st.toggle("Show the raw data Claude saw")
        ui.show_steps(latest_run["steps"], raw)


# ---------- 3. This week's plan ----------
st.subheader("📅 This week")
plan = {row["date"]: row for row in db.get_plan(email, today.isoformat(), (today + timedelta(days=6)).isoformat())}
if plan:
    for offset, column in enumerate(st.columns(7)):
        day = today + timedelta(days=offset)
        row = plan.get(day.isoformat(), {})
        workout = row.get("adjusted_workout") or row.get("planned_workout") or "–"
        with column.container(border=True):
            st.markdown(f"**{'Today' if offset == 0 else f'{day:%a}'}** · {day.day}")
            st.markdown(f"<div style='font-size:0.9rem;line-height:1.35'>{html.escape(workout)}</div>",
                        unsafe_allow_html=True)
            if row.get("adjusted_workout") and row.get("planned_workout") and row["adjusted_workout"] != row["planned_workout"]:
                st.caption("✏️ changed by the coach")
else:
    st.caption("No plan yet. Press **Plan my day** and the coach will write one.")


# ---------- 4. Charts ----------
st.subheader("📊 Your data")
range_spot, refresh_spot = st.columns([3, 1], vertical_alignment="center")
span = range_spot.segmented_control(
    "Time range", ["7 days", "30 days", "90 days"], default="30 days", required=True, label_visibility="collapsed",
)
if refresh_spot.button("🔄 Refresh WHOOP data", width="stretch"):
    try:
        with st.spinner("Downloading from WHOOP..."):
            whoop.sync(email)
        st.rerun()
    except requests.HTTPError as error:
        whoop_error(error)
st.caption(f"Last downloaded from WHOOP {ui.time_ago(profile['whoop_synced_at'])}. "
           "Everything here is read from the database. Hover any chart for exact numbers.")

count = int(span.split()[0])
start = pd.Timestamp(today - timedelta(days=count - 1))
shown = days[days["date"] >= start]
shown_workouts = workouts[workouts["date"] >= start] if not workouts.empty else workouts
weeks = max(4, count // 7)

recovery_tab, sleep_tab, training_tab, insights_tab, data_tab = st.tabs(
    ["💚 Recovery", "😴 Sleep", "🏃 Training", "💡 Insights", "📋 All data"]
)

with recovery_tab:
    st.markdown("**Recovery each morning**")
    st.altair_chart(charts.recovery_chart(shown), width="stretch",
                    alt=f"Bar chart of daily WHOOP recovery over the last {count} days, colored by zone.")
    hrv_spot, rhr_spot = st.columns(2, gap="large")
    with hrv_spot:
        st.markdown("**Heart rate variability (HRV)**")
        st.caption("Higher usually means more recovered. The shaded band is your normal range.")
        st.altair_chart(charts.trend_chart(shown, "hrv_ms", "HRV", "ms"), width="stretch",
                        alt="Line chart of daily HRV with your normal range shaded.")
    with rhr_spot:
        st.markdown("**Resting heart rate**")
        st.caption("Lower usually means more recovered. The shaded band is your normal range.")
        st.altair_chart(charts.trend_chart(shown, "resting_hr", "Resting HR", "bpm"), width="stretch",
                        alt="Line chart of daily resting heart rate with your normal range shaded.")
    st.markdown("**Other signals**")
    st.caption("Big changes in these can be an early sign of getting sick.")
    others = st.columns(3)
    tile(others[0], "Blood oxygen", latest.get("spo2"), "%", normal("spo2"), "vs normal", shown["spo2"], 1, "off")
    tile(others[1], "Skin temperature", latest.get("skin_temp_c"), " °C", normal("skin_temp_c"), "vs normal",
         shown["skin_temp_c"], 1, "off")
    tile(others[2], "Breathing rate", latest.get("respiratory_rate"), " /min", normal("respiratory_rate"),
         "vs normal", shown["respiratory_rate"], 1, "off")

with sleep_tab:
    sleep_tiles = st.columns(4)
    tile(sleep_tiles[0], "Average sleep", shown["sleep_hours"].mean(), " h", shown["sleep_need_hours"].mean(),
         "vs needed", shown["sleep_hours"], 1)
    tile(sleep_tiles[1], "Sleep debt", latest.get("sleep_debt_hours"), " h", None, None, shown["sleep_debt_hours"], 1)
    tile(sleep_tiles[2], "Sleep performance", shown["sleep_performance"].mean(), "%", None, None,
         shown["sleep_performance"])
    tile(sleep_tiles[3], "Consistency", shown["sleep_consistency"].mean(), "%", None, None, shown["sleep_consistency"])
    st.markdown("**Sleep stages each night**")
    st.caption("The gray tick on each bar is how much sleep WHOOP says you needed that night.")
    st.altair_chart(charts.sleep_chart(shown), width="stretch",
                    alt="Stacked bar chart of deep, REM, light, and awake hours each night, with sleep needed marked.")
    st.markdown("**When you slept**")
    st.caption("Each bar runs from falling asleep (top) to waking up (bottom). Bars that line up mean a steady schedule.")
    bedtimes = charts.bedtime_chart(shown, time_zone)
    if bedtimes is None:
        st.caption("No bedtimes saved yet. Press Refresh WHOOP data.")
    else:
        st.altair_chart(bedtimes, width="stretch", alt="Chart of the time you fell asleep and woke up each night.")

with training_tab:
    runs = (shown_workouts[shown_workouts["sport"].str.contains("run", case=False, na=False)]
            if not shown_workouts.empty else shown_workouts)
    training_tiles = st.columns(4)
    training_tiles[0].metric("Workouts", len(shown_workouts), border=True)
    training_tiles[1].metric("Running miles", ui.show(runs["distance_miles"].sum() if not runs.empty else 0, "", 1),
                             border=True)
    tile(training_tiles[2], "Average day strain", shown["day_strain"].mean(), "", None, None, shown["day_strain"], 1)
    training_tiles[3].metric("Workout minutes", int(shown_workouts["minutes"].sum()) if not shown_workouts.empty else 0,
                             border=True)
    miles_spot, effort_spot = st.columns(2, gap="large")
    with miles_spot:
        st.markdown(f"**Running miles per week** (last {weeks} weeks)")
        st.caption("Raising this by about 10% a week at most helps avoid injuries.")
        st.altair_chart(charts.weekly_miles_chart(workouts, today, weeks), width="stretch",
                        alt=f"Bar chart of running miles for each of the last {weeks} weeks.")
    with effort_spot:
        st.markdown(f"**How hard you trained** (last {weeks} weeks)")
        st.caption("Minutes in easy, moderate, and hard heart-rate zones each week.")
        effort = charts.effort_chart(workouts, today, weeks)
        if effort is None:
            st.caption("No heart-rate zone data yet. Press Refresh WHOOP data.")
        else:
            st.altair_chart(effort, width="stretch",
                            alt="Stacked bar chart of easy, moderate, and hard training minutes per week.")
    st.markdown("**Day strain**")
    st.caption("How hard each whole day was on your body, on WHOOP's 0-21 scale.")
    st.altair_chart(charts.strain_chart(shown), width="stretch", alt="Bar chart of WHOOP day strain each day.")

with insights_tab:
    st.caption("Patterns in your own data that the WHOOP app doesn't show directly. "
               "Longer time ranges give more reliable patterns.")
    found = charts.insights(shown, shown_workouts, time_zone)
    if found:
        for pair_start in range(0, len(found), 2):
            for spot, (title, headline, explanation) in zip(st.columns(2), found[pair_start:pair_start + 2]):
                with spot.container(border=True):
                    st.caption(title)
                    st.markdown(f"### {headline}")
                    st.write(explanation)
    else:
        st.info("Not enough data for patterns yet. Try the 90-day range, or check back in a couple of weeks.")
    scatter_spot, weekday_spot = st.columns(2, gap="large")
    with scatter_spot:
        st.markdown("**Hours of sleep vs. next-morning recovery**")
        st.caption("Each dot is one morning. The gray line shows the overall trend.")
        scatter = charts.sleep_vs_recovery_chart(shown)
        if scatter is None:
            st.caption("Needs at least 5 nights.")
        else:
            st.altair_chart(scatter, width="stretch", alt="Scatter plot of hours asleep against next-morning recovery.")
    with weekday_spot:
        st.markdown("**Your best day of the week**")
        st.caption("Average recovery for each weekday. Your best day is highlighted.")
        weekdays = charts.weekday_chart(shown)
        if weekdays is None:
            st.caption("Needs at least 2 weeks. Try the 30- or 90-day range.")
        else:
            st.altair_chart(weekdays, width="stretch", alt="Bar chart of average recovery for each day of the week.")

with data_tab:
    st.markdown("**Every day**")
    columns = {
        "date": "Date", "recovery_score": "Recovery %", "hrv_ms": "HRV (ms)", "resting_hr": "Resting HR",
        "spo2": "Blood oxygen %", "skin_temp_c": "Skin temp (°C)", "sleep_hours": "Sleep (h)",
        "sleep_need_hours": "Needed (h)", "sleep_debt_hours": "Debt (h)", "deep_sleep_hours": "Deep (h)",
        "rem_sleep_hours": "REM (h)", "light_sleep_hours": "Light (h)", "awake_hours": "Awake (h)",
        "sleep_performance": "Sleep perf. %", "sleep_efficiency": "Efficiency %", "sleep_consistency": "Consistency %",
        "respiratory_rate": "Breaths/min", "day_strain": "Strain", "calories": "Calories", "steps": "Steps",
        "avg_hr": "Avg HR", "max_hr": "Max HR", "weather_summary": "Weather",
    }
    st.dataframe(
        shown.sort_values("date", ascending=False)[[c for c in columns if c in shown]].rename(columns=columns),
        column_config={"Date": st.column_config.DateColumn(format="ddd, MMM D")},
        hide_index=True,
    )
    st.markdown("**Workouts**")
    if shown_workouts.empty:
        st.caption("No workouts in this time range.")
    else:
        st.dataframe(
            shown_workouts[["date", "sport", "minutes", "distance_miles", "strain", "avg_hr", "max_hr", "calories"]]
            .rename(columns={"date": "Date", "sport": "Workout", "minutes": "Minutes", "distance_miles": "Miles",
                             "strain": "Strain", "avg_hr": "Avg HR", "max_hr": "Max HR", "calories": "Calories"}),
            column_config={"Date": st.column_config.DateColumn(format="ddd, MMM D")},
            hide_index=True,
        )
