# home.py: the main page. Same layout as the demo (docs/index.html), with live data.
#   Signed out -> what the site does, and "Sign in with Google".
#   Signed in  -> 1. today's numbers  2. the coach + today's calendar  3. the sleep plan  4. the week
#                 5. trends  6. insights.
# WHOOP numbers are read from the database (downloaded into it first). Calendar events come
# straight from Google Calendar, remembered for 5 minutes.
# The look comes from style.css and .streamlit/config.toml.

import html
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic
import pandas as pd
import requests
import streamlit as st

import agent
import charts
import config
import db
import google_calendar
import personal
import sleep_plan
import ui
import weather
import whoop

EXAM = re.compile(r"\b(exam|midterm|final|quiz|test)s?\b", re.IGNORECASE)


def whoop_error(error):
    """Explain a failed WHOOP request in plain English."""
    st.error(
        f"WHOOP turned down the request (error {error.response.status_code}). "
        "Try **Disconnect** on the Coaching profile page, then connect again.",
        icon=":material/error:",
    )


@st.cache_data(ttl=1800, show_spinner=False)  # remember the forecast for 30 minutes
def cached_forecast(latitude, longitude):
    return weather.forecast(latitude, longitude)


@st.cache_data(ttl=300, show_spinner=False)  # ask Google Calendar at most every 5 minutes
def week_events(email, monday, time_zone_name):
    zone = ZoneInfo(time_zone_name)
    start = datetime.combine(date.fromisoformat(monday), time(0), zone)
    return google_calendar.list_events(email, start, start + timedelta(days=7), zone)


@st.cache_data(ttl=300, show_spinner=False)
def sleep_events(email, first_morning, time_zone_name):
    """Events for the 7 mornings after tonight, for the sleep plan's wake-up times."""
    zone = ZoneInfo(time_zone_name)
    start = datetime.combine(date.fromisoformat(first_morning), time(0), zone)
    return google_calendar.list_events(email, start, start + timedelta(days=7), zone)


def calendar_box(email, plan, connected, time_zone):
    """Ask to add the coach's suggested time to Google Calendar. Nothing goes on the
    calendar without this click. It shows the next suggestion this week (it may be on another day)."""
    waiting = [row for _, row in sorted(plan.items())
               if row.get("event_start") and row.get("event_status") == "proposed"]
    if waiting:
        row = waiting[0]
        day = row["date"]
        begins = datetime.fromisoformat(row["event_start"]).astimezone(time_zone)
        minutes = row.get("event_minutes") or 30
        day_name = "Today" if begins.date() == datetime.now(time_zone).date() else f"{begins:%a, %b} {begins.day}"
        when = f"{day_name}, {ui.clock(begins)}–{ui.clock(begins + timedelta(minutes=minutes))}"
        title = row.get("event_title") or "Workout"
        with st.container(key="approve-event"):
            st.markdown(f":material/calendar_add_on: **Add to your Google Calendar?** &nbsp;{when} · {title}")
            yes, no, _ = st.columns([1.4, 1, 2.6])
            if yes.button("Add to calendar", icon=":material/event_available:", type="primary", key="approve-yes"):
                try:
                    event_id = google_calendar.save_workout_event(
                        email, row.get("calendar_event_id"), begins, minutes, title,
                        f"{row.get('reason') or ''}\n\nPlanned by Training Coach.", str(time_zone),
                    )
                    db.set_event_status(email, day, "added", event_id)
                    week_events.clear()  # so the new event shows up right away
                    sleep_events.clear()
                    st.toast("Added to your Google Calendar.", icon=":material/event_available:")
                    st.rerun()
                except requests.HTTPError as error:
                    st.error(f"Google Calendar said no (error {error.response.status_code}). Try connecting it "
                             "again on your Coaching profile.", icon=":material/error:")
            if no.button("No thanks", key="approve-no"):
                db.set_event_status(email, day, "declined")
                st.rerun()
    elif not connected:
        st.caption(":material/calendar_month: Connect Google Calendar on your Coaching profile, "
                   "and the coach will plan around your classes.")


def chart_card(key, title, subtitle, chart, alt_text, legend="", note=""):
    """A card with a title, a one-line explanation, an optional color legend, and an Altair chart
    (or a note if there's no data yet). An optional note goes under the chart."""
    with st.container(key=f"card-chart-{key}", height="stretch"):  # as tall as the card next to it
        ui.draw(f'<div class="chart-title">{title}</div><div class="chart-sub">{subtitle}</div>{legend}')
        if chart is None:
            st.caption("Not enough data yet.")
        else:
            st.altair_chart(chart, width="stretch", alt=alt_text)
        if note:
            ui.draw(note)


# ---------- Signed out: the welcome page ----------
if not st.user.is_logged_in:
    ui.label("AI training coach", "directions_run", "welcome")
    st.title("Train with your body, not against it.")
    st.html("<p class='lead'>Training Coach reads your WHOOP recovery and sleep, checks the weather and your "
            "calendar, and plans each day's workout. Then it shows you exactly how it decided.</p>")
    st.button("Sign in with Google", icon=":material/login:", type="primary", on_click=st.login)
    st.write("")
    for number, (column, (icon, title, text)) in enumerate(zip(st.columns(3, gap="medium"), [
        ("watch", "Reads your WHOOP", "Recovery, heart rate variability, sleep stages, strain, and every workout."),
        ("calendar_month", "Plans around your day", "Checks the weather and your Google Calendar for free time."),
        ("psychology", "Decides today's workout", "Keeps it, eases it, swaps days, or rests, and shows every step."),
    ]), start=1):
        with column.container(key=f"card-feature-{number}"):
            st.markdown(f":material/{icon}:")
            st.markdown(f"**{title}**")
            st.caption(text)
    st.caption("Free · training suggestions only, not medical advice")
    st.page_link("privacy.py", label="Privacy policy", icon=":material/lock:")
    st.stop()

email = st.user.email
full_name = st.user.get("name") or email
first_name = (st.user.get("given_name") or full_name).split()[0]
initials = "".join(part[0] for part in full_name.split()[:2]).upper()


# ---------- Coming back from WHOOP or Google ----------
# After someone approves on WHOOP's or Google's site, they're sent back to this page
# with ?code=...&state=... added to the web address.
params = st.query_params.to_dict()
if "state" in params:
    st.query_params.clear()  # tidy the address bar so this only happens once
    provider = db.use_oauth_state(email, params["state"])  # "whoop" or "google", or None if the ticket is bad
    service = {"whoop": "WHOOP", "google": "Google Calendar"}.get(provider)
    if provider is None:
        st.error("That connection link expired or wasn't yours. Please press **Connect** again.", icon=":material/error:")
    elif "code" not in params:
        st.warning(f"{service} wasn't connected. You may have pressed Cancel.", icon=":material/warning:")
    elif provider == "whoop":
        try:
            whoop.finish_connecting(email, params["code"])
            with st.spinner("Connected! Downloading your last 90 days from WHOOP..."):
                whoop.sync(email)
            st.success("Your WHOOP is connected.", icon=":material/check_circle:")
        except requests.HTTPError as error:
            whoop_error(error)
    else:  # Google Calendar
        try:
            missing = google_calendar.finish_connecting(email, params["code"])
            if missing:
                st.warning("Google Calendar is connected, but some boxes were unchecked on Google's screen, so the "
                           "coach can't see or add events. Disconnect it on your Coaching profile and connect again, "
                           "checking every box.", icon=":material/warning:")
            else:
                st.success("Google Calendar is connected. The coach will now plan around your schedule.",
                           icon=":material/check_circle:")
        except requests.HTTPError as error:
            st.error(f"Google turned down the request (error {error.response.status_code}). Try connecting again.",
                     icon=":material/error:")


# ---------- Connect WHOOP (first visit) ----------
if not db.is_connected(email, "whoop"):
    st.title(f"Welcome, {first_name}")
    with st.container(key="card-connect"):
        ui.label("Step 1 of 2", "link", "connect")
        st.markdown("#### Connect your WHOOP")
        st.write("The coach needs your recovery, sleep, strain, and workouts. WHOOP will ask you to approve this.")
        # A random one-time ticket. When WHOOP sends someone back, the ticket
        # has to match, which proves it's really you finishing the connection.
        if "whoop_state" not in st.session_state:
            st.session_state.whoop_state = db.new_oauth_state(email, "whoop")
        st.link_button("Connect WHOOP", whoop.login_url(st.session_state.whoop_state), type="primary",
                       icon=":material/watch:")
        st.caption("WHOOP opens in a new tab. When you're done, the coach opens there and you can close this tab.")
    st.stop()


# ---------- Header: date, greeting, weather, account ----------
profile = db.get_profile(email)
time_zone = ZoneInfo(profile["timezone"] or "America/New_York")
now = datetime.now(time_zone)
today = now.date()
ready = bool(profile["goal"]) and profile["latitude"] is not None
calendar_connected = db.is_connected(email, "google")

greeting, side = st.columns([1.3, 1], vertical_alignment="bottom")
with greeting:
    ui.label(f"{now:%A, %B} {now.day}", "today", "date")
    part_of_day = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
    st.title(f"Good {part_of_day}, {first_name}")
with side.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
    if profile["latitude"] is not None:
        try:
            sky = cached_forecast(profile["latitude"], profile["longitude"])["days"][0]
            st.html(ui.weather_chip(sky, weather.icon(sky["sky"]), profile["city"] or profile["place_name"]),
                    width="content")
        except requests.RequestException:
            pass  # the weather pill is a nice extra; skip it if Open-Meteo is down
    with st.container(key="account", width="content"):
        with st.popover(initials):
            st.markdown(f"**{full_name}**")
            st.caption(email)
            st.page_link("settings.py", label="Coaching profile", icon=":material/tune:")
            st.button("Sign out", icon=":material/logout:", on_click=st.logout, width="stretch")

if not ready:
    with st.container(key="card-setup"):
        st.markdown("**Finish setting up:** tell the coach your goal and your city so it can plan for you.")
        st.page_link("settings.py", label="Open your coaching profile", icon=":material/tune:")


# ---------- Load the WHOOP data (from the database) ----------
# Get the newest WHOOP data by itself: once per visit, if the last download was over 3 hours ago.
synced = profile["whoop_synced_at"]
stale = not synced or datetime.now(timezone.utc) - datetime.fromisoformat(synced) > timedelta(hours=3)
if stale and not st.session_state.get("auto_synced"):
    st.session_state.auto_synced = True  # only try once per visit, even if it fails
    try:
        with st.spinner("Getting your latest WHOOP data..."):
            whoop.sync(email)
        profile = db.get_profile(email)
    except requests.RequestException:
        st.toast("Couldn't reach WHOOP just now. Showing your saved data.", icon=":material/cloud_off:")

days = pd.DataFrame(db.get_daily_metrics(email, days=90))
workouts = pd.DataFrame(db.get_workouts(email, days=90))
if days.empty:
    st.info("No WHOOP data saved yet. (A brand-new WHOOP needs a few days before it scores you.)",
            icon=":material/info:")
    if st.button("Download my WHOOP data", icon=":material/sync:"):
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
recent = days.tail(14)


def normal(column):
    return before[column].mean() if column in before and before[column].notna().any() else None


def change(column, compare):
    value = latest.get(column)
    return None if value is None or pd.isna(value) or compare is None or pd.isna(compare) else value - compare


# ---------- 1. Today: ring + tiles ----------
zone_label, _, advice = charts.zone(latest["recovery_score"])
zone_color = {charts.ZONES[0]: charts.GREEN, charts.ZONES[1]: charts.YELLOW, charts.ZONES[2]: charts.RED}.get(
    zone_label, "#898781")
zone_word = (zone_label or "No score").split(" (")[0]
previous = days.iloc[-2]["recovery_score"] if len(days) > 1 else None
note = (f"{'Up' if latest['recovery_score'] >= previous else 'Down'} from {previous:.0f}% yesterday"
        if previous is not None and not pd.isna(previous) and not pd.isna(latest["recovery_score"]) else advice)
if latest["date"].date() != today:
    note = f"Latest score: {latest['date']:%a, %b} {latest['date'].day}"

with st.container(key="card-today"):
    ring_spot, tiles_spot = st.columns([1, 3.4], vertical_alignment="center", gap="large")
    with ring_spot:
        ui.draw(ui.recovery_ring(latest["recovery_score"], zone_color, zone_word,
                                 advice if note == advice else f"{advice} · {note[0].lower()}{note[1:]}"))
    with tiles_spot:
        ui.draw(ui.tiles(
            ui.tile("monitor_heart", "HRV", latest["hrv_ms"], "ms", change("hrv_ms", normal("hrv_ms")),
                    compared_to="vs normal", trend=recent["hrv_ms"].tolist()),
            ui.tile("favorite", "Resting HR", latest["resting_hr"], "bpm", change("resting_hr", normal("resting_hr")),
                    higher_is_better=False, compared_to="vs normal", trend=recent["resting_hr"].tolist()),
            ui.tile("bedtime", "Sleep", latest["sleep_hours"], "h", change("sleep_hours", latest.get("sleep_need_hours")),
                    digits=1, compared_to="vs needed", trend=recent["sleep_hours"].tolist()),
            ui.tile("local_fire_department", "Strain", latest["day_strain"], "", None, digits=1,
                    compared_to="so far today" if latest["date"].date() == today else "that day",
                    trend=recent["day_strain"].tolist()),
        ))


# ---------- Google Calendar events ----------
monday = today - timedelta(days=today.weekday())
plan = {row["date"]: row for row in db.get_plan(email, monday.isoformat(), (today + timedelta(days=6)).isoformat())}


def calendar_blocks(first_day, plan_rows):
    """Everything to draw for the week starting first_day: the person's Google Calendar events, plus
    the coach's suggested times still waiting for an OK. Returns (blocks, error message or None)."""
    events, error = [], None
    if calendar_connected:
        try:
            events = week_events(email, first_day.isoformat(), str(time_zone))
        except requests.HTTPError:
            error = "Couldn't read your Google Calendar. If this keeps happening, connect it again on your Coaching profile."
    found = [{
        "start": e["start"], "end": e["end"], "title": e["title"], "all_day": e["all_day"],
        # Google names your main calendar after your email address; "Your calendar" reads better.
        "calendar": "Your calendar" if "@" in e["calendar"] else e["calendar"],
        # Colors stay the same every week: the coach's workouts orange, your main calendar blue,
        # your second calendar green, any others gray.
        "kind": "coach" if e["from_coach"] else ("cal1", "cal2")[e.get("calendar_rank", 0)]
        if e.get("calendar_rank", 0) < 2 else "other",
        "rank": e.get("calendar_rank", 0), "exam": bool(EXAM.search(e["title"])), "suggested": False,
    } for e in events]
    for row in plan_rows:
        if row.get("event_status") == "proposed" and row.get("event_start"):
            begins = datetime.fromisoformat(row["event_start"]).astimezone(time_zone)
            found.append({"start": begins, "end": begins + timedelta(minutes=row.get("event_minutes") or 30),
                          "title": row.get("event_title") or "Workout", "calendar": "Training Coach",
                          "all_day": False, "kind": "coach", "rank": -1, "exam": False, "suggested": True})
    return found, error


blocks, calendar_error = calendar_blocks(monday, plan.values())  # this week (for today's list)


# ---------- 2. The coach + today's calendar ----------
st.write("")
coach_side, agenda_side = st.columns([1.55, 1], gap="medium")

with coach_side.container(key="card-coach", height="stretch"):
    decision_spot = st.container()  # filled in below, after the button, so a new plan shows right away
    approval_spot = st.container()
    with st.container(horizontal=True, vertical_alignment="center", key="coach-buttons"):
        pressed = st.button("Plan my day", icon=":material/auto_awesome:", type="primary", disabled=not ready)
        st.page_link("chat.py", label="Ask the coach", icon=":material/forum:")

    # The agent ONLY runs when the button is pressed. Streamlit re-runs this whole
    # file on every click, so running it anywhere else would waste Claude credits.
    if pressed and not agent.can_run(email):
        st.info(agent.LIMIT_MESSAGE, icon=":material/hourglass_top:")
    elif pressed:
        with st.status("The coach is working...", expanded=True) as status:
            try:
                result = agent.plan_my_day(email, ui.show_step)
                week_events.clear()
                status.update(label=f"Done · used {ui.tools_used(result['steps'])} tools", state="complete",
                              expanded=False)
                plan = {row["date"]: row for row in
                        db.get_plan(email, monday.isoformat(), (today + timedelta(days=6)).isoformat())}
            except anthropic.APIError as error:
                problem, explanation = ui.claude_problem(error)
                status.update(label=problem, state="error")
                st.error(explanation)

    latest_run = db.latest_agent_run(email)
    today_row = plan.get(today.isoformat(), {})
    with decision_spot:
        label_spot, meta_spot = st.columns([1, 1.3])
        with label_spot:
            ui.label("Today's workout", "auto_awesome", "workout")
        if latest_run:
            with meta_spot.container(key="meta-run"):
                st.caption(f"Planned {ui.time_ago(latest_run['created_at'])} · "
                           f"{ui.tools_used(latest_run['steps'])} tools · {latest_run['model']}")
            saved_workout = today_row.get("adjusted_workout") or today_row.get("planned_workout")
            title, detail = ui.split_workout(saved_workout or latest_run["final_decision"].split(". ")[0])
            when = ""
            if today_row.get("event_start") and today_row.get("event_status") in ("proposed", "added"):
                begins = datetime.fromisoformat(today_row["event_start"]).astimezone(time_zone)
                ends = begins + timedelta(minutes=today_row.get("event_minutes") or 30)
                when = f"{ui.clock(begins)}–{ui.clock(ends)}" + (f" · {detail}" if detail else "")
            elif detail:
                when = detail
            ui.draw(f'<div class="headline">{html.escape(title)}</div>'
                    + (f'<div class="when"><span class="msr">schedule</span>{html.escape(when)}</div>' if when else ""))
            # The coach's first line repeats the workout, so show only the explanation after it.
            first_line, _, explanation = latest_run["final_decision"].partition("\n")
            st.write(ui.plain(explanation.strip() or first_line))
            tags = []
            if today_row.get("event_status") == "added":
                tags.append(("event_available", "Added to Google Calendar", "on-cal"))
            if (today_row.get("adjusted_workout") and today_row.get("planned_workout")
                    and today_row["adjusted_workout"] != today_row["planned_workout"]):
                tags.append(("edit_calendar", "Adjusted by the coach", ""))
            ui.draw(ui.badges(tags))
        else:
            ui.draw('<div class="headline">Ready when you are</div>')
            st.write("Press **Plan my day** and the coach will read your WHOOP data, check the weather and your "
                     "calendar, and decide today's workout.")
    with approval_spot:
        calendar_box(email, plan, calendar_connected, time_zone)
    if latest_run:
        with st.expander("How the coach decided", icon=":material/account_tree:"):
            raw = st.toggle("Show the raw data Claude saw")
            ui.show_steps(latest_run["steps"], raw)

with agenda_side.container(key="card-agenda", height="stretch"):
    # An all-day event can last several days, so it counts on every day it covers.
    todays = sorted((b for b in blocks if b["start"].date() == today
                     or (b["all_day"] and b["start"].date() <= today < b["end"].date())),
                    key=lambda b: (not b["all_day"], b["start"]))
    label_spot, meta_spot = st.columns([3, 1])
    with label_spot:
        ui.label("Today · Google Calendar", "event", "agenda")
    if calendar_connected and not calendar_error:
        with meta_spot.container(key="meta-agenda"):
            st.caption(f"{len(todays)} event{'s' if len(todays) != 1 else ''}")
    if calendar_error:
        st.caption(calendar_error)
    elif not calendar_connected:
        ui.draw('<p class="empty-note">Connect Google Calendar and your classes and events will show up here, '
                'so the coach can plan around them.</p>')
        st.page_link("settings.py", label="Connect Google Calendar", icon=":material/link:")
    elif not todays:
        ui.draw('<p class="empty-note">Nothing on your calendar today.</p>')
    else:
        ui.draw(ui.agenda(todays))


# ---------- 3. Sleep plan: when to go to bed and wake up ----------
st.write("")
first_night = sleep_plan.tonight(now)
morning_events = []
if calendar_connected:
    try:
        morning_events = sleep_events(email, (first_night + timedelta(days=1)).isoformat(), str(time_zone))
    except requests.HTTPError:
        pass  # without the calendar, wake-up times assume nothing early
nights = sleep_plan.plan_week(
    first_night, morning_events,
    db.get_plan(email, first_night.isoformat(), (first_night + timedelta(days=7)).isoformat()),
    personal.sleep_habits(days, time_zone),
    None if pd.isna(latest.get("sleep_need_hours")) else latest["sleep_need_hours"], time_zone,
)
ui.label("Sleep plan · tonight and the week ahead", "bedtime", "sleep")
with st.container(key="card-sleep"):
    ui.draw(ui.sleep_card(nights, now))


# ---------- 4. The week, on the calendar (last week, this week, or next week) ----------
st.write("")
label_spot, week_spot = st.columns([2.2, 1.6], vertical_alignment="center")
choice = week_spot.segmented_control(
    "Week", ["Last week", "This week", "Next week"], default="This week", required=True,
    label_visibility="collapsed", key="week-choice",
)
first_day = monday + timedelta(weeks={"Last week": -1, "This week": 0, "Next week": 1}[choice])
last_day = first_day + timedelta(days=6)
with label_spot:
    ui.label(f"Your week · {first_day:%b} {first_day.day} – {last_day:%b} {last_day.day}", "calendar_month", "week")

week_plan = {row["date"]: row for row in db.get_plan(email, first_day.isoformat(), last_day.isoformat())}
week_blocks, week_error = calendar_blocks(first_day, week_plan.values())  # remembered 5 minutes, so this is quick
workout_days = set(workouts["date"].dt.date) if not workouts.empty else set()
timed = [b for b in week_blocks if not b["all_day"]]
week = []
for offset in range(7):
    day = first_day + timedelta(days=offset)
    row = week_plan.get(day.isoformat())
    chip = None
    if row and (row.get("adjusted_workout") or row.get("planned_workout")):
        workout = row.get("adjusted_workout") or row.get("planned_workout")
        kind = ui.workout_kind(workout)
        name = ui.split_workout(workout)[0]
        done = day < today and (kind == "rest" or day in workout_days)
        chip = (ui.WORKOUT_ICONS[kind], name if len(name) <= 20 else name[:19] + "…", done)
    week.append({
        "date": day, "is_today": day == today, "plan": chip,
        "events": [b for b in timed if b["start"].date() == day],
        # An all-day event belongs to every day from its start date up to (not including) its end date.
        "all_day": [b for b in week_blocks if b["all_day"] and b["start"].date() <= day < b["end"].date()],
    })
starts = [b["start"].hour for b in timed]
ends = [b["end"].hour + (1 if b["end"].minute else 0) for b in timed if b["end"].date() == b["start"].date()]
first_hour = max(5, min([7] + starts))
last_hour = min(24, max([22] + ends))

names = {}
for b in week_blocks:
    if b["kind"] in ("cal1", "cal2"):
        names[b["rank"]] = b["calendar"]
week_legend = [f'<span><i class="swatch" style="background:var({"--s1" if rank == 0 else "--s3"})"></i>{html.escape(name)}</span>'
          for rank, name in sorted(names.items())]
if any(b["kind"] == "other" for b in week_blocks):
    week_legend.append('<span><i class="swatch" style="background:var(--muted)"></i>Other calendars</span>')
week_legend.append('<span><i class="swatch" style="background:var(--s2)"></i>Training Coach workouts</span>')
if any(b["suggested"] for b in week_blocks):
    week_legend.append('<span><i class="swatch dashed"></i>Suggested, waiting for your OK</span>')
if any(b["exam"] for b in week_blocks):
    week_legend.append("<span>★ Exam</span>")
if week_error:
    st.caption(week_error)
with st.container(key="card-week"):
    ui.draw(ui.week_calendar(week, first_hour, last_hour, now)
            + f'<div class="wk-foot"><div class="legend">{"".join(week_legend)}</div>'
            f'<div class="note">The coach only adds a workout after you approve it.</div></div>')


# ---------- 5. Trends ----------
st.write("")
label_spot, range_spot, refresh_spot = st.columns([2.2, 1.6, 1], vertical_alignment="center")
span = range_spot.segmented_control(
    "Time range", ["7 days", "30 days", "90 days"], default="30 days", required=True, label_visibility="collapsed",
)
count = int(span.split()[0])
with label_spot:
    ui.label(f"Trends · last {count} days", "insights", "trends")
if refresh_spot.button("Refresh WHOOP", icon=":material/sync:", width="stretch"):
    try:
        with st.spinner("Downloading from WHOOP..."):
            whoop.sync(email)
        st.rerun()
    except requests.HTTPError as error:
        whoop_error(error)

start_day = today - timedelta(days=count - 1)  # every chart shows this whole range, even days with no data
shown = days[days["date"] >= pd.Timestamp(start_day)]
shown_workouts = workouts[workouts["date"] >= pd.Timestamp(start_day)] if not workouts.empty else workouts
weeks = max(4, count // 7)
if days["date"].min().date() > start_day:
    st.caption(f"Your WHOOP data starts {days['date'].min():%b} {days['date'].min().day}, so the left side is empty.")


def chart_legend(*items):
    """A row of colored swatches with labels, drawn above a chart."""
    return '<div class="legend chart-legend">' + "".join(
        f'<span><i class="{"swatch dashed" if color == "dashed" else "swatch"}" '
        f'style="{"" if color == "dashed" else f"background:{color}"}"></i>{text}</span>' for color, text in items) + "</div>"


# The running card: miles done (WHOOP) and planned (the coach), plus the goal.
weekly = charts.weekly_running(workouts, list(db.get_plan(email, monday.isoformat(),
                                                           (monday + timedelta(days=13)).isoformat())), today, weeks)
this_week, last_week = weekly[weekly["when"] == "now"].iloc[0], weekly[weekly["when"] == "past"].iloc[-1]
if this_week["planned"] > 0:
    run_note = (f'<div class="run-note"><span class="msr">check_circle</span><span><b>{this_week["done"]:g} of '
                f'{this_week["planned"]:g} miles</b> done this week · {last_week["done"]:g} last week</span></div>')
else:
    run_note = ('<div class="run-note"><span class="msr">info</span>No runs planned this week yet. '
                'Ask the coach, or press Plan my day.</div>')
goal = profile.get("goal") or "Set a goal on your Coaching profile"
if profile.get("goal_date"):
    goal_day = date.fromisoformat(profile["goal_date"])
    weeks_left = (goal_day - today).days // 7
    goal += f" · {goal_day:%b} {goal_day.day}" + (f" ({weeks_left} weeks away)" if weeks_left >= 2 else "")

top_left, top_right = st.columns(2, gap="medium")
with top_left:
    chart_card("recovery", "Recovery each morning", "How ready your body is to train, from WHOOP.",
               charts.recovery_chart(shown, start_day, today),
               f"Bar chart of daily recovery over the last {count} days, colored by zone.",
               chart_legend(("var(--good)", "Green 67–100%"), ("var(--warn)", "Yellow 34–66%"), ("var(--bad)", "Red 0–33%")))
with top_right:
    chart_card("hrv", "Heart rate variability", "Higher usually means more recovered.",
               charts.trend_chart(shown, "hrv_ms", "HRV", "ms", start_day, today),
               "Line chart of daily HRV with your normal range shaded.",
               chart_legend(("var(--s1)", "Your HRV (ms)"), ("var(--band)", "Your normal range")))
bottom_left, bottom_right = st.columns(2, gap="medium")
with bottom_left:
    chart_card("sleep", "Sleep stages", "Each night, stacked. The gray tick is how much sleep you needed.",
               charts.sleep_chart(shown, start_day, today),
               "Stacked bar chart of sleep stages each night, with sleep needed marked.",
               chart_legend(("var(--s1)", "Deep"), ("var(--s2)", "REM"), ("var(--s3)", "Light"), ("var(--s4)", "Awake"),
                      ("var(--muted)", "Needed")))
with bottom_right:
    chart_card("miles", "Running miles per week", f"Goal: {html.escape(goal)}",
               charts.weekly_miles_chart(weekly), f"Bar chart of running miles for the last {weeks} weeks and next week's plan.",
               chart_legend(("var(--s1)", "Miles run"), ("dashed", "Planned by the coach")), run_note)

with st.expander("More charts", icon=":material/stacked_line_chart:"):
    left, right = st.columns(2, gap="large")
    with left:
        ui.draw('<div class="chart-title">Resting heart rate</div><div class="chart-sub">Lower usually means more recovered.</div>')
        resting = charts.trend_chart(shown, "resting_hr", "Resting HR", "bpm", start_day, today)
        if resting is not None:
            st.altair_chart(resting, width="stretch", alt="Line chart of daily resting heart rate with your normal range.")
        ui.draw('<div class="chart-title">How hard you trained</div><div class="chart-sub">Minutes in easy, moderate, and hard heart-rate zones each week.</div>'
                + chart_legend(("#86b6ef", "Easy (zones 1-2)"), ("#2a78d6", "Moderate (zone 3)"), ("#184f95", "Hard (zones 4-5)")))
        effort = charts.effort_chart(workouts, today, weeks)
        if effort is None:
            st.caption("No heart-rate zone data yet.")
        else:
            st.altair_chart(effort, width="stretch", alt="Stacked bar chart of training minutes by effort each week.")
        ui.draw('<div class="chart-title">Sleep vs. next-morning recovery</div><div class="chart-sub">Each dot is one morning. The gray line is the trend.</div>')
        scatter = charts.sleep_vs_recovery_chart(shown)
        if scatter is None:
            st.caption("Needs at least 5 nights.")
        else:
            st.altair_chart(scatter, width="stretch", alt="Scatter plot of hours asleep against recovery.")
    with right:
        ui.draw('<div class="chart-title">When you slept</div><div class="chart-sub">Falling asleep (top) to waking up (bottom). Bars that line up mean a steady schedule.</div>')
        bedtimes = charts.bedtime_chart(shown, time_zone, start_day, today)
        if bedtimes is None:
            st.caption("No bedtimes saved yet.")
        else:
            st.altair_chart(bedtimes, width="stretch", alt="Chart of when you fell asleep and woke up each night.")
        ui.draw('<div class="chart-title">Day strain</div><div class="chart-sub">How hard each whole day was on your body (0-21).</div>')
        st.altair_chart(charts.strain_chart(shown, start_day, today), width="stretch", alt="Bar chart of WHOOP day strain.")
        ui.draw('<div class="chart-title">Your best day of the week</div><div class="chart-sub">Average recovery for each weekday.</div>')
        weekdays = charts.weekday_chart(shown)
        if weekdays is None:
            st.caption("Needs at least 2 weeks of data.")
        else:
            st.altair_chart(weekdays, width="stretch", alt="Bar chart of average recovery by weekday.")


# ---------- 6. Insights ----------
st.write("")
ui.label("Insights · patterns in your own data", "lightbulb", "insights")
found = charts.insights(shown, shown_workouts, time_zone)
if found:
    ui.draw(ui.insight_cards(found))
else:
    st.info("Not enough data for patterns in this time range yet. Try 30 or 90 days.", icon=":material/info:")
st.write("")

with st.expander("See every number as a table", icon=":material/table_chart:"):
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
    if not shown_workouts.empty:
        st.dataframe(
            shown_workouts[["date", "sport", "minutes", "distance_miles", "strain", "avg_hr", "max_hr", "calories"]]
            .rename(columns={"date": "Date", "sport": "Workout", "minutes": "Minutes", "distance_miles": "Miles",
                             "strain": "Strain", "avg_hr": "Avg HR", "max_hr": "Max HR", "calories": "Calories"}),
            column_config={"Date": st.column_config.DateColumn(format="ddd, MMM D")},
            hide_index=True,
        )
    st.caption(f"WHOOP data last downloaded {ui.time_ago(profile['whoop_synced_at'])}.")

ui.draw(f'<div class="site-foot"><div><b>Training Coach</b> · training suggestions only, not medical advice</div>'
        f'<div>Coach model: {config.MODEL}</div></div>')
