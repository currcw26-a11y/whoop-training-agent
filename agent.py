# agent.py: the AI coach.
#
# How it works (the approach from Anthropic's "Building Effective Agents"):
#   1. We send Claude the person's info, instructions, and a list of TOOLS it may use.
#   2. Claude replies by asking to use one or more tools ("get my sleep data").
#   3. Our code runs those tools and sends Claude the results.
#   4. Repeat until Claude stops asking for tools and writes its answer,
#      or until MAX_STEPS rounds, whichever comes first.
# Every step is recorded so the website can show exactly what the agent did.
#
# The same coach runs two ways:
#   plan_my_day() - the "Plan my day" button: decides today's workout
#   chat()        - one message on the "Ask the coach" page
#
# What makes it personal instead of generic (details and sources in RESEARCH.md):
#   - it compares the person with their OWN normal (personal.py), not with everyone else
#   - it gets their sleep plan, calendar, goal, and plan, and must use them
#   - it remembers what they tell it (remember_about_them), across days

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic
import pandas as pd
import requests
import streamlit as st

import config
import db
import google_calendar
import personal
import sleep_plan
import weather
import whoop

# ---------- The instructions Claude gets every time ----------

COACH_RULES = """You are the coach inside Training Coach, a website that coaches one person at a time from their WHOOP data, the weather, their Google Calendar, and their training plan. Most people using it are college students.

What makes you worth using is that you know this person. Generic advice ("stay hydrated", "listen to your body", "sleep is important") is useless to them, so never give it on its own. Tie every recommendation to something specific about them:
- their numbers compared with THEIR normal (get_personal_patterns has their normal ranges),
- a pattern in their own data (get_personal_patterns, under patterns_in_their_data),
- a named event on their calendar ("your CHM 111 midterm at 10"),
- their goal and goal date,
- something they told you before (the notes under "What you remember about them").
When you quote a number, say what it's compared with, like "HRV 52 ms this week, below your normal 58-64".

How to decide (from sports and sleep research):
- Readiness. WHOOP recovery 67-100% is green, 34-66% yellow, 0-33% red. Judge HRV and resting heart rate by the 7-day average against their normal range, not by one morning; one low morning is often noise. A 7-day HRV average below their range, or resting heart rate above it, means keep this week easy even if today's recovery looks fine. Within or above their range on a green day, a harder session is OK.
- Red on 2 of the last 3 days, or recovery falling for several days: rest or very easy movement. Never stack hard days on low recovery, and avoid two hard days in a row. Check yesterday's strain; tennis, pickup games, and lifting count as training.
- Mostly easy. About 80% of training time should be easy (a pace where they could talk, heart-rate zones 1-2). Newer runners get at most one harder session a week, and only on a green day after a decent night's sleep.
- Build slowly. Raise weekly running distance about 10% at a time, and never more than about 30% over two weeks (novice runners who jumped that much had more injuries). Use run/walk for newer runners, include at least 2 rest or cross-training days a week, and put the longest run on their freest day, usually a weekend.
- Sleep. Their sleep need is personal (WHOOP's estimate), and a short night means an easier day. get_sleep_plan has their bedtime and wake-up time worked out; use those exact times. A steady wake-up time matters: students with irregular sleep had lower grades in a Harvard study. No caffeine within about 6 hours of bedtime. Don't put a hard workout that ends within an hour of bedtime; easy evening exercise is fine.
- Exams and busy days. Exam stress lowers HRV. If their calendar shows an exam, test, quiz, midterm, final, or presentation today or tomorrow, or the day is packed, keep the workout short and easy (or move it), never right before the exam, and protect their sleep the night before.
- Weather. Avoid thunderstorms, heavy rain, ice, and feels-like temperatures above about 90°F or below about 20°F. Pick a better hour within the times they're willing to run, or suggest an indoor option.
- Safety. You give training suggestions, not medical advice. If the numbers could point to illness or injury (several red days in a row, resting heart rate far above their normal, high skin temperature, low blood oxygen), or they mention pain, recommend rest and suggest seeing a doctor or athletic trainer if it continues. Pain that changes how they move means no running.

Memory: when they tell you something that should shape future coaching (an injury or pain, a preference, a weekly commitment like club tennis, how a workout felt, a race), save it with remember_about_them as one short sentence. Don't save what's already in their data or profile, and don't save the same thing twice.

Dates are YYYY-MM-DD; treat the date in the latest message as today. Tools use a 24-hour clock, but write times for the person like "9:20 PM". If a tool returns an error or no data, work with what you have and mention it briefly."""

PLAN_STEPS = """This run: plan today. Do this:
1. Gather the facts. In your first turn, call all of these together: get_whoop_recovery, get_whoop_sleep, get_whoop_strain, get_whoop_workouts, get_personal_patterns, get_sleep_plan, get_weather, get_training_plan, get_training_history, and get_calendar_events (if you have it).
2. If get_training_plan has nothing planned for the coming week, write a plan for today and the next 13 days with update_training_plan, starting from what they do now and building toward their goal by the rules above. If they gave a goal date, aim to get there safely; if it looks unrealistic, plan safely anyway and say so.
3. Decide today's workout: keep what was planned, make it easier, swap it with another day this week (update both days with update_training_plan), or rest.
4. Pick a time for the next workout (today's, or the day you moved it to). If you have create_calendar_event: choose a free window from get_calendar_events, inside the times they're willing to run if possible, with OK weather that hour, then call create_calendar_event once. It only suggests the time; they approve it on the website. Without calendar tools, pick a time from the weather and their run times.
5. Call save_daily_log once with today's final workout (include the time), the reason, and a one-line weather summary.
6. Reply. First line: today's workout, in a few words. Then 2 to 3 short sentences on why, using their numbers against their normal and anything on their calendar that mattered. If sleep matters tonight (an early class, sleep debt, low recovery), end with the bedtime from get_sleep_plan. Under 90 words in all. Plain text only, no headings or bullet points. If you suggested a calendar time, say it's waiting for their OK."""

CHAT_STEPS = """This run: they're messaging you on the "Ask the coach" page.
- Answer what they asked, directly, like a good coach texting back.
- Look things up with tools whenever the answer depends on their data, plan, calendar, sleep, or weather. Never guess their numbers. Call only the tools you need, several at once when you can.
- To change future days, use update_training_plan; to change today's workout, also use save_daily_log. Say what you changed.
- Planning toward a goal: if something essential is missing (the goal date, or how much they run now), ask for it, at most two short questions at a time. Then save the goal with set_goal, write the plan with update_training_plan (up to 28 days ahead), and sum up the first two weeks in a few short lines.
- If they ask about something other than training, sleep, or recovery, say in one line that you're their training coach and bring it back.
- Keep replies under 120 words unless they ask for more. Short "- " bullet lists are fine; no headings or tables."""


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
        "name": "get_personal_patterns",
        "description": "What's normal for THIS person, from their last 90 days: their 7-day HRV and resting heart rate "
                       "averages against their own normal range (and what that means), their usual bedtime, wake time, "
                       "sleep need, and how much their bedtime varies, their training load (this week vs. the 4 weeks "
                       "before, share of easy training), and patterns found in their data (like how their recovery "
                       "changes after short nights or hard days). Use it to make advice personal instead of generic.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_sleep_plan",
        "description": "Their recommended bedtime and wake-up time for tonight and the next 6 nights (local 24-hour "
                       "clock), worked out from WHOOP's sleep need for them, their sleep efficiency, their usual "
                       "wake-up time, and their first calendar event each morning. Also the time to stop caffeine.",
        "input_schema": {"type": "object", "properties": {}},
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
                       "has a planned workout gets replaced. Today's final decision goes in save_daily_log.",
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
        "description": "Save today's final workout decision. When planning the day, call it exactly once, at the end. "
                       "In a chat, call it only if the person wants today's workout changed.",
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
    {
        "name": "remember_about_them",
        "description": "Save one short note about the person that should shape future coaching: an injury or pain, a "
                       "preference, a weekly commitment, how a workout felt, or a race. You'll see your notes at the "
                       "start of every conversation, and they can read and edit them on their Coaching profile.",
        "input_schema": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "One sentence without a date (the date is added for you), "
                                                        "like 'Left knee gets sore after runs over 3 miles.'"},
            },
            "required": ["note"],
        },
    },
]

# Only in chats: changing the goal is something the person asks for, not something to do on a button press.
CHAT_TOOLS = [
    {
        "name": "set_goal",
        "description": "Save the person's training goal (and goal date, if they have one) to their Coaching profile. "
                       "Use it only when they clearly ask to set or change their goal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "In their words, like 'Run the Turkey Trot 5K without walking'"},
                "goal_date": {"type": "string", "description": "YYYY-MM-DD, or leave out if there's no date"},
            },
            "required": ["goal"],
        },
    },
]

# Only given to Claude when the person has connected Google Calendar.
CALENDAR_TOOLS = [
    {
        "name": "get_calendar_events",
        "description": "The person's Google Calendar, starting today: each day's events (title, start and end on "
                       "their local 24-hour clock, and which calendar) plus 'free_windows', open stretches of 30+ "
                       "minutes between 06:00 and 21:00, each marked with whether it falls in the times they like "
                       "to run. Classes, exams, and work show up here. Use it to pick a workout time that fits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "How many days to look at, starting today, 1 to 7. Default 2."},
            },
        },
    },
    {
        "name": "create_calendar_event",
        "description": "Suggest a time for the next workout (today's, or the day you moved it to) on the person's "
                       "Google Calendar. It is NOT added right away: the website shows them an 'Add to calendar' "
                       "button and they decide. Use a free window from get_calendar_events, inside their run times if "
                       "possible, when the weather that hour is OK, and not right before an exam. Call it at most "
                       "once per run, and never for a rest day.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD, today or up to 6 days ahead"},
                "start_time": {"type": "string", "description": "HH:MM on their local 24-hour clock, like 07:00"},
                "minutes": {"type": "integer", "description": "How long to block off, including warm-up and cool-down"},
                "title": {"type": "string", "description": "Short, like 'Easy run/walk, 2 miles'"},
            },
            "required": ["date", "start_time", "minutes", "title"],
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
    def clock(moment):  # "2026-10-10T03:12:00+00:00" -> "23:12" in their time zone
        return datetime.fromisoformat(moment).astimezone(person["zone"]).strftime("%H:%M") if moment else None

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


def _history(person):
    """The person's last 90 days of WHOOP data, as tables (read once per run)."""
    if "history" not in person:
        person["history"] = personal.as_frames(db.get_daily_metrics(person["email"], days=90),
                                               db.get_workouts(person["email"], days=90))
    return person["history"]


def get_personal_patterns(person, tool_input):
    days, workouts = _history(person)
    return personal.coach_summary(days, workouts, person["zone"], person["today"])


def get_sleep_plan(person, tool_input):
    days, _ = _history(person)
    zone = person["zone"]
    first_night = sleep_plan.tonight(person["now"])
    events, note = [], None
    if person["has_calendar"]:
        start = datetime.combine(first_night + timedelta(days=1), time(0), zone)
        try:
            events = google_calendar.list_events(person["email"], start, start + timedelta(days=7), zone)
        except requests.RequestException:
            note = "Couldn't read their calendar, so wake-up times assume nothing early."
    plan_rows = db.get_plan(person["email"], first_night.isoformat(), (first_night + timedelta(days=7)).isoformat())
    latest_need = days.iloc[-1]["sleep_need_hours"] if not days.empty else None
    nights = sleep_plan.plan_week(first_night, events, plan_rows, personal.sleep_habits(days, zone),
                                  None if pd.isna(latest_need) else latest_need, zone)
    return {
        "nights": [{
            "night_of": f"{n['night']:%Y-%m-%d (%a)}",
            "bedtime": f"{n['bed']:%H:%M}",
            "wake_up": f"{n['wake']:%H:%M}",
            "sleep_need_hours": n["need_hours"],
            "minutes_in_bed": n["in_bed_minutes"],
            "first_thing_next_morning": f"{n['first']['title']} at {n['first']['start']:%H:%M}" if n["first"] else None,
            "no_caffeine_after": f"{n['caffeine_cutoff']:%H:%M}",
        } for n in nights],
        "how_it_works": "Wake-up: an hour before the first event (15 minutes before a workout), otherwise at most 30 "
                        "minutes after their usual time. Bedtime: wake-up minus sleep need divided by sleep efficiency.",
        **({"note": note} if note else {}),
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


def remember_about_them(person, tool_input):
    note = " ".join(str(tool_input["note"]).split())[:200]  # one tidy line
    note = re.sub(r"^(?:[A-Z][a-z]{2} \d{1,2}|\d{4}-\d{2}-\d{2}):\s*", "", note)  # we add the date ourselves
    if not note:
        raise ValueError("The note was empty.")
    notes = db.get_coach_notes(person["email"])
    notes.append(f"{person['today']:%b} {person['today'].day}: {note}")
    notes = notes[-config.MAX_COACH_NOTES:]  # keep the newest ones
    db.save_coach_notes(person["email"], notes)
    return {"saved": True, "notes_kept": len(notes)}


def set_goal(person, tool_input):
    changes = {"goal": " ".join(str(tool_input["goal"]).split())[:200]}
    if tool_input.get("goal_date"):
        goal_day = date.fromisoformat(tool_input["goal_date"])
        if goal_day < person["today"]:
            raise ValueError("That goal date has already passed.")
        changes["goal_date"] = goal_day.isoformat()
    db.update_profile(person["email"], changes)
    return {"saved": True, **changes}


def _preferred_hours(profile):
    """The (start hour, end hour) windows the person picked on their Coaching profile."""
    picked = (profile.get("run_times") or "").split(", ")
    return [config.RUN_TIME_WINDOWS[name] for name in picked if name in config.RUN_TIME_WINDOWS]


def get_calendar_events(person, tool_input):
    zone, today = person["zone"], person["today"]
    days = max(1, min(int(tool_input.get("days", 2)), 7))
    start = datetime.combine(today, time(0), zone)
    events = google_calendar.list_events(person["email"], start, start + timedelta(days=days), zone)
    result = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        listed = []
        for event in events:
            if event["all_day"]:
                on_this_day = event["start"].date() <= day < event["end"].date()  # all-day events end the next day
            else:
                on_this_day = event["start"].date() == day
            if on_this_day:
                listed.append({
                    "title": event["title"],
                    "start": None if event["all_day"] else f"{event['start']:%H:%M}",
                    "end": None if event["all_day"] else f"{event['end']:%H:%M}",
                    "all_day": event["all_day"],
                    "calendar": event["calendar"],
                    "is_a_workout_you_scheduled": event["from_coach"],
                })
        result.append({
            "date": day.isoformat(),
            "weekday": f"{day:%a}",
            "events": listed[:25],
            "free_windows": google_calendar.free_windows(events, day, zone, person["now"], _preferred_hours(person["profile"])),
        })
    return {"time_zone": str(zone), "days": result}


def create_calendar_event(person, tool_input):
    """Check the suggested time, then save it as a proposal. Nothing goes on the calendar until the person approves."""
    zone, today = person["zone"], person["today"]
    day = date.fromisoformat(tool_input["date"])
    if not today <= day <= today + timedelta(days=6):
        raise ValueError("Pick a date from today to 6 days from now.")
    start = datetime.combine(day, time.fromisoformat(tool_input["start_time"]), zone)
    minutes = max(10, min(int(tool_input["minutes"]), 240))
    end = start + timedelta(minutes=minutes)
    if start < person["now"]:
        raise ValueError("That time has already passed. Pick a later time.")
    clashes = [
        event["title"] for event in google_calendar.list_events(person["email"], start, end, zone)
        if event["busy"] and not event["all_day"] and not event["from_coach"]
    ]
    if clashes:
        raise ValueError(f"That time overlaps with: {', '.join(clashes)}. Pick a free window from get_calendar_events.")
    db.save_event_proposal(person["email"], day.isoformat(), start.isoformat(), minutes, tool_input["title"])
    return {"status": "waiting_for_approval",
            "note": "The person sees an 'Add to calendar' button on the website and decides."}


TOOL_FUNCTIONS = {
    "get_whoop_recovery": get_whoop_recovery,
    "get_whoop_sleep": get_whoop_sleep,
    "get_whoop_strain": get_whoop_strain,
    "get_whoop_workouts": get_whoop_workouts,
    "get_personal_patterns": get_personal_patterns,
    "get_sleep_plan": get_sleep_plan,
    "get_weather": get_weather,
    "get_training_plan": get_training_plan,
    "get_training_history": get_training_history,
    "update_training_plan": update_training_plan,
    "save_daily_log": save_daily_log,
    "remember_about_them": remember_about_them,
    "set_goal": set_goal,
    "get_calendar_events": get_calendar_events,
    "create_calendar_event": create_calendar_event,
}


def _run_tool(name, tool_input, person):
    """Run one tool. Returns (result, failed). Errors go back to Claude instead of crashing the page."""
    try:
        return TOOL_FUNCTIONS[name](person, tool_input), False
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"}, True


# ---------- Daily limits (hidden from the website) ----------

LIMIT_MESSAGE = "The coach has done a lot of planning today and needs a breather. It'll be ready again tomorrow."


def can_run(email):
    """True if this person, and the whole site, are under today's limits in config.py."""
    return (db.count_agent_runs(email) < config.RUNS_PER_PERSON_PER_DAY
            and db.count_agent_runs() < config.RUNS_PER_SITE_PER_DAY)


# ---------- The agent loop ----------

def _cost(usage):
    """Dollars for one Claude reply, from the token counts it reports. (Saved for us; never shown on the site.)"""
    price = config.PRICES[config.MODEL]
    per_input_token = price["input"] / 1_000_000
    per_output_token = price["output"] / 1_000_000
    return (
        usage.input_tokens * per_input_token
        + (usage.cache_creation_input_tokens or 0) * per_input_token * config.CACHE_WRITE_MULTIPLIER
        + (usage.cache_read_input_tokens or 0) * per_input_token * config.CACHE_READ_MULTIPLIER
        + usage.output_tokens * per_output_token
    )


def _about_them(person):
    """Who the person is and what the coach remembers, at the top of each run's latest message."""
    profile, now = person["profile"], person["now"]
    notes = db.get_coach_notes(person["email"])
    remembered = "\n".join(f"- {note}" for note in notes) if notes else "- Nothing yet."
    return f"""Today is {now:%A, %B} {now.day}, {now.year}, and it's {now:%I:%M %p} where this person lives ({profile['timezone']}).

About them, in their own words:
- Name: {profile.get('name') or 'not given'}
- Goal: {profile.get('goal') or 'not given'}
- Goal date: {profile.get('goal_date') or 'none'}
- What they do now: {profile.get('experience') or 'not given'}
- Times they're willing to run: {profile.get('run_times') or 'any time'}
- Location: {profile.get('place_name') or 'unknown'}
- Google Calendar: {'connected' if person['has_calendar'] else 'not connected'}

What you remember about them:
{remembered}"""


def _person(email):
    profile = db.get_profile(email)
    zone = ZoneInfo(profile["timezone"] or "America/New_York")
    now = datetime.now(zone)
    return {"email": email, "profile": profile, "today": now.date(), "now": now, "zone": zone,
            # The calendar tools are only offered when the person has connected Google Calendar.
            "has_calendar": db.is_connected(email, "google")}


def _loop(person, system, tools, messages, record):
    """Ask Claude, run the tools it wants, and repeat until it answers. Returns (answer, cost)."""
    client = anthropic.Anthropic(api_key=st.secrets["anthropic_api_key"])
    cost = 0.0
    for _ in range(config.MAX_STEPS):
        response = client.messages.create(
            model=config.MODEL,
            max_tokens=16000,
            system=system,
            tools=tools,
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
            answer = "The coach couldn't help with that one. Try asking another way."
            record({"kind": "stopped", "text": answer})
            return answer, cost

        if response.stop_reason != "tool_use":
            # No more tools wanted: this is Claude's final answer.
            answer = "".join(block.text for block in response.content if block.type == "text").strip()
            answer = answer or "The coach finished without writing a message."
            record({"kind": "decision", "text": answer})
            return answer, cost

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
                    "content": json.dumps(output, default=str),
                    "is_error": failed,
                })
        messages.append({"role": "user", "content": results})

    # The loop used up all MAX_STEPS without Claude finishing.
    answer = f"The coach stopped after {config.MAX_STEPS} rounds without finishing. Try again."
    record({"kind": "stopped", "text": answer})
    return answer, cost


def _recorder(steps, show_step):
    def record(step):
        steps.append(step)
        show_step(step)
    return record


def plan_my_day(email, show_step):
    """Run the coach for one person's day.

    show_step(step) is called after every step so the page can show it live.
    Returns a dict with the final decision, every step, and the cost in dollars.
    """
    steps = []
    record = _recorder(steps, show_step)

    # Step 0: download the newest WHOOP data, so the tools read fresh numbers from the database.
    try:
        saved = whoop.sync(email)
        record({"kind": "sync", "text": f"Downloaded the latest WHOOP data ({saved} days)."})
    except requests.RequestException:
        record({"kind": "sync", "text": "Couldn't reach WHOOP, so the coach is using the data saved earlier."})

    person = _person(email)
    tools = TOOLS + (CALENDAR_TOOLS if person["has_calendar"] else [])
    messages = [{"role": "user", "content": _about_them(person) + "\n\nPlan today's training."}]
    decision, cost = _loop(person, COACH_RULES + "\n\n" + PLAN_STEPS, tools, messages, record)
    db.save_agent_run(email, config.MODEL, steps, decision, cost, kind="plan")
    return {"decision": decision, "steps": steps, "cost": cost}


def chat(email, question, earlier, show_step):
    """Answer one message on the Ask the coach page.

    earlier: the conversation so far, as (question, answer) pairs, oldest first.
    Returns a dict with the answer, every step, and the cost in dollars.
    """
    steps = [{"kind": "question", "text": question}]
    record = _recorder(steps, show_step)

    # Messages don't download from WHOOP every time (that's slow), only if the data is a few hours old.
    synced = (db.get_profile(email) or {}).get("whoop_synced_at")
    if not synced or datetime.now(timezone.utc) - datetime.fromisoformat(synced) > timedelta(hours=3):
        try:
            whoop.sync(email)
            record({"kind": "sync", "text": "Downloaded the latest WHOOP data."})
        except requests.RequestException:
            pass  # use the saved data

    person = _person(email)
    tools = TOOLS + CHAT_TOOLS + (CALENDAR_TOOLS if person["has_calendar"] else [])
    messages = []
    for asked, answered in earlier[-8:]:  # the last few back-and-forths are plenty of context
        messages += [{"role": "user", "content": asked}, {"role": "assistant", "content": answered}]
    messages.append({"role": "user", "content": _about_them(person) + "\n\nTheir message:\n" + question})
    answer, cost = _loop(person, COACH_RULES + "\n\n" + CHAT_STEPS, tools, messages, record)
    db.save_agent_run(email, config.MODEL, steps, answer, cost, kind="chat")
    return {"answer": answer, "steps": steps, "cost": cost}
