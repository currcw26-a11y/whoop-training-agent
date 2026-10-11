# ui.py: display helpers shared by the pages: icons, labels, the recovery ring,
# the week plan, and the agent's steps.
# Icons are Google's Material Symbols (built into Streamlit). Browse them at
# https://fonts.google.com/icons and write them like ":material/favorite:".

import html
from datetime import datetime, timezone

import streamlit as st

TOOL_ICONS = {
    "get_whoop_recovery": "monitor_heart", "get_whoop_sleep": "bedtime", "get_whoop_strain": "local_fire_department",
    "get_whoop_workouts": "directions_run", "get_weather": "partly_cloudy_day", "get_training_plan": "calendar_month",
    "get_training_history": "history", "update_training_plan": "edit_calendar", "save_daily_log": "save",
}

WORKOUT_ICONS = {"run": "directions_run", "walk": "directions_walk", "cross": "exercise", "rest": "bedtime", "other": "event"}


def show(value, unit="", digits=None):
    """Format a number for display, or a dash if it's missing."""
    if value is None or value != value:  # value != value is True only for "not a number" (NaN)
        return "–"
    if digits is not None:
        value = round(float(value), digits) if digits else int(round(float(value)))
    return f"{value}{unit}"


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


def label(text, icon, key):
    """A small uppercase section label with an icon (styled in style.css)."""
    with st.container(key=f"label-{key}"):
        st.markdown(f":material/{icon}: {text}")


# ---------- Pieces drawn with our own HTML (styled in style.css) ----------

def weather_chip(sky, icon_name, place):
    """A rounded pill: weather icon, temperature range, and rain chance."""
    return (
        f"<div class='weather-row'><div class='weather'><span class='msr'>{icon_name}</span>"
        f"<span class='temp'>{sky['low_f']}–{sky['high_f']}°F</span>"
        f"<span class='note'>{html.escape(sky['sky'].capitalize())} · {sky['max_rain_chance_pct']}% rain · "
        f"{html.escape(place)}</span></div></div>"
    )


def recovery_ring(score, color, zone_name, advice, when):
    """A WHOOP-style ring that fills up to the recovery score, in the zone's color.
    It's drawn with CSS (see .ring in style.css): --pct says how full, --ring-color which color."""
    has_score = score is not None and score == score
    filled = max(0, min(100, round(score))) if has_score else 0
    number = f"{filled}%" if has_score else "–"
    return f"""
<div class="ring-wrap" role="img" aria-label="Recovery {number}, {html.escape(zone_name)}">
  <div class="ring" style="--pct: {filled}; --ring-color: {color};">
    <div class="ring-center"><div class="ring-num">{number}</div><div class="ring-cap">RECOVERY</div></div>
  </div>
  <div class="zone"><span class="dot" style="background:{color}"></span>{html.escape(zone_name)}</div>
  <div class="zone-note">{html.escape(advice)} · {html.escape(when)}</div>
</div>"""


def workout_kind(text):
    """Sort a workout into run / walk / cross-training / rest, for its icon."""
    words = text.lower()
    if "rest" in words:
        return "rest"
    if any(word in words for word in ["bike", "cycl", "swim", "cross", "strength", "yoga", "elliptical"]):
        return "cross"
    if "run" in words:
        return "run"
    if "walk" in words:
        return "walk"
    return "other"


def split_workout(text):
    """Split 'Easy run, 2 miles, 7am' into a title ('Easy run') and details ('2 miles · 7am')."""
    text = (text or "").strip()
    if text.endswith(")") and "(" in text:  # "Rest day (you already ran today)"
        title, _, detail = text[:-1].partition("(")
        return title.strip(), detail.strip().capitalize()
    title, _, detail = text.partition(", ")
    return title.strip(), detail.replace(", ", " · ").strip()


def week_grid(days):
    """Seven equal cards for the week. Each item in `days` has:
    weekday, day, workout, today (True/False), changed (True/False)."""
    cards = []
    for day in days:
        title, detail = split_workout(day["workout"])
        kind = workout_kind(day["workout"]) if day["workout"] else "other"
        changed = ("<div class='changed'><span class='msr'>edit</span>Adjusted</div>"
                   if day["changed"] else "")
        cards.append(
            f"<div class='day{' today' if day['today'] else ''}'>"
            f"<div class='day-top'><span class='dow'>{'Today' if day['today'] else day['weekday']}</span>"
            f"<span class='num'>{day['day']}</span></div>"
            f"<div class='badge {kind}'><span class='msr'>{WORKOUT_ICONS[kind]}</span></div>"
            f"<div class='title'>{html.escape(title or 'Nothing planned')}</div>"
            f"<div class='detail'>{html.escape(detail)}</div>{changed}</div>"
        )
    return f"<div class='week'>{''.join(cards)}</div>"


# ---------- Showing what the agent did ----------

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
            st.markdown(f":material/sync: {step['text']}")
        elif kind == "thinking":
            st.caption(f":material/psychology: {plain(step['text'])}")
        elif kind == "note":
            st.write(plain(step["text"]))
        elif kind == "tool_call":
            asked[step["tool"]] = step["input"]  # shown together with its result, below
        elif kind == "tool_result":
            tool_input = step.get("input", asked.get(step["tool"])) or {}
            days = f" (last {tool_input['days']} days)" if "days" in tool_input else ""
            if step["failed"]:
                st.error(f"{step['tool']} failed: {step['output'].get('error')}", icon=":material/error:")
            else:
                st.markdown(
                    f":material/{TOOL_ICONS.get(step['tool'], 'build')}: **{step['tool']}**{days} → "
                    f"{plain(summarize(step['tool'], tool_input, step['output']))}"
                )
            if raw:
                st.json({"Claude asked for": tool_input, "The tool sent back": step["output"]}, expanded=1)
        elif kind == "decision":
            st.success(plain(step["text"]), icon=":material/check_circle:")
        else:  # "stopped"
            st.warning(plain(step["text"]), icon=":material/warning:")


def show_step(step):
    """Draw one step live, while the agent is running."""
    show_steps([step])
