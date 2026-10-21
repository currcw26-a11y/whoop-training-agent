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

# ---------- The rules (the same IDs and sentences as facts.py in the real app) ----------

RESEARCH_RULES = {
    "R1": "Check engine light: If 2+ of resting heart rate, breathing rate, skin temperature, and HRV are outside your normal range, today is easy; 3+, or 2+ two days in a row, means rest.",
    "R2": "Readiness trend: HRV and resting heart rate are judged by their 7-day average against your normal range: HRV below and resting HR above means easy days until they're back. A red recovery means easy; red on 2 of the last 3 days means rest.",
    "R3": "Morning check-in: How you say you feel counts: a low check-in plus any body signal that's off means an easy day.",
    "R4": "Short sleep: After 6 hours of sleep or less, afternoon and evening workouts stay easy and morning ones moderate.",
    "L1": "Spike guard: No run longer than 110% of your longest run in the 30 days before it.",
    "L3": "Weekly build: Weekly running grows no more than about 30% over two weeks. 10% a week is a comfortable default, not a proven rule.",
    "B1": "Mostly easy: About 80% of training time easy (heart-rate zones 1-2), at least 2 rest or cross-training days a week, and at most one faster session a week for newer runners, only on a good day. Run/walk is fine.",
    "B2": "Pain check: Pain on one spot of the bone, pain at rest or at night, pain getting worse, or 5+/10 means no running and see an athletic trainer; 3-4/10, or pain still there the morning after a run, means cross-training at the same effort until it's gone.",
    "S1": "Steady sleep: Keep bedtimes and wake-up times steady; students with more regular sleep earned better grades.",
    "S3": "Sleep target: At least 7 hours of sleep, more when WHOOP says you need it.",
    "S4": "Caffeine and late workouts: Last coffee 9 hours before bedtime, energy drinks or pre-workout 13 hours before, and hard workouts over at least 2 hours before bedtime.",
    "A2": "Exams: No hard session within 2 days before an exam and nothing in the 2 hours before it; keep wake-up times steady and protect sleep the whole week before.",
    "W1": "Weather: Move runs away from thunderstorms, heavy rain, ice, and feels-like temperatures above about 90°F or below about 20°F, or go indoors.",
    "P3": "Your patterns: A consistent pattern in your own data can make a day easier, never harder.",
}

# ---------- The instructions (a shorter version of agent.py's) ----------

RULES = """You are the coach inside Training Coach, a website that coaches one person from their WHOOP data, the weather, their Google Calendar, and their training plan. This is the public DEMO, used in a class presentation: the person is Clive Curry, a Wake Forest freshman, and everything you see is sample data. Anything you change lasts only for this visitor's visit.

What makes you worth using is that you know this person. Generic advice ("stay hydrated", "listen to your body", "sleep is important") is useless, so never give it on its own. Tie every recommendation to something specific: his numbers compared with HIS normal, a pattern in his data, a named event on his calendar ("your CHM 111 midterm at 10"), his goal, or something he told you (the notes under "What you remember"). When you quote a number, say what it's compared with.

How you work: code decides, you explain.
- The latest message starts with TODAY'S FACTS, worked out by code from his data: today's limits and why, the check engine light (4 body signals against his normal), his 7-day HRV and resting heart rate, his morning check-in and any pain, the spike guard, tonight's sleep times, exams coming up, and what moves his recovery. Quote these numbers; don't redo the math.
- Never go over today's limits: the max intensity, whether running is allowed, and the longest run allowed. update_training_plan and save_daily_log turn down workouts that break them, and update_training_plan checks every planned run against the spike guard [L1].
- Cite the rules behind each recommendation (a workout, a change to the plan, a bedtime, a caffeine time) with their IDs in square brackets at the end of the sentence, like "Keep it to 3.5 miles [L1]." Use only the IDs listed below.
- Say how sure a pattern is with the facts' own label ("possible pattern", "consistent pattern", "no clear difference yet"). Patterns are "linked to", never "caused by", and they may make a day easier, never harder [P3].
- If the facts and your tools don't have something, say you don't have that data instead of guessing.
- An automatic checker reads your reply before anyone sees it. It sends it back if you name a medical condition, leave out rule IDs, go over a limit, give a bedtime or caffeine time that isn't in the sleep plan, or leave out the doctor line when it's needed.

The rules (from sports and sleep research; the IDs are what you cite):
""" + "\n".join(f"- {rule} {text}" for rule, text in RESEARCH_RULES.items()) + """
Also: WHOOP recovery 67-100% is green, 34-66% yellow, 0-33% red. Club tennis counts as training, so avoid two hard days in a row. Put the longest run on his freest day.

Safety: training suggestions, not medical advice. Never name a condition (like shin splints, a stress fracture, the flu, or COVID), even as a guess; describe the warning sign and suggest an athletic trainer or doctor. When the check engine light says rest or the pain light is red, always add that he should check in with a doctor or athletic trainer if it continues.

Memory: if he tells you something that should shape future coaching (an injury, a preference, a weekly commitment), save it with remember_about_them as one short sentence without a date.

This is a demo for classmates. Stay his training, sleep, and recovery coach; if someone asks for anything else (homework, code, other topics), say in one line that you're Clive's training coach and suggest a coaching question they could try. If asked, say plainly that this is sample data. Dates are YYYY-MM-DD and the date in the latest message is today. Tools use a 24-hour clock, but write times like "9:20 PM"."""

PLAN_STEPS = """This run: plan today. Call get_whoop_recovery, get_whoop_sleep, get_personal_patterns, get_sleep_plan, get_weather, get_training_plan, and get_calendar_events together first. Decide today's workout (keep it, make it easier, swap days with update_training_plan, or rest). If it's a workout, pick a free time today with create_calendar_event (only a suggestion; it shows dashed on the calendar until he approves). Decide within today's limits; if running isn't allowed, swap the run for cross-training at the same effort. Then call save_daily_log once. Reply: first line = today's workout in a few words (no rule IDs on that line); then 2 to 3 short sentences on why, using his numbers against his normal and his calendar, each ending with the IDs of the rules it follows, like [R1] or [L1, B1]; end with tonight's bedtime if sleep matters [S3]. Under 100 words, plain text, no bullets."""

CHAT_STEPS = """This run: a visitor is messaging the coach. Answer what they asked directly, like a good coach texting back. Look things up with tools whenever the answer depends on the data, plan, calendar, sleep, or weather; never guess numbers, and call only the tools you need (several at once when you can). Answer questions without changing anything; change the plan only when they ask you to or agree to it (future days with update_training_plan, today's workout with save_daily_log), and say what you changed, with the rule IDs behind it. If they ask for more than the limits allow (like a run longer than the spike guard allows), say no kindly, give the most he can do instead, and say why with the rule ID. For a goal, save it with set_goal and write the plan with update_training_plan (the plan only goes through the end of next week in this demo). Under 120 words. Short "- " bullet lists are fine; no headings or tables."""

# ---------- The tools ----------

def _days(description="How many days back to look, 1 to 14. Default 7."):
    return {"type": "object", "properties": {"days": {"type": "integer", "description": description}}}


NO_INPUT = {"type": "object", "properties": {}}
TOOLS = [
    {"name": "get_whoop_recovery", "description": "Daily WHOOP recovery (0-100%), HRV (ms), and resting heart rate, newest first, plus 30-day averages under 'normal_30_day'.", "input_schema": _days()},
    {"name": "get_whoop_sleep", "description": "Recent nights, newest first: hours asleep vs. needed, deep/REM/light/awake hours, bedtime, and wake time.", "input_schema": _days()},
    {"name": "get_whoop_strain", "description": "Daily WHOOP strain (0-21, how hard the whole day was), newest first.", "input_schema": _days()},
    {"name": "get_whoop_workouts", "description": "Recorded workouts (sport, miles, minutes) for the last 14 days, and running miles this week and last week.", "input_schema": NO_INPUT},
    {"name": "get_personal_patterns", "description": "What's normal for THIS person: 7-day HRV and resting heart rate vs. his own normal range, usual sleep, training load, and what moves his recovery (five fixed questions from his last 90 days, each with a likely range and how sure it is). Use it to make advice personal.", "input_schema": NO_INPUT},
    {"name": "get_sleep_plan", "description": "Recommended bedtime and wake-up time for tonight and the next 6 nights, from his sleep need (at least 7 hours), sleep efficiency, usual wake time, and first event each morning. Also his last-coffee time, energy-drink time, and when hard workouts should be over [S4].", "input_schema": NO_INPUT},
    {"name": "get_weather", "description": "Today's and tomorrow's forecast in Winston-Salem, with an hour-by-hour list for today.", "input_schema": NO_INPUT},
    {"name": "get_calendar_events", "description": "His Google Calendar from today: each day's events (24-hour times) and free windows of 30+ minutes between 06:00 and 21:00, marked if they're in the times he likes to run (6-9am, 3-6pm).", "input_schema": _days("How many days to look at, starting today, 1 to 7. Default 2.")},
    {"name": "get_training_plan", "description": "The plan from today through the end of next week: each day's workout.", "input_schema": NO_INPUT},
    {"name": "get_training_history", "description": "The last 10 days of the plan and whether each was done.", "input_schema": NO_INPUT},
    {"name": "update_training_plan", "description": "Change planned workouts for today through the end of next week. A date that already has a workout gets replaced. Every run is checked against the spike guard [L1] (at most 110% of the longest run in the 30 days before it, counting earlier planned runs as done) and today's workout against today's limits; workouts that break them are turned down with the reason. Write run distances in miles.",
     "input_schema": {"type": "object", "properties": {"workouts": {"type": "array", "items": {"type": "object", "properties": {
         "date": {"type": "string", "description": "YYYY-MM-DD"},
         "workout": {"type": "string", "description": "Like 'Easy run, 2.5 miles' or 'Rest day'"}}, "required": ["date", "workout"]}}},
         "required": ["workouts"]}},
    {"name": "save_daily_log", "description": "Save today's final workout decision (shown in the 'Today's workout' card). It's turned down if it breaks today's limits (intensity, running allowed, longest run).",
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


def _days_between(first, second):
    return round((time.mktime(time.strptime(second, "%Y-%m-%d")) - time.mktime(time.strptime(first, "%Y-%m-%d"))) / 86400)


# ---------- The same limit rules as facts.py ----------

LEVELS = ["rest", "easy", "moderate", "hard"]
MILES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mi\b|miles?\b)", re.IGNORECASE)


def is_run(text):
    words = (text or "").lower()
    return bool(re.search(r"\b(run|jog)", words)) and not re.search(r"\bno run", words)


def run_miles(text):
    found = MILES.search(text or "")
    return float(found.group(1)) if found and is_run(text) else None


def intensity(text):
    """How hard a workout written in words is: rest, easy, moderate, or hard."""
    words = (text or "").lower()
    if re.match(r"\s*(rest|off|day off)\b", words):
        return "rest"
    words = re.sub(r"\b(no|not|avoid|skip|without)\s+(\w+\s+)?(hard|intervals?|tempo|speed)\b", "", words)
    if re.search(r"\b(intervals?|tempo|threshold|speed ?work|fartlek|hill repeats?|repeats|race[- ]pace|time trial|vo2|sprints?|hard)\b", words):
        return "hard"
    if re.search(r"\b(moderate|steady|strides|progression|pick-?ups?)\b", words):
        return "moderate"
    if re.search(r"\b(tennis|soccer|basketball|volleyball|pickup|game|match|lifting|weights|strength|crossfit|hiit)\b", words) \
            and not re.search(r"\b(light|easy|gentle)\b", words):
        return "moderate"
    if not re.search(r"\b(run|jog|bike|biking|cycl|swim|row|elliptical|spin|tennis|soccer|basketball|lift|strength|weights|hiit|workout|training)", words):
        return "rest"
    return "easy"


# ---------- The checker (the same checks as checker.py) ----------

CONDITIONS = re.compile(
    r"\b(stress fractures?|shin splints|tendini?tis|tendonitis|plantar fasciitis|it band syndrome|runner'?s knee|"
    r"covid(?:-19)?|the flu|influenza|an infection|a virus|overtraining syndrome|mononucleosis|mono|strep|"
    r"pneumonia|anemia|asthma|a sprain|a fracture)\b", re.IGNORECASE)
SICK = re.compile(r"\byou(?:'re| are)(?: probably| likely)? (?:getting |coming down with |)sick\b|\bhe(?:'s| is)(?: probably| likely)? (?:getting |coming down with |)sick\b", re.IGNORECASE)
NOT_A_DIAGNOSIS = re.compile(r"(can'?t|cannot|not|n't|whether|if|rule out|no way to|unable to|only a|a doctor can|an athletic trainer can)\b[^.]{0,40}$", re.IGNORECASE)
SEE_SOMEONE = re.compile(r"\b(doctor|athletic trainer|physician|student health|health center|nurse)\b", re.IGNORECASE)
CITED = re.compile(r"\[([A-Z]\d(?:\s*,\s*[A-Z]\d)*)\]")
TIME = r"(\d{1,2}:\d{2}\s*[AaPp]\.?\s?[Mm]\.?)"
BEDTIME = re.compile(r"\b(?:bedtime|in bed|to bed|lights[- ]out|asleep by|sleep by)\b([^\d.!?]{0,20}?)" + TIME, re.IGNORECASE)
CAFFEINE = re.compile(r"\b(?:coffee|caffeine|energy drinks?|pre-?workout)\b([^\d.!?]{0,25}?)" + TIME, re.IGNORECASE)
NOT_THAT_TIME = re.compile(r"\b(wake|up at|alarm|until|class|run|workout)\b", re.IGNORECASE)
CHECKS = 9


def _time(text):
    found = re.match(r"(\d{1,2}):(\d{2})\s*([AaPp])", text)
    return f"{int(found.group(1))}:{found.group(2)} {found.group(3).upper()}M"


def check(reply, facts, mode, saved_workout):
    """Problems with one reply (an empty list means it passed). facts: the packet the page sent."""
    problems, caps = [], facts.get("_caps") or {}
    for found in list(CONDITIONS.finditer(reply)) + list(SICK.finditer(reply)):
        if not NOT_A_DIAGNOSIS.search(reply[max(0, found.start() - 60):found.start()]):
            problems.append(f"It names a medical condition ('{found.group(0)}'). Don't diagnose: describe the warning sign and suggest an athletic trainer or doctor.")
            break
    cited = [rule.strip() for group in CITED.findall(reply) for rule in group.split(",")]
    unknown = sorted(set(cited) - set(RESEARCH_RULES))
    if unknown:
        problems.append(f"It cites rule IDs that don't exist: {', '.join(unknown)}. Use only the listed IDs.")
    if (mode == "plan" or saved_workout) and not cited:
        problems.append("It recommends a workout without citing the rules it follows. End those sentences with rule IDs in square brackets, like [R1, L1].")
    if saved_workout and caps:
        level = intensity(saved_workout)
        if LEVELS.index(level) > LEVELS.index(caps.get("max", "hard")):
            problems.append(f"Today's workout ('{saved_workout}') is {level}, but today's limit is {caps['max']}. Change it with save_daily_log.")
        if not caps.get("running", True) and is_run(saved_workout):
            problems.append(f"Today's workout ('{saved_workout}') is a run, but running isn't allowed today [B2].")
        miles = run_miles(saved_workout)
        if miles and caps.get("exact") and miles > caps["exact"] + 0.01:
            problems.append(f"Today's run is {miles:g} miles, over the spike guard's {caps['limit']:g} miles [L1].")
    if facts.get("_rest_or_red") and not SEE_SOMEONE.search(reply):
        problems.append("Today's facts call for rest or a pain warning, so the reply must suggest checking in with a doctor or athletic trainer if it continues.")
    bedtimes, cutoffs = set(facts.get("_bedtimes") or []), set(facts.get("_cutoffs") or [])
    for gap, said in BEDTIME.findall(reply):
        if bedtimes and not NOT_THAT_TIME.search(gap) and _time(said) not in bedtimes:
            problems.append(f"It gives a bedtime of {_time(said)}, but the sleep plan's bedtimes are {', '.join(sorted(bedtimes))}. Use get_sleep_plan's exact times.")
    for gap, said in CAFFEINE.findall(reply):
        if cutoffs and not NOT_THAT_TIME.search(gap) and _time(said) not in cutoffs:
            problems.append(f"It gives a caffeine time of {_time(said)}, which isn't in the sleep plan [S4].")
    return problems


def fallback(facts, mode):
    """A plain reply from the facts alone, if the checker still finds problems after one retry."""
    limits = facts.get("limits_for_today") or {}
    head = {"rest": "Rest day", "easy": "Easy day", "moderate": "Moderate at most today", "hard": "Follow the plan"}
    reasons = [f"{reason[5:]} [{reason[1:3]}]." for reason in (limits.get("because") or [])[:2] if reason.startswith("[")]
    sleep = facts.get("sleep") or {}
    if sleep.get("tonight_in_bed_by"):
        reasons.append(f"Tonight, be in bed by {sleep['tonight_in_bed_by']} [S3].")
    if facts.get("_rest_or_red"):
        reasons.append("If it continues, check in with a doctor or athletic trainer.")
    if mode == "plan":
        return f"{head.get(limits.get('max_intensity'), 'Easy day')}\n{' '.join(reasons)}"
    return "I couldn't double-check a full answer to that, so here's what the data says today: " + " ".join(reasons)


# ---------- What each tool does (on the sample data the page sent) ----------

class Demo:
    """One request: the sample data, plus the changes the coach makes (sent back to the page)."""

    def __init__(self, data):
        self.data = data
        self.today = data["today"]
        self.now = _minutes(data["now"])
        self.changes = {"plan": {}, "suggested": None, "today": None, "notes": [], "goal": None}
        self.facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
        self.caps = self.facts.get("_caps") or {}

    def breaks_todays_limits(self, workout):
        """Why a workout for today breaks today's limits, or None."""
        if not self.caps:
            return None
        level = intensity(workout)
        if LEVELS.index(level) > LEVELS.index(self.caps.get("max", "hard")):
            why = "; ".join((self.facts.get("limits_for_today") or {}).get("because") or [])
            return f"'{workout}' is {level}, but today's limit is {self.caps['max']} ({why})."
        if not self.caps.get("running", True) and is_run(workout):
            return f"'{workout}' is a run, but running isn't allowed today [B2]. Use cross-training at the same effort."
        miles = run_miles(workout)
        if miles and self.caps.get("exact") and miles > self.caps["exact"] + 0.01:
            return f"'{workout}' is longer than the spike guard allows today: {self.caps['limit']:g} miles [L1]."
        return None

    def spike_problems(self, planned):
        """The spike guard [L1] for the whole plan: each run at most 110% of the longest run in the
        30 days before it, counting earlier planned runs as done. Returns {date: problem} for `planned`."""
        history = [(run["date"], run["miles"]) for run in self.facts.get("_runs") or [] if run.get("miles")]
        whole = {iso: self.workout(iso) for iso in self.future_dates(14)}
        whole.update(planned)
        problems = {}
        for iso in sorted(whole):
            miles = run_miles(whole[iso])
            if not miles:
                continue
            window = [m for d, m in history if 0 < _days_between(d, iso) <= 30]
            limit = 1.10 * max(window) if window else 2.0
            if iso in planned and miles > limit + 0.01:
                problems[iso] = (f"{_weekday(iso)} {iso}: '{whole[iso]}' is too long. The spike guard [L1] allows up to "
                                 f"{int(limit * 10 + 1e-6) / 10:g} miles that day (longest run in the 30 days before it: "
                                 f"{max(window) if window else 'none'} miles).")
                continue
            history.append((iso, miles))
        return problems

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
        allowed, planned, skipped = set(self.future_dates(14)), {}, []
        for item in (tool_input.get("workouts") or [])[:14]:
            iso, text = str(item.get("date", "")), _short(item.get("workout", ""), 80)
            if iso in allowed and text:
                planned[iso] = text
            else:
                skipped.append(iso)
        problems = self.spike_problems(planned)
        if self.today in planned and self.today not in problems:
            problem = self.breaks_todays_limits(planned[self.today])
            if problem:
                problems[self.today] = problem
        for iso, text in planned.items():
            if iso not in problems:
                self.changes["plan"][iso] = text
        return {"saved": len(planned) - len(problems), "skipped_dates_outside_the_plan": skipped,
                **({"turned_down": list(problems.values()),
                    "what_to_do": "Shorten or swap the turned-down days and call update_training_plan again for them."} if problems else {}),
                "note": "On changed days, the old workout blocks are taken off his calendar automatically."}

    def save_daily_log(self, tool_input):
        workout = _short(tool_input.get("workout", ""), 80)
        problem = self.breaks_todays_limits(workout)
        if problem:
            raise ValueError(f"Not saved: {problem} Pick a workout within today's limits.")
        self.changes["today"] = {"workout": workout, "reason": _short(tool_input.get("reason", ""), 400)}
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
        if name == "update_training_plan" and output.get("turned_down"):
            return f"changed {output['saved']} days; turned down {len(output['turned_down'])}: {output['turned_down'][0]}"
        if name == "update_training_plan":
            return f"changed {output['saved']} days of the plan"
        if name == "get_personal_patterns":
            found = sum(1 for p in output.get("what_moves_his_recovery", []) if p.get("finding"))
            return f"HRV {output['hrv']['status']}, {found} of 5 recovery questions answered"
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
    shown = {key: value for key, value in (data.get("facts") or {}).items() if not key.startswith("_")}  # _ keys are for the checker
    facts = json.dumps(shown, ensure_ascii=False) if shown else "(none sent)"
    return f"""Today is {data['today_text']}, and it's {_clock(_minutes(data['now']))} where he lives (America/New_York).

About him, in his own words:
- Name: Clive Curry
- Goal: {profile['goal']}
- Goal date: {profile['goal_date'] or 'none'}
- What he does now: {profile['experience']}
- Times he's willing to run: {profile['run_times']}
- Location: Winston-Salem, North Carolina

What you remember about him:
{notes}

TODAY'S FACTS (worked out by code from his data; never go over the limits):
{facts}"""


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

    caps = demo.caps
    steps, answer, retried = [], None, False
    if caps:
        steps.append(["fact_check", None, f"Worked out today's facts in code: limit {caps.get('max')}"
                      + (" · no running" if not caps.get("running", True)
                         else "" if caps.get("max") == "rest" else f" · runs up to {caps.get('limit')} mi") + ".", "check"])
    for _ in range(MAX_STEPS + 1):
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
            saved = (demo.changes["today"] or {}).get("workout")
            problems = check(answer, demo.facts, mode, saved) if demo.facts else []
            if problems and not retried:
                retried = True
                steps.append(["rule", None, "Checker caught: " + " ".join(problems) + " Sent back to the coach to fix.", "caught"])
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": "An automatic check found problems with that reply, before anyone saw it:\n"
                                 + "\n".join(f"- {p}" for p in problems)
                                 + "\nFix them (use tools if the plan has to change), then write the whole reply again."})
                answer = None
                continue
            if problems:
                answer = fallback(demo.facts, mode)
                steps.append(["rule", None, "Checker still found problems, so the reply was replaced with a plain one built only from today's facts.", "caught"])
            elif demo.facts:
                steps.append(["verified", None, f"Checker: passed all {CHECKS} checks" + (" on the second try." if retried else "."), "check"])
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
