# ui.py: small display helpers shared by the pages.

from datetime import datetime, timezone

import streamlit as st

TOOL_ICONS = {
    "get_whoop_recovery": "💚", "get_whoop_sleep": "😴", "get_whoop_strain": "🔥", "get_whoop_workouts": "🏃",
    "get_weather": "🌤️", "get_training_plan": "📅", "get_training_history": "📜",
    "update_training_plan": "✏️", "save_daily_log": "💾",
}


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
