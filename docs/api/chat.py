# api/chat.py: the AI coach for the demo website. Vercel runs it as a "serverless function"
# at /api/chat (the demo page itself is plain HTML, so it can't hold a secret key).
#
# The demo page sends a message (or "plan my day") plus Clive's SAMPLE data as it looks right
# now. This runs the same kind of tool-use loop as agent.py, but every tool reads that sample
# data, and nothing is ever saved: changes (a moved workout, a suggested time, a note) go back
# to the page, which shows them until you reload.
#
# The Claude key is ANTHROPIC_API_KEY in Vercel's settings, never in the page. Use a separate
# key with a monthly spending limit. The limits below slow down anyone who tries to overuse it.

import json
import os
import re
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler

import anthropic

MODEL = "claude-haiku-5-5"
EFFORT = "medium"
MAX_STEPS = 6                  # rounds of tool use per message
MAX_MESSAGE_CHARS = 500        # longest message a visitor can send
MAX_BODY_BYTES = 60_000        # the page's sample data is about 15 KB
PER_VISITOR = (12, 5 * 60)     # at most 12 messages per 5 minutes from one browser
PER_NETWORK = (200, 5 * 60)    # and 200 from one internet address (a whole class on campus Wi-Fi shares one)
PER_SERVER_HOUR = 1000         # and 1,000 an hour in all (the key's spending limit is the real cap)

# ---------- The instructions (a shorter version of agent.py's) ----------

RULES = """You are the coach inside Training Coach, a website that coaches one person from their WHOOP data, the weather, their Google Calendar, and their training plan. This is the public DEMO, used in a class presentation: the person is Clive Curry, a Wake Forest freshman, and everything you see is sample data. Anything you change lasts only for this visitor's visit.

What makes you worth using is that you know this person. Generic advice ("stay hydrated", "listen to your body", "sleep is important") is useless, so never give it on its own. Tie every recommendation to something specific: his numbers compared with HIS normal (get_personal_patterns), a pattern in his data, a named event on his calendar ("your CHM 111 midterm at 10"), his goal, or something he told you (the notes under "What you remember"). When you quote a number, say what it's compared with.

How to decide (from sports and sleep research):
- Readiness: WHOOP recovery 67-100% green, 34-66% yellow, 0-33% red. Judge HRV and resting heart rate by the 7-day average against his normal range, not one morning. Below his HRV range (or resting HR above its range) means keep the week easy.
- Red on 2 of the last 3 days, or recovery falling for days: rest or very easy movement. Avoid two hard days in a row; club tennis counts as training.
- About 80% of training easy. A newer runner gets at most one harder session a week, only on a green day after decent sleep.
- Build slowly: about 10% more running a week, never more than about 30% over two weeks. At least 2 rest or cross-training days a week. Longest run on his freest day.
- Sleep: use get_sleep_plan's exact bedtime and wake-up time. Steady wake-up times matter. No caffeine within about 6 hours of bedtime. No hard workout ending within an hour of bedtime.
- Exams: keep training easy and short around exams, never right before one, and protect sleep the night before.
- Weather: avoid storms, heavy rain, ice, and feels-like temperatures above about 90°F or below about 20°F.
- Safety: training suggestions, not medical advice. For pain, illness signs, or several red days, recommend rest and seeing a doctor or athletic trainer if it continues.

Memory: if he tells you something that should shape future coaching (an injury, a preference, a weekly commitment), save it with remember_about_them as one short sentence without a date.

This is a demo for classmates. Stay his training, sleep, and recovery coach; if someone asks for anything else (homework, code, other topics), say in one line that you're Clive's training coach and suggest a coaching question they could try. If asked, say plainly that this is sample data. Dates are YYYY-MM-DD and the date in the latest message is today. Tools use a 24-hour clock, but write times like "9:20 PM"."""

PLAN_STEPS = """This run: plan today. Call get_whoop_recovery, get_whoop_sleep, get_personal_patterns, get_sleep_plan, get_weather, get_training_plan, and get_calendar_events together first. Decide today's workout (keep it, make it easier, swap days with update_training_plan, or rest). If it's a workout, pick a free time today with create_calendar_event (only a suggestion; it shows dashed on the calendar until he approves). Then call save_daily_log once. Reply: first line = today's workout in a few words; then 2 to 3 short sentences on why, using his numbers against his normal and his calendar; end with tonight's bedtime if sleep matters. Under 90 words, plain text, no bullets."""

CHAT_STEPS = """This run: a visitor is messaging the coach. Answer what they asked directly, like a good coach texting back. Look things up with tools whenever the answer depends on the data, plan, calendar, sleep, or weather; never guess numbers, and call only the tools you need (several at once when you can). To change future days use update_training_plan; to change today's workout also use save_daily_log; say what you changed. For a goal, save it with set_goal and write the plan with update_training_plan (the plan only goes through the end of next week in this demo). Under 120 words. Short "- " bullet lists are fine; no headings or tables."""

# ---------- The tools ----------

def _days(description="How many days back to look, 1 to 14. Default 7."):
    return {"type": "object", "properties": {"days": {"type": "integer", "description": description}}}


NO_INPUT = {"type": "object", "properties": {}}
TOOLS = [
    {"name": "get_whoop_recovery", "description": "Daily WHOOP recovery (0-100%), HRV (ms), and resting heart rate, newest first, plus 30-day averages under 'normal_30_day'.", "input_schema": _days()},
    {"name": "get_whoop_sleep", "description": "Recent nights, newest first: hours asleep vs. needed, deep/REM/light/awake hours, bedtime, and wake time.", "input_schema": _days()},
    {"name": "get_whoop_strain", "description": "Daily WHOOP strain (0-21, how hard the whole day was), newest first.", "input_schema": _days()},
    {"name": "get_whoop_workouts", "description": "Recorded workouts (sport, miles, minutes) for the last 14 days, and running miles this week and last week.", "input_schema": NO_INPUT},
    {"name": "get_personal_patterns", "description": "What's normal for THIS person: 7-day HRV and resting heart rate vs. his own normal range, usual sleep, training load, and patterns found in his last 90 days. Use it to make advice personal.", "input_schema": NO_INPUT},
    {"name": "get_sleep_plan", "description": "Recommended bedtime, wake-up time, and caffeine cutoff for tonight and the next 6 nights, from his sleep need, sleep efficiency, usual wake time, and first event each morning.", "input_schema": NO_INPUT},
    {"name": "get_weather", "description": "Today's and tomorrow's forecast in Winston-Salem, with an hour-by-hour list for today.", "input_schema": NO_INPUT},
    {"name": "get_calendar_events", "description": "His Google Calendar from today: each day's events (24-hour times) and free windows of 30+ minutes between 06:00 and 21:00, marked if they're in the times he likes to run (6-9am, 3-6pm).", "input_schema": _days("How many days to look at, starting today, 1 to 7. Default 2.")},
    {"name": "get_training_plan", "description": "The plan from today through the end of next week: each day's workout.", "input_schema": NO_INPUT},
    {"name": "get_training_history", "description": "The last 10 days of the plan and whether each was done.", "input_schema": NO_INPUT},
    {"name": "update_training_plan", "description": "Change planned workouts for today through the end of next week. A date that already has a workout gets replaced.",
     "input_schema": {"type": "object", "properties": {"workouts": {"type": "array", "items": {"type": "object", "properties": {
         "date": {"type": "string", "description": "YYYY-MM-DD"},
         "workout": {"type": "string", "description": "Like 'Easy run, 2.5 miles' or 'Rest day'"}}, "required": ["date", "workout"]}}},
         "required": ["workouts"]}},
    {"name": "save_daily_log", "description": "Save today's final workout decision (shown in the 'Today's workout' card).",
     "input_schema": {"type": "object", "properties": {
         "workout": {"type": "string", "description": "Like 'Easy run, 2 miles at 7:00 AM'"},
         "reason": {"type": "string", "description": "One or two sentences with the key numbers"}}, "required": ["workout", "reason"]}},
    {"name": "create_calendar_event", "description": "Suggest a time for a workout on his calendar, today or up to 6 days ahead. It shows as a dashed 'waiting for your OK' block. Use a free window from get_calendar_events. At most once per message, never for a rest day.",
     "input_schema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "YYYY-MM-DD"},
         "start_time": {"type": "string", "description": "HH:MM, 24-hour"},
         "minutes": {"type": "integer", "description": "Including warm-up and cool-down"},
         "title": {"type": "string", "description": "Short, like 'Easy run, 2 miles'"}}, "required": ["date", "start_time", "minutes", "title"]}},
    {"name": "remember_about_them", "description": "Save one short note that should shape future coaching (an injury, a preference, a weekly commitment). No date; it's added for you.",
     "input_schema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}},
]
GOAL_TOOL = {"name": "set_goal", "description": "Save a new training goal (and goal date) when he clearly asks to set or change it.",
             "input_schema": {"type": "object", "properties": {
                 "goal": {"type": "string"}, "goal_date": {"type": "string", "description": "YYYY-MM-DD, or leave out"}}, "required": ["goal"]}}

ICONS = {"get_whoop_recovery": "monitor_heart", "get_whoop_sleep": "bedtime", "get_whoop_strain": "local_fire_department",
         "get_whoop_workouts": "directions_run", "get_personal_patterns": "person_search", "get_sleep_plan": "bedtime",
         "get_weather": "partly_cloudy_day", "get_calendar_events": "event", "get_training_plan": "calendar_month",
         "get_training_history": "history", "update_training_plan": "edit_calendar", "save_daily_log": "save",
         "create_calendar_event": "calendar_add_on", "remember_about_them": "bookmark_add", "set_goal": "flag"}


# ---------- Small helpers ----------

def _minutes(hhmm):
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _hhmm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _clock(minutes):
    """540 -> '9:00 AM'."""
    hours, mins = divmod(minutes % (24 * 60), 60)
    return f"{(hours + 11) % 12 + 1}:{mins:02d} {'AM' if hours < 12 else 'PM'}"


def _short(text, limit):
    return " ".join(str(text).split())[:limit]


def _count(tool_input, default, top):
    try:
        return max(1, min(int(tool_input.get("days", default)), top))
    except (TypeError, ValueError):
        return default


def _weekday(iso):
    return time.strftime("%a", time.strptime(iso, "%Y-%m-%d"))


# ---------- What each tool does (on the sample data the page sent) ----------

class Demo:
    """One request: the sample data, plus the changes the coach makes (sent back to the page)."""

    def __init__(self, data):
        self.data = data
        self.today = data["today"]
        self.now = _minutes(data["now"])
        self.changes = {"plan": {}, "suggested": None, "today": None, "notes": [], "goal": None}

    def calendar_day(self, iso):
        return next((d for d in self.data["calendar"] if d["date"] == iso), None)

    def future_dates(self, count):
        dates = [d["date"] for d in self.data["calendar"] if d["date"] >= self.today]
        return dates[:count]

    def workout(self, iso):
        if iso in self.changes["plan"]:
            return self.changes["plan"][iso]
        day = self.calendar_day(iso)
        return day["workout"] if day else None

    # --- WHOOP ---
    def get_whoop_recovery(self, tool_input):
        days = self.data["days"][::-1][: _count(tool_input, 7, 14)]
        zone = lambda score: "green" if score >= 67 else "yellow" if score >= 34 else "red"
        return {"days": [{"date": d["date"], "recovery_score": d["recovery"], "zone": zone(d["recovery"]), "hrv_ms": d["hrv"],
                          "resting_hr": d["rhr"]} for d in days],
                "normal_30_day": self.data["normal_30_day"]}

    def get_whoop_sleep(self, tool_input):
        days = self.data["days"][::-1][: _count(tool_input, 7, 14)]
        return {"nights": [{"woke_up_on": f"{_weekday(d['date'])} {d['date']}", "sleep_hours": d["sleep"], "sleep_need_hours": d["need"], "deep": d["deep"],
                            "rem": d["rem"], "light": d["light"], "awake": d["awake"], "bedtime": d["bedtime"],
                            "wake_time": d["wake_time"]} for d in days]}

    def get_whoop_strain(self, tool_input):
        days = self.data["days"][::-1][: _count(tool_input, 7, 14)]
        return {"days": [{"date": d["date"], "day_strain": d["strain"]} for d in days]}

    def get_whoop_workouts(self, tool_input):
        return self.data["workouts"]

    def get_personal_patterns(self, tool_input):
        return self.data["patterns"]

    def get_sleep_plan(self, tool_input):
        return {"nights": self.data["nights"]}

    def get_weather(self, tool_input):
        return self.data["weather"]

    # --- Calendar ---
    def busy(self, iso):
        """Timed events that block time that day (the coach's own workouts don't)."""
        day = self.calendar_day(iso) or {"events": []}
        return sorted((_minutes(e["start"]), _minutes(e["end"]), e["title"]) for e in day["events"] if e["calendar"] != "coach")

    def free_windows(self, iso):
        start, end = 6 * 60, 21 * 60
        if iso == self.today:
            start = max(start, -(-self.now // 15) * 15)  # can't plan in the past
        windows, cursor = [], start
        for begin, finish, _ in self.busy(iso) + [(end, end, "")]:
            if begin - cursor >= 30:
                liked = any(cursor < b * 60 and min(begin, end) > a * 60 for a, b in [(6, 9), (15, 18)])
                windows.append({"start": _hhmm(cursor), "end": _hhmm(min(begin, end)), "in_his_run_times": liked})
            cursor = max(cursor, finish)
        return windows

    def get_calendar_events(self, tool_input):
        out = []
        for iso in self.future_dates(_count(tool_input, 2, 7)):
            day = self.calendar_day(iso)
            out.append({"date": iso, "weekday": _weekday(iso), "all_day": day.get("all_day", []),
                        "events": [{"title": e["title"], "start": e["start"], "end": e["end"],
                                    "calendar": {"classes": "WFU Classes", "personal": "Tennis & personal"}.get(e["calendar"], "Training Coach"),
                                    **({"note": "exam"} if e.get("tag") == "exam" else {}),
                                    **({"note": "suggested, waiting for his OK"} if e.get("tag") == "suggested" else {})}
                                   for e in day["events"]],
                        "free_windows": self.free_windows(iso)})
        return {"days": out}

    def get_training_plan(self, tool_input):
        return {"today": self.today, "plan": [{"date": iso, "weekday": _weekday(iso), "workout": self.workout(iso)}
                                              for iso in self.future_dates(14)]}

    def get_training_history(self, tool_input):
        past = [d for d in self.data["calendar"] if d["date"] < self.today][-10:]
        return {"history": [{"date": d["date"], "weekday": _weekday(d["date"]), "workout": d["workout"], "done": d.get("done")} for d in past]}

    # --- Changes (sent back to the page) ---
    def update_training_plan(self, tool_input):
        allowed, saved, skipped = set(self.future_dates(14)), 0, []
        for item in (tool_input.get("workouts") or [])[:14]:
            iso, text = str(item.get("date", "")), _short(item.get("workout", ""), 80)
            if iso in allowed and text:
                self.changes["plan"][iso] = text
                saved += 1
            else:
                skipped.append(iso)
        return {"saved": saved, "skipped_dates_outside_the_plan": skipped,
                "note": "On changed days, the old workout blocks are taken off his calendar automatically."}

    def save_daily_log(self, tool_input):
        self.changes["today"] = {"workout": _short(tool_input.get("workout", ""), 80), "reason": _short(tool_input.get("reason", ""), 400)}
        return {"saved": True, "date": self.today}

    def create_calendar_event(self, tool_input):
        iso = str(tool_input.get("date", ""))
        if iso not in self.future_dates(7):
            raise ValueError("Pick a date from today to 6 days from now.")
        start = _minutes(str(tool_input["start_time"]))
        minutes = max(10, min(int(tool_input.get("minutes") or 30), 180))
        if iso == self.today and start < self.now:
            raise ValueError("That time has already passed. Pick a later time.")
        if not 5 * 60 <= start <= 22 * 60 - minutes:
            raise ValueError("Pick a time between 5 AM and 10 PM.")
        clashes = [title for begin, finish, title in self.busy(iso) if begin < start + minutes and finish > start]
        if clashes:
            raise ValueError(f"That overlaps with {', '.join(clashes)}. Pick a free window from get_calendar_events.")
        self.changes["suggested"] = {"date": iso, "start": _hhmm(start), "end": _hhmm(start + minutes),
                                     "title": _short(tool_input.get("title", "Workout"), 40)}
        return {"status": "suggested", "note": "It shows dashed on his calendar until he approves it."}

    def remember_about_them(self, tool_input):
        note = re.sub(r"^(?:[A-Z][a-z]{2} \d{1,2}|\d{4}-\d{2}-\d{2}):\s*", "", _short(tool_input.get("note", ""), 160))
        if note:
            self.changes["notes"].append(note)
        return {"saved": bool(note)}

    def set_goal(self, tool_input):
        self.changes["goal"] = {"goal": _short(tool_input.get("goal", ""), 100), "goal_date": _short(tool_input.get("goal_date") or "", 10) or None}
        return {"saved": True, **self.changes["goal"]}

    def run(self, name, tool_input):
        """Run one tool. Returns (result, failed)."""
        try:
            return getattr(self, name)(tool_input or {}), False
        except Exception as error:  # the coach sees the error and tries something else
            return {"error": f"{type(error).__name__}: {error}"}, True


def summary(name, tool_input, output):
    """One plain-English line about a tool's result, for the 'Looked at...' list on the page."""
    try:
        if name == "get_whoop_recovery":
            day = output["days"][0]
            return f"latest recovery {day['recovery_score']}%, HRV {day['hrv_ms']} ms (30-day normal {output['normal_30_day']['hrv_ms']})"
        if name == "get_whoop_sleep":
            night = output["nights"][0]
            return f"last night {night['sleep_hours']} h asleep, {night['sleep_need_hours']} h needed"
        if name == "get_whoop_strain":
            return f"latest day strain {output['days'][0]['day_strain']}"
        if name == "get_whoop_workouts":
            return f"{output['running_miles_this_week_so_far']} running miles this week ({output['running_miles_last_week']} last week)"
        if name == "get_personal_patterns":
            return f"HRV {output['hrv']['status']}, {len(output['patterns_in_his_data'])} patterns in his data"
        if name == "get_sleep_plan":
            night = output["nights"][0]
            return f"tonight: in bed by {night['bedtime']}, up at {night['wake_up']}"
        if name == "get_weather":
            return output["today"]["summary"]
        if name == "get_calendar_events":
            first = output["days"][0]
            return f"{len(output['days'])} days; today {len(first['events'])} events, {len(first['free_windows'])} free windows"
        if name == "get_training_plan":
            return f"{len(output['plan'])} days planned"
        if name == "get_training_history":
            return f"{len(output['history'])} past days"
        if name == "update_training_plan":
            return f"changed {output['saved']} days of the plan"
        if name == "save_daily_log":
            return f"saved today's decision: {tool_input.get('workout')}"
        if name == "create_calendar_event":
            return f"suggested {tool_input['date']} at {tool_input['start_time']} for {tool_input.get('minutes')} min, waiting for OK"
        if name == "remember_about_them":
            return f"remembered: {tool_input.get('note')}"
        if name == "set_goal":
            return f"saved the goal: {tool_input.get('goal')}"
    except (KeyError, IndexError, TypeError):
        pass
    return "done"


# ---------- The agent loop ----------

def about_him(data):
    profile = data["profile"]
    notes = "\n".join(f"- {note}" for note in data["notes"]) or "- Nothing yet."
    return f"""Today is {data['today_text']}, and it's {_clock(_minutes(data['now']))} where he lives (America/New_York).

About him, in his own words:
- Name: Clive Curry
- Goal: {profile['goal']}
- Goal date: {profile['goal_date'] or 'none'}
- What he does now: {profile['experience']}
- Times he's willing to run: {profile['run_times']}
- Location: Winston-Salem, North Carolina

What you remember about him:
{notes}"""


def coach(data, mode, message, earlier):
    """Run the coach once. Returns the answer, the steps to show, and the changes for the page."""
    demo = Demo(data)
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=40, max_retries=2)
    tools = TOOLS + ([GOAL_TOOL] if mode == "chat" else [])
    messages = []
    for asked, answered in earlier:
        messages += [{"role": "user", "content": asked}, {"role": "assistant", "content": answered}]
    request = "Plan today's training." if mode == "plan" else f"Their message:\n{message}"
    messages.append({"role": "user", "content": about_him(data) + "\n\n" + request})

    steps, answer = [], None
    for _ in range(MAX_STEPS):
        response = client.messages.create(
            model=MODEL, max_tokens=4000, system=RULES + "\n\n" + (PLAN_STEPS if mode == "plan" else CHAT_STEPS),
            tools=tools, messages=messages, thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": EFFORT}, cache_control={"type": "ephemeral"},
        )
        for block in response.content:
            if block.type == "thinking" and block.thinking and not any(s[0] == "psychology" for s in steps):
                steps.append(["psychology", None, _short(block.thinking, 280), "thought"])
        if response.stop_reason == "refusal":
            answer = "I can only help with Clive's training, sleep, and recovery. Try asking about his week."
            break
        if response.stop_reason != "tool_use":
            answer = "".join(b.text for b in response.content if b.type == "text").strip() or "Done."
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type == "tool_use":
                output, failed = demo.run(block.name, block.input)
                steps.append([ICONS.get(block.name, "build"), block.name,
                              f"failed: {output['error']}" if failed else summary(block.name, block.input, output), ""])
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(output), "is_error": failed})
        messages.append({"role": "user", "content": results})
    if answer is None:
        answer = "I ran out of steps on that one. Try asking a simpler question."
    steps.append(["check_circle", None, "Answered." if mode == "chat" else "Decided today's plan.", "done"])
    return {"answer": answer, "steps": steps, "changes": demo.changes}


# ---------- Checking requests, and the speed limits ----------

visits = defaultdict(deque)   # recent message times, per browser and per internet address
hour = deque()


def _recent(key, window, now):
    times = visits[key]
    while times and now - times[0] > window:
        times.popleft()
    return times


def allowed(browser, network):
    """True if this browser, this internet address, and the whole site are all under their limits."""
    now = time.time()
    mine, ours = _recent(("browser", browser), PER_VISITOR[1], now), _recent(("network", network), PER_NETWORK[1], now)
    while hour and now - hour[0] > 3600:
        hour.popleft()
    if len(mine) >= PER_VISITOR[0] or len(ours) >= PER_NETWORK[0] or len(hour) >= PER_SERVER_HOUR:
        return False
    for times in (mine, ours, hour):
        times.append(now)
    return True


def clean_request(body):
    """Check what the page sent. Returns (mode, message, earlier turns, data) or raises ValueError."""
    mode = body.get("mode")
    if mode not in ("chat", "plan"):
        raise ValueError("Unknown request.")
    message = _short(body.get("message", ""), MAX_MESSAGE_CHARS) if mode == "chat" else ""
    if mode == "chat" and not message:
        raise ValueError("Type a message first.")
    earlier = []
    for turn in (body.get("earlier") or [])[-6:]:
        asked, answered = _short(turn.get("asked", ""), MAX_MESSAGE_CHARS), _short(turn.get("answered", ""), 1500)
        if asked and answered:
            earlier.append((asked, answered))
    data = body.get("data")
    if not isinstance(data, dict) or not {"today", "now", "days", "calendar", "profile", "notes"} <= set(data):
        raise ValueError("The page didn't send its sample data.")
    data["notes"] = [_short(n, 160) for n in data["notes"][:12]]
    return mode, message, earlier, data


def respond(body, network):
    """(status code, reply) for one request from the page. network is the visitor's internet address."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return 503, {"error": "The demo coach isn't switched on yet."}
    try:
        mode, message, earlier, data = clean_request(body)
    except (ValueError, AttributeError, TypeError) as error:
        return 400, {"error": str(error) or "That request didn't look right."}
    browser = str(body.get("visitor") or "")  # a random ID the page makes for each browser
    if not re.fullmatch(r"[A-Za-z0-9-]{8,40}", browser):
        browser = network
    if not allowed(browser, network):
        return 429, {"error": "The coach needs a short breather. Try again in a few minutes."}
    try:
        return 200, coach(data, mode, message, earlier)
    except anthropic.RateLimitError:
        return 503, {"error": "Lots of people are trying the coach right now. Try again in a minute."}
    except anthropic.APIError:
        return 503, {"error": "The demo coach is taking a break right now. The saved conversation above still shows what it can do."}
    except (KeyError, TypeError, ValueError):
        return 400, {"error": "The page's sample data didn't look right. Try reloading."}


class handler(BaseHTTPRequestHandler):
    """Vercel calls this for every request to /api/chat."""

    def do_GET(self):  # the page asks this first, to know whether to show the message box
        self._send(200, {"ready": bool(os.environ.get("ANTHROPIC_API_KEY")), "model": MODEL})

    def do_POST(self):
        size = int(self.headers.get("content-length") or 0)
        if size > MAX_BODY_BYTES:
            return self._send(413, {"error": "That request was too big."})
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            return self._send(400, {"error": "That request didn't look right."})
        network = (self.headers.get("x-forwarded-for") or self.client_address[0]).split(",")[0].strip()
        self._send(*respond(body if isinstance(body, dict) else {}, network))

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
