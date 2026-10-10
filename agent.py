# agent.py: the AI coach.
#
# How it works (the approach from Anthropic's "Building Effective Agents"):
#   1. We send Claude the person's info, instructions, and a list of TOOLS it may use.
#   2. Claude replies by asking to use one or more tools ("get my sleep data").
#   3. Our code runs those tools and sends Claude the results.
#   4. Repeat until Claude stops asking for tools and writes its answer,
#      or until MAX_STEPS rounds, whichever comes first.
# Every step is recorded so the website can show exactly what the agent did.

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic
import requests
import streamlit as st

import config
import db
import weather
import whoop

# ---------- The instructions Claude gets every time ----------

SYSTEM_PROMPT = """You are the coach inside Training Coach, a website that plans one person's training each day from their WHOOP data, the weather, and their training plan.

Each run, do this:
1. Gather the facts. In your first turn, call all of these together: get_whoop_recovery, get_whoop_sleep, get_whoop_strain, get_whoop_workouts, get_weather, get_training_plan, and get_training_history.
2. If get_training_plan has nothing planned for the coming week, write a plan for today and the next 13 days with update_training_plan. Start from what the person does now (their recent WHOOP workouts and what they wrote about themselves) and build toward their goal. Raise weekly running distance by no more than about 10% a week, include at least 2 rest or cross-training days a week, put the longest run on a weekend, and use run/walk for newer runners. If they gave a goal date, aim to get there safely; if the goal looks unrealistic for that date, plan safely anyway and say so.
3. Decide today's workout: keep what was planned, make it easier, swap it with another day this week (update both days with update_training_plan), or rest.
4. Call save_daily_log once with today's final workout, the reason, and a one-line weather summary.
5. Reply to the person. First line: today's workout, in a few words. Then 2 to 3 short sentences explaining why, quoting the numbers that mattered (for example "Recovery 41%, down from 70% yesterday"). If weather matters, say the best time to go. Keep the whole reply under 80 words: the plan itself is shown separately, so don't describe it. Plain text only, no headings or bullet points.

How to decide:
- WHOOP recovery: 67-100% is green (the planned workout is fine), 34-66% is yellow (consider making it easier), 0-33% is red (easy movement or rest).
- Compare HRV and resting heart rate with the person's 30-day normal from get_whoop_recovery. HRV well below normal, or resting heart rate well above normal, means they are less recovered than usual.
- If recovery was red on 2 of the last 3 days, or has been falling for several days, choose rest or very easy movement. Never stack hard days on low recovery.
- Sleep well short of their sleep need, or a large sleep debt, means make it easier.
- Avoid two hard days in a row; check yesterday's strain and recent workouts.
- Weather: avoid thunderstorms, heavy rain, ice, and feels-like temperatures above about 90°F or below about 20°F. Pick a better hour within the times they are willing to run, or suggest an indoor option.
- You give training suggestions, not medical advice. If the numbers could point to illness or injury (several red days in a row, resting heart rate far above normal, high skin temperature, low blood oxygen), recommend rest, and suggest seeing a doctor or athletic trainer if it continues.

Notes:
- Dates are written YYYY-MM-DD. Treat the date in the first message as today.
- If a tool returns an error or no data, work with what you have and mention it briefly.
- You can't see the person's calendar yet."""


# ---------- The tools Claude may use (name, what it does, and what to send) ----------

def _days_input(default):
    return {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": f"How many days back to look, 1 to 30. Default {default}."},
        },
    }


TOOLS = [
    {
        "name": "get_whoop_recovery",
        "description": "The person's daily WHOOP recovery, newest day first: recovery score (0-100%), heart rate "
                       "variability (HRV, ms), resting heart rate (bpm), blood oxygen (%), and skin temperature (°C). "
                       "Also returns their 30-day averages under 'normal_30_day' so you can tell if today is unusual. "
                       "Use it to judge how ready their body is to train.",
        "input_schema": _days_input(7),
    },
    {
        "name": "get_whoop_sleep",
        "description": "The person's sleep for recent nights, newest first: hours asleep vs. hours needed, sleep debt, "
                       "sleep performance/efficiency/consistency (%), deep/REM/light/awake hours, disturbances, "
                       "breathing rate, bedtime, and wake time (local 24-hour clock). Use it to see if they are rested.",
        "input_schema": _days_input(7),
    },
    {
        "name": "get_whoop_strain",
        "description": "The person's daily WHOOP strain (0-21, how hard the whole day was on their body), calories, "
                       "average and max heart rate, and steps, newest first. Use it to spot hard days and fatigue.",
        "input_schema": _days_input(7),
    },
    {
        "name": "get_whoop_workouts",
        "description": "The person's recorded WHOOP workouts, newest first: date, sport, minutes, miles, strain, heart "
                       "rate, calories, and minutes in heart-rate zones 0-5. Also returns their running miles for the "
                       "last 7 days and the 7 days before that, to keep weekly increases small.",
        "input_schema": _days_input(14),
    },
    {
        "name": "get_weather",
        "description": "Today's and tomorrow's forecast where the person lives: sky, high/low, rain chance, sunrise, "
                       "sunset, UV, and an hour-by-hour forecast from 05:00 to 21:00 (temperature, feels-like, rain "
                       "chance, wind). Use it to pick a safe, comfortable time to train.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_training_plan",
        "description": "The person's training plan from today through the next 13 days: each day's planned workout "
                       "and, if the coach already changed it, the adjusted workout. An empty plan means none has "
                       "been written yet; create one with update_training_plan.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_training_history",
        "description": "Past days of the training plan (before today), oldest first: what was planned, what the coach "
                       "recommended instead, and why. Use it to see how training has been going.",
        "input_schema": _days_input(14),
    },
    {
        "name": "update_training_plan",
        "description": "Write or change planned workouts for today or future days (up to 28 days ahead). Use it to "
                       "create a plan when none exists, or to move a workout to another day. A date that already "
                       "has a planned workout gets replaced. Don't use it for today's final decision; that goes in "
                       "save_daily_log.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workouts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "YYYY-MM-DD"},
                            "workout": {"type": "string", "description": "Like 'Easy run/walk, 2.5 miles' or 'Rest day'"},
                        },
                        "required": ["date", "workout"],
                    },
                },
            },
            "required": ["workouts"],
        },
    },
    {
        "name": "save_daily_log",
        "description": "Save today's final decision. Call it exactly once, at the end, after you've decided.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workout": {"type": "string", "description": "Today's final workout, like 'Easy run/walk, 2 miles at 7am'"},
                "reason": {"type": "string", "description": "One or two sentences on why, with the key numbers"},
                "weather_summary": {"type": "string", "description": "One line, like 'Sunny, 58-74°F, 10% rain'"},
            },
            "required": ["workout", "reason", "weather_summary"],
        },
    },
]


# ---------- What each tool actually does ----------
# Each one gets `person` (who we're planning for) and `tool_input` (what Claude asked for),
# and returns a dictionary that gets sent back to Claude as the result.

def _days(tool_input, default):
    """Read 'days' from Claude's request, kept between 1 and 30."""
    return max(1, min(int(tool_input.get("days", default)), 30))


def _average(rows, key):
    values = [row[key] for row in rows if row.get(key) is not None]
    return round(sum(values) / len(values), 1) if values else None


def _keep(row, keys):
    """Copy only some columns, so Claude gets a short, clear table."""
    return {key: row.get(key) for key in keys}


def get_whoop_recovery(person, tool_input):
    month = db.get_daily_metrics(person["email"], days=30)
    keys = ["date", "recovery_score", "hrv_ms", "resting_hr", "spo2", "skin_temp_c"]
    return {
        "days": [_keep(row, keys) for row in month[: _days(tool_input, 7)]],
        "normal_30_day": {key: _average(month, key) for key in ["recovery_score", "hrv_ms", "resting_hr", "skin_temp_c"]},
    }


def get_whoop_sleep(person, tool_input):
    def clock(time):  # "2026-10-10T03:12:00+00:00" -> "23:12" in their time zone
        return datetime.fromisoformat(time).astimezone(person["zone"]).strftime("%H:%M") if time else None

    nights = []
    for row in db.get_daily_metrics(person["email"], days=30)[: _days(tool_input, 7)]:
        night = _keep(row, ["date", "sleep_hours", "sleep_need_hours", "sleep_debt_hours", "sleep_performance",
                            "sleep_efficiency", "sleep_consistency", "deep_sleep_hours", "rem_sleep_hours",
                            "light_sleep_hours", "awake_hours", "disturbances", "respiratory_rate"])
        night["bedtime"] = clock(row.get("sleep_start"))
        night["wake_time"] = clock(row.get("sleep_end"))
        nights.append(night)
    return {"nights": nights}


def get_whoop_strain(person, tool_input):
    keys = ["date", "day_strain", "calories", "avg_hr", "max_hr", "steps"]
    return {"days": [_keep(row, keys) for row in db.get_daily_metrics(person["email"], days=30)[: _days(tool_input, 7)]]}


def get_whoop_workouts(person, tool_input):
    workouts = db.get_workouts(person["email"], days=30)
    today = person["today"]
    first_shown = (today - timedelta(days=_days(tool_input, 14))).isoformat()
    week_ago = (today - timedelta(days=7)).isoformat()
    two_weeks_ago = (today - timedelta(days=14)).isoformat()

    def running_miles(after, up_to):
        return round(sum(w["distance_miles"] or 0 for w in workouts
                         if "run" in w["sport"].lower() and after < w["date"] <= up_to), 1)

    keys = ["date", "sport", "minutes", "distance_miles", "strain", "avg_hr", "max_hr", "calories", "zone_minutes"]
    return {
        "workouts": [_keep(w, keys) for w in workouts if w["date"] > first_shown],
        "running_miles_last_7_days": running_miles(week_ago, today.isoformat()),
        "running_miles_7_to_14_days_ago": running_miles(two_weeks_ago, week_ago),
    }


def get_weather(person, tool_input):
    profile = person["profile"]
    if profile.get("latitude") is None:
        raise ValueError("The person hasn't saved a city, so there's no weather.")
    return {"location": profile["place_name"], **weather.forecast(profile["latitude"], profile["longitude"])}


def _with_weekday(row, keys):
    day = _keep(row, keys)
    day["weekday"] = date.fromisoformat(row["date"]).strftime("%a")  # "Sat"
    return day


def get_training_plan(person, tool_input):
    today = person["today"]
    rows = db.get_plan(person["email"], today.isoformat(), (today + timedelta(days=13)).isoformat())
    return {
        "today": today.isoformat(),
        "plan": [_with_weekday(row, ["date", "planned_workout", "adjusted_workout"]) for row in rows],
    }


def get_training_history(person, tool_input):
    today = person["today"]
    first = today - timedelta(days=_days(tool_input, 14))
    rows = db.get_plan(person["email"], first.isoformat(), (today - timedelta(days=1)).isoformat())
    return {"history": [_with_weekday(row, ["date", "planned_workout", "adjusted_workout", "reason"]) for row in rows]}


def update_training_plan(person, tool_input):
    today = person["today"]
    last_allowed = today + timedelta(days=28)
    planned, skipped = {}, []  # a dict, so each date is saved only once
    for item in tool_input.get("workouts", []):
        day = date.fromisoformat(item["date"])
        if today <= day <= last_allowed:
            planned[day.isoformat()] = {"email": person["email"], "date": day.isoformat(), "planned_workout": item["workout"]}
        else:
            skipped.append(item["date"])
    db.save_planned_workouts(list(planned.values()))
    return {"saved": len(planned), "skipped_dates_outside_today_to_28_days": skipped}


def save_daily_log(person, tool_input):
    today = person["today"].isoformat()
    db.save_decision(person["email"], today, tool_input["workout"], tool_input["reason"])
    db.set_weather_summary(person["email"], today, tool_input.get("weather_summary"))
    return {"saved": True, "date": today}


TOOL_FUNCTIONS = {
    "get_whoop_recovery": get_whoop_recovery,
    "get_whoop_sleep": get_whoop_sleep,
    "get_whoop_strain": get_whoop_strain,
    "get_whoop_workouts": get_whoop_workouts,
    "get_weather": get_weather,
    "get_training_plan": get_training_plan,
    "get_training_history": get_training_history,
    "update_training_plan": update_training_plan,
    "save_daily_log": save_daily_log,
}


def _run_tool(name, tool_input, person):
    """Run one tool. Returns (result, failed). Errors go back to Claude instead of crashing the page."""
    try:
        return TOOL_FUNCTIONS[name](person, tool_input), False
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"}, True


# ---------- The agent loop ----------

def _cost(usage):
    """Dollars for one Claude reply, from the token counts it reports."""
    price = config.PRICES[config.MODEL]
    per_input_token = price["input"] / 1_000_000
    per_output_token = price["output"] / 1_000_000
    return (
        usage.input_tokens * per_input_token
        + (usage.cache_creation_input_tokens or 0) * per_input_token * config.CACHE_WRITE_MULTIPLIER
        + (usage.cache_read_input_tokens or 0) * per_input_token * config.CACHE_READ_MULTIPLIER
        + usage.output_tokens * per_output_token
    )


def _first_message(profile, now):
    """The message that starts each run: today's date and what the person told us about themselves."""
    return f"""Today is {now:%A, %B} {now.day}, {now.year}, and it's {now:%I:%M %p} where this person lives ({profile['timezone']}).

About them, in their own words:
- Goal: {profile.get('goal') or 'not given'}
- Goal date: {profile.get('goal_date') or 'none'}
- What they do now: {profile.get('experience') or 'not given'}
- Times they're willing to run: {profile.get('run_times') or 'any time'}
- Location: {profile.get('place_name') or 'unknown'}

Plan today's training."""


def plan_my_day(email, show_step):
    """Run the coach for one person.

    show_step(step) is called after every step so the page can show it live.
    Returns a dict with the final decision, every step, and the cost in dollars.
    """
    steps = []

    def record(step):
        steps.append(step)
        show_step(step)

    # Step 0: download the newest WHOOP data, so the tools read fresh numbers from the database.
    try:
        saved = whoop.sync(email)
        record({"kind": "sync", "text": f"Downloaded the latest WHOOP data ({saved} days)."})
    except requests.RequestException:
        record({"kind": "sync", "text": "Couldn't reach WHOOP, so the coach is using the data saved earlier."})

    profile = db.get_profile(email)
    zone = ZoneInfo(profile["timezone"] or "America/New_York")
    now = datetime.now(zone)
    person = {"email": email, "profile": profile, "today": now.date(), "zone": zone}

    client = anthropic.Anthropic(api_key=st.secrets["anthropic_api_key"])
    messages = [{"role": "user", "content": _first_message(profile, now)}]
    cost = 0.0

    for _ in range(config.MAX_STEPS):
        response = client.messages.create(
            model=config.MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            # Claude thinks before answering; "summarized" lets us show a short version of that thinking.
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": config.EFFORT},
            # Reuse the instructions and earlier steps from the cache, so each round costs less.
            cache_control={"type": "ephemeral"},
        )
        cost += _cost(response.usage)

        for block in response.content:
            if block.type == "thinking" and block.thinking:
                record({"kind": "thinking", "text": block.thinking})
            elif block.type == "text" and block.text.strip() and response.stop_reason == "tool_use":
                record({"kind": "note", "text": block.text})

        if response.stop_reason == "refusal":
            decision = "Claude declined to plan this request. Try again, or change your goal text."
            record({"kind": "stopped", "text": decision})
            break

        if response.stop_reason != "tool_use":
            # No more tools wanted: this is Claude's final answer.
            decision = "".join(block.text for block in response.content if block.type == "text").strip()
            decision = decision or "The coach finished without writing a message."
            record({"kind": "decision", "text": decision})
            break

        # Claude asked for tools. Run every one, then send all the results back together.
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type == "tool_use":
                record({"kind": "tool_call", "tool": block.name, "input": block.input})
                output, failed = _run_tool(block.name, block.input, person)
                record({"kind": "tool_result", "tool": block.name, "input": block.input, "output": output, "failed": failed})
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output),
                    "is_error": failed,
                })
        messages.append({"role": "user", "content": results})
    else:
        # The loop used up all MAX_STEPS without Claude finishing.
        decision = f"The coach stopped after {config.MAX_STEPS} rounds without finishing. Try again."
        record({"kind": "stopped", "text": decision})

    db.save_agent_run(email, config.MODEL, steps, decision, cost)
    return {"decision": decision, "steps": steps, "cost": cost}
