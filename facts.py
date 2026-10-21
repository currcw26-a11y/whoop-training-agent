# facts.py: the rules engine. Code decides today's limits; the AI coach explains them.
#
# Why: in Google's study of an AI health agent, a model doing math on wearable numbers in plain
# text got 22% of questions right, and 74-84% when the math was done in code. So everything that
# can be worked out is worked out here, from the person's own data, and handed to Claude as one
# "facts packet" with rule IDs it has to cite. checker.py then checks Claude's reply against it.
#
#   check_engine()  R1  how many of 4 body signals are outside the person's normal
#   read_checkin()  R3  how they say they feel (the 10-second morning check-in)
#   pain_light()    B2  pain -> green / yellow / red
#   run_limit()     L1  the longest run they can safely do on a given day
#   limits()            all of the above (plus R2, R4, A2, P3) -> today's caps
#   daily()             everything for today, for the home page and for Claude
# Sources for every rule are in RESEARCH.md.

import math
import re
from datetime import timedelta

import numpy as np
import pandas as pd

import personal

RULES = {  # ID: (short name, the rule in one sentence, where it comes from)
    "R1": ("Check engine light",
           "If 2+ of resting heart rate, breathing rate, skin temperature, and HRV are outside your normal range, "
           "today is easy; 3+, or 2+ two days in a row, means rest.",
           "Mishra et al. 2020; Miller et al. 2020; Mason et al. 2022"),
    "R2": ("Readiness trend",
           "HRV and resting heart rate are judged by their 7-day average against your normal range: HRV below and "
           "resting HR above means easy days until they're back. A red recovery means easy; red on 2 of the last 3 "
           "days means rest.",
           "Plews et al. 2013; Vesterinen et al. 2016"),
    "R3": ("Morning check-in",
           "How you say you feel counts: a low check-in plus any body signal that's off means an easy day.",
           "Saw, Main & Gastin 2016 (review of 56 studies)"),
    "R4": ("Short sleep",
           "After 6 hours of sleep or less, afternoon and evening workouts stay easy and morning ones moderate.",
           "Craven et al. 2022 (meta-analysis)"),
    "L1": ("Spike guard",
           "No run longer than 110% of your longest run in the 30 days before it.",
           "Frandsen et al. 2025 (5,205 runners)"),
    "L3": ("Weekly build",
           "Weekly running grows no more than about 30% over two weeks. 10% a week is a comfortable default, "
           "not a proven rule.",
           "Nielsen et al. 2014; Buist et al. 2008"),
    "B1": ("Mostly easy",
           "About 80% of training time easy (heart-rate zones 1-2), at least 2 rest or cross-training days a week, "
           "and at most one faster session a week for newer runners, only on a good day. Run/walk is fine.",
           "Muñoz et al. 2014; Hottenrott et al. 2016"),
    "B2": ("Pain check",
           "Pain on one spot of the bone, pain at rest or at night, pain getting worse, or 5+/10 means no running "
           "and see an athletic trainer; 3-4/10, or pain still there the morning after a run, means cross-training "
           "at the same effort until it's gone.",
           "Warden et al. 2014, 2021"),
    "S1": ("Steady sleep",
           "Keep bedtimes and wake-up times steady; students with more regular sleep earned better grades.",
           "Phillips et al. 2017; Sletten et al. 2023"),
    "S3": ("Sleep target",
           "At least 7 hours of sleep, more when WHOOP says you need it.",
           "Watson et al. 2015; Walsh et al. 2021"),
    "S4": ("Caffeine and late workouts",
           "Last coffee 9 hours before bedtime, energy drinks or pre-workout 13 hours before, and hard workouts "
           "over at least 2 hours before bedtime.",
           "Gardiner et al. 2023; Frimpong et al. 2021"),
    "A2": ("Exams",
           "No hard session within 2 days before an exam and nothing in the 2 hours before it; keep wake-up times "
           "steady and protect sleep the whole week before.",
           "Okano et al. 2019; Mann et al. 2016"),
    "W1": ("Weather",
           "Move runs away from thunderstorms, heavy rain, ice, and feels-like temperatures above about 90°F or "
           "below about 20°F, or go indoors.",
           "Roberts et al. 2021 (ACSM); Walsh et al. 2013 (NATA)"),
    "P3": ("Your patterns",
           "A consistent pattern in your own data can make a day easier, never harder.",
           "Kravitz & Duan 2014 (n-of-1 guide)"),
}

LEVELS = ["rest", "easy", "moderate", "hard"]
EXAM = re.compile(r"\b(exam|midterm|final|quiz|test)s?\b", re.IGNORECASE)
MILES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mi\b|miles?\b)", re.IGNORECASE)
MINUTES = re.compile(r"(\d+)\s*(?:min\b|mins\b|minutes?\b)", re.IGNORECASE)


def clock(moment):
    """10:45 PM"""
    return f"{moment:%I:%M %p}".lstrip("0")


# ---------- What kind of workout is this? ----------

def is_run(text):
    words = (text or "").lower()
    return bool(re.search(r"\b(run|jog)", words)) and not re.search(r"\bno run", words)


def intensity(text):
    """How hard a workout written in words is: rest, easy, moderate, or hard.
    Walks, yoga, and stretching count as rest-level ("very easy movement")."""
    words = (text or "").lower()
    if re.match(r"\s*(rest|off|day off)\b", words):
        return "rest"
    words = re.sub(r"\b(no|not|avoid|skip|without)\s+(\w+\s+)?(hard|intervals?|tempo|speed)\b", "", words)
    if re.search(r"\b(intervals?|tempo|threshold|speed ?work|fartlek|hill repeats?|repeats|race[- ]pace|time trial|"
                 r"vo2|sprints?|hard)\b", words):
        return "hard"
    if re.search(r"\b(moderate|steady|strides|progression|pick-?ups?)\b", words):
        return "moderate"
    if re.search(r"\b(tennis|soccer|basketball|volleyball|pickup|game|match|lifting|weights|strength|crossfit|hiit)\b",
                 words) and not re.search(r"\b(light|easy|gentle)\b", words):
        return "moderate"
    if not re.search(
            r"\b(run|jog|bike|biking|cycl|swim|row|elliptical|spin|tennis|soccer|basketball|lift|strength|weights|"
            r"hiit|workout|training)", words):
        return "rest"
    return "easy"


def run_size(text):
    """(miles, minutes) for a run written in words, like 'Long run/walk, 3.5 miles'; None if it isn't a run."""
    if not is_run(text):
        return None
    miles, minutes = MILES.search(text), MINUTES.search(text)
    if not miles and not minutes:
        return None
    return (float(miles.group(1)) if miles else None, float(minutes.group(1)) if minutes else None)


# ---------- R1: the check engine light ----------

SIGNALS = [  # column, name, unit, which way is a warning, smallest change that counts (a design choice)
    ("resting_hr", "Resting HR", "bpm", +1, 3.0),
    ("respiratory_rate", "Breathing rate", "/min", +1, 1.0),
    ("skin_temp_c", "Skin temp", "°C", +1, 0.5),   # bigger than the ~0.3 °C menstrual-cycle shift
    ("hrv_ms", "HRV", "ms", -1, 0.10),             # on a log scale, so 0.10 is about a 10% drop
]


def _signal(days, column, name, unit, sign, smallest, on_date):
    """One signal on one morning vs. the 28 mornings before it, as a "robust" z-score (the median and
    the median distance from it, so one odd night barely moves the normal). None if under 14 nights."""
    if column not in days:
        return None
    rows = days.dropna(subset=[column])
    today = rows[rows["date"] == on_date]
    base = rows[(rows["date"] < on_date) & (rows["date"] >= on_date - pd.Timedelta(days=28))][column].astype(float)
    if today.empty or len(base) < 14:
        return {"name": name, "learning": True, "nights": len(base)}
    value = float(today[column].iloc[0])
    log = column == "hrv_ms"
    x, base = (math.log(value), np.log(base)) if log else (value, base)
    middle = float(np.median(base))
    spread = float(np.median(np.abs(base - middle))) * 1.4826
    z = (x - middle) / spread if spread > 0 else 0.0
    off = sign * z >= 2 and sign * (x - middle) >= smallest
    width = max(2 * spread, smallest)  # "your normal": where it would not count as off
    show = math.exp if log else (lambda v: v)
    digits = 0 if column in ("resting_hr", "hrv_ms") else 1
    tidy = (lambda v: int(round(v))) if digits == 0 else (lambda v: round(v, 1))
    if log:
        change = f"{100 * (value / show(middle) - 1):+.0f}%"
    else:
        change = f"{value - middle:+.{digits}f} {unit}".replace(" /min", "/min")
    change = change.replace("-", "−", 1)  # a real minus sign, like the demo
    return {
        "name": name, "unit": unit, "value": tidy(value), "normal": tidy(show(middle)),
        "low": tidy(show(middle - width)), "high": tidy(show(middle + width)),
        "change": change, "off": bool(off), "warning_side": "high" if sign > 0 else "low", "learning": False,
    }


def check_engine(days):
    """R1. Counts how many of the 4 body signals are outside the person's normal this morning.
    Wearables picked up many infections before symptoms in the studies, but with false alarms,
    so it only acts when 2+ agree, and it never names an illness."""
    if days.empty:
        return None
    latest = days["date"].max()
    today = [_signal(days, *signal, latest) for signal in SIGNALS]
    yesterday = [_signal(days, *signal, latest - pd.Timedelta(days=1)) for signal in SIGNALS]
    known = [s for s in today if s and not s["learning"]]
    if len(known) < 2:
        nights = max([s["nights"] for s in today if s and s["learning"]] or [0])
        return {"alert": None, "signals": [s for s in today if s], "off": 0, "of": len(known), "date": latest.date(),
                "message": f"Still learning your normal ({min(nights, 14)} of 14 nights so far)."}
    off = [s for s in known if s["off"]]
    two_days = len(off) >= 2 and sum(1 for s in yesterday if s and not s["learning"] and s["off"]) >= 2
    alert = "rest" if len(off) >= 3 or two_days else "easy" if len(off) == 2 else "ok"
    details = ", ".join(f"{s['name'].lower().replace('hrv', 'HRV').replace('resting hr', 'resting HR')} {s['change']}"
                        for s in off)
    if alert == "ok" and not off:
        message = f"All {len(known)} body signals are in your normal range."
    elif alert == "ok":
        message = (f"1 of {len(known)} body signals is outside your normal range ({details}). One on its own is usually "
                   "noise, like a late meal, a hot room, or alcohol, so it doesn't change the plan.")
    else:
        when = " for the second day in a row" if two_days and len(off) < 3 else " today"
        message = (f"{len(off)} of your {len(known)} body signals are outside your normal range{when} ({details}). "
                   f"That pattern often shows up when your body is fighting something, so today is "
                   f"{'a rest day' if alert == 'rest' else 'easy'}. If you feel sick or it lasts 3+ days, check in "
                   "with a doctor.")
    return {"alert": alert, "signals": [s for s in today if s], "off": len(off), "of": len(known),
            "two_days": two_days, "date": latest.date(), "message": message}


# ---------- R3 and B2: the morning check-in ----------

QUESTIONS = [("energy", "Energy"), ("stress", "Stress"), ("soreness", "Soreness"), ("sleep_quality", "Sleep quality")]


def wellness(checkin):
    """4 to 20, higher is better: energy + (6 - stress) + (6 - soreness) + sleep quality, each 1 to 5."""
    try:
        return checkin["energy"] + (6 - checkin["stress"]) + (6 - checkin["soreness"]) + checkin["sleep_quality"]
    except (KeyError, TypeError):
        return None


def read_checkin(checkin, earlier):
    """R3. How today's check-in compares with the person's own earlier ones (or a fixed cut-off of
    10 of 20 until there are 14 to compare with)."""
    score = wellness(checkin) if checkin else None
    if score is None:
        return None
    past = np.array([s for s in (wellness(c) for c in earlier) if s is not None], dtype=float)
    if len(past) >= 14:
        middle = float(np.median(past))
        spread = float(np.median(np.abs(past - middle))) * 1.4826 or 1.0
        low = (score - middle) / spread <= -1.5
        judged = f"compared with your last {len(past)} check-ins (usual {middle:.0f} of 20)"
    else:
        low = score <= 10
        judged = "a fixed cut-off of 10 of 20 until there are 14 check-ins to compare with"
    return {"score": score, "out_of": 20, "low": bool(low), "good": bool(not low and score >= 14 and checkin["energy"] >= 3),
            "judged": judged, **{key: checkin[key] for key, _ in QUESTIONS}}


def pain_light(checkin, ran_yesterday):
    """B2. Pain -> green, yellow, or red, with what to do. It describes warning signs; it never diagnoses."""
    if not checkin:
        return None
    pain = checkin.get("pain") or 0
    where = (checkin.get("pain_where") or "").strip()
    warnings = [text for flag, text in [
        (checkin.get("pain_one_spot"), "pain you can press on one spot of the bone"),
        (checkin.get("pain_at_rest"), "pain at rest or at night"),
        (checkin.get("pain_worse"), "pain that got worse over your last 2 runs"),
        (pain >= 5, f"{pain}/10 pain"),
    ] if flag]
    place = f"Your {where.lower()}" if where else "The pain"
    if warnings:
        return {"light": "red", "pain": pain, "where": where, "running": False,
                "message": f"{warnings[0][0].upper()}{warnings[0][1:]} is a warning sign worth getting checked. Running "
                           "is paused; an athletic trainer can help you decide when to restart."}
    if pain >= 3 or (pain >= 1 and ran_yesterday):
        why = f"{place} is {pain}/10 this morning" if pain >= 3 else \
            f"{place} is still there ({pain}/10) the morning after a run"
        return {"light": "yellow", "pain": pain, "where": where, "running": False,
                "message": f"{why}, so today's run becomes easy cross-training, like cycling, at the same effort. "
                           "We'll check again tomorrow."}
    if pain:
        return {"light": "green", "pain": pain, "where": where, "running": True,
                "message": f"{place} is {pain}/10 with no warning signs, so the plan stays. "
                           "Tell the coach if it's still there tomorrow."}
    return {"light": "green", "pain": 0, "where": "", "running": True, "message": "Nothing to change in the plan."}


# ---------- L1: the spike guard ----------

def done_runs(workouts):
    """Runs WHOOP recorded, as (date, miles, minutes)."""
    if workouts.empty:
        return []
    runs = workouts[workouts["sport"].str.contains("run", case=False, na=False)]
    return [(row["date"].date(), None if pd.isna(row.get("distance_miles")) else float(row["distance_miles"]),
             None if pd.isna(row.get("minutes")) else float(row["minutes"])) for _, row in runs.iterrows()]


def _floor(value, digits=1):
    return math.floor(value * 10 ** digits + 1e-6) / 10 ** digits


def run_limit(day, runs):
    """L1 for a run on `day`: at most 110% of the longest run in the 30 days before it.
    In a study of 5,205 runners, injuries rose when a single run went more than 10% past that.
    No runs in the last 30 days means starting back: 25 minutes, or 2 miles, of run/walk."""
    window = [run for run in runs if day - timedelta(days=30) <= run[0] < day]
    if not window:
        return {"longest_miles": None, "longest_minutes": None, "longest_on": None,
                "limit_miles": 2.0, "exact_miles": 2.0, "limit_minutes": 25, "starting_back": True}
    by_miles = [run for run in window if run[1]]
    by_minutes = [run for run in window if run[2]]
    longest = max(by_miles, key=lambda run: run[1]) if by_miles else None
    longest_time = max(by_minutes, key=lambda run: run[2]) if by_minutes else None
    return {
        "longest_miles": round(longest[1], 1) if longest else None,
        "longest_on": longest[0] if longest else None,
        "limit_miles": _floor(1.10 * longest[1]) if longest else None,
        "exact_miles": 1.10 * longest[1] if longest else None,  # for checking; the floored one is for showing
        "longest_minutes": int(longest_time[2]) if longest_time else None,
        "limit_minutes": int(1.10 * longest_time[2]) if longest_time else None,
        "starting_back": False,
    }


def spike_percent(size, limit):
    """How a run compares with the 30-day longest, like 104 (%), or None."""
    miles, minutes = size
    if miles and limit.get("longest_miles"):
        return round(100 * miles / limit["longest_miles"])
    if minutes and limit.get("longest_minutes"):
        return round(100 * minutes / limit["longest_minutes"])
    return None


def check_runs(planned, runs):
    """L1 for a whole plan. planned: (date, workout text) pairs. A planned run counts as done once its
    day has passed, so a plan can still build about 10% per long run. Returns {date: problem}."""
    history, problems = list(runs), {}
    for day, text in sorted(planned):
        size = run_size(text)
        if not size:
            continue
        limit = run_limit(day, history)
        miles, minutes = size
        if miles and limit["limit_miles"] is not None and miles > (limit.get("exact_miles") or limit["limit_miles"]) + 0.01:
            longest = (f"{limit['longest_miles']:g} mi on {limit['longest_on']:%b} {limit['longest_on'].day}"
                       if limit["longest_miles"] else "none, so this is starting back")
            problems[day] = (f"{day:%a %b} {day.day}: '{text}' is too long. The spike guard [L1] allows up to "
                             f"{limit['limit_miles']:g} miles that day (longest run in the 30 days before it: {longest}).")
            continue
        if not miles and minutes and limit["limit_minutes"] is not None and minutes > limit["limit_minutes"] + 0.5:
            problems[day] = (f"{day:%a %b} {day.day}: '{text}' is too long. The spike guard [L1] allows up to "
                             f"{limit['limit_minutes']} minutes of running that day.")
            continue
        history.append((day, miles, minutes))
    return problems


# ---------- Today's limits ----------

def limits(*, today, days, engine, hrv, resting, checkin, pain, sleep_hours, exams, patterns, runs):
    """Every rule that applies today, and the limits they add up to."""
    level, because = "hard", []

    def cap(to, rule, text):
        nonlocal level
        if LEVELS.index(to) < LEVELS.index(level):
            level = to
        because.append({"rule": rule, "cap": to, "text": text})

    if engine and engine["alert"] in ("easy", "rest"):
        cap(engine["alert"], "R1", f"{engine['off']} of {engine['of']} body signals are outside your normal range"
            + (" for the second day in a row" if engine.get("two_days") and engine["off"] < 3 else ""))
    scores = days.dropna(subset=["recovery_score"]).sort_values("date").tail(3)["recovery_score"].tolist()
    if len(scores) >= 2 and sum(score < 34 for score in scores) >= 2 and scores[-1] < 34:
        cap("rest", "R2", "Recovery has been red on 2 of the last 3 days")
    elif scores and scores[-1] < 34:
        cap("easy", "R2", f"Recovery is {scores[-1]:.0f}% (red)")
    hrv_low = bool(hrv and hrv["place"] == "below")
    rhr_high = bool(resting and resting["place"] == "above")
    if hrv_low and rhr_high:
        cap("easy", "R2", "Your 7-day HRV is below your normal range and your resting HR is above it, a pattern "
                          "linked to not coping well with recent training")
    elif hrv_low and not (checkin and checkin["good"]):
        cap("moderate", "R2", "Your 7-day HRV is below your normal range, so a hard session waits for a good check-in")
    if checkin and checkin["low"] and ((engine and engine["off"]) or hrv_low or rhr_high):
        cap("easy", "R3", f"You rated your morning {checkin['score']} of 20 and a body signal is low too")
    afternoon = None
    if sleep_hours is not None and sleep_hours <= 6:
        cap("moderate", "R4", f"You slept {sleep_hours:g} hours, and short sleep hurts afternoon workouts most")
        afternoon = "easy"
    running = True
    if pain and pain["light"] == "red":
        running = False
        cap("easy", "B2", "A pain warning sign means no running until an athletic trainer checks it")
    elif pain and pain["light"] == "yellow":
        running = False
        because.append({"rule": "B2", "cap": None,
                        "text": "Pain this morning means cross-training at the same effort instead of running"})
    for exam in exams:
        away = (exam["date"] - today).days
        if away in (0, 1):
            cap("easy", "A2", f"{exam['title']} is {'today' if away == 0 else 'tomorrow'}")
            break
        if away == 2:
            cap("moderate", "A2", f"{exam['title']} is in 2 days, so no hard session")
            break
    for pattern in patterns:
        if pattern.get("can_make_easier") and pattern.get("applies_today"):
            cap("easy", "P3", pattern["action"])
    spike = run_limit(today, runs)
    return {"max_intensity": level, "afternoon_max": afternoon, "running_allowed": running,
            "longest_run_miles": spike["limit_miles"], "longest_run_minutes": spike["limit_minutes"],
            "spike": spike, "because": because}


# ---------- Everything for today ----------

def daily(*, days, workouts, checkins, nights, patterns, exams, today, time_zone):
    """Everything code decides for today, used by the home page and sent to Claude.
    days/workouts: tables from personal.as_frames(); checkins: saved check-ins, newest first;
    nights: sleep_plan.plan_week(); patterns: patterns.find(); exams: upcoming {date, title, start}."""
    engine = check_engine(days)
    hrv = personal.trend(days, "hrv_ms", log_scale=True)
    resting = personal.trend(days, "resting_hr", higher_is_better=False)
    todays = next((c for c in checkins if str(c["date"]) == today.isoformat()), None)
    checkin = read_checkin(todays, [c for c in checkins if c is not todays])
    runs = done_runs(workouts)
    pain = pain_light(todays, any(run[0] == today - timedelta(days=1) for run in runs))
    latest = days.sort_values("date").iloc[-1] if not days.empty else None
    slept = None if latest is None or latest["date"].date() != today or pd.isna(latest.get("sleep_hours")) \
        else round(float(latest["sleep_hours"]), 1)
    caps = limits(today=today, days=days, engine=engine, hrv=hrv, resting=resting, checkin=checkin, pain=pain,
                  sleep_hours=slept, exams=exams, patterns=patterns, runs=runs)
    regular = personal.sleep_regularity(personal.sleep_nights(days, time_zone, today))
    regular_before = personal.sleep_regularity(personal.sleep_nights(days, time_zone, today - timedelta(days=14)))
    return {"today": today, "engine": engine, "hrv": hrv, "resting": resting, "checkin": checkin, "checkin_row": todays,
            "pain": pain, "limits": caps, "nights": nights, "slept": slept, "exams": exams, "patterns": patterns,
            "regularity": regular, "regularity_before": regular_before}


def packet(facts):
    """The facts packet Claude gets at the start of every run, as plain JSON-ready data."""
    caps, engine, tonight = facts["limits"], facts["engine"], facts["nights"][0] if facts["nights"] else None
    spike = caps["spike"]
    return {
        "limits_for_today": {
            "max_intensity": caps["max_intensity"],
            **({"afternoon_and_evening_max": caps["afternoon_max"]} if caps["afternoon_max"] else {}),
            "running_allowed": caps["running_allowed"],
            "longest_run_allowed_today": (f"{caps['longest_run_miles']:g} miles" if caps["longest_run_miles"] else None)
            if not spike["starting_back"] else "2 miles or 25 minutes of run/walk (no runs in 30 days, so starting back)",
            "because": [f"[{b['rule']}] {b['text']}" for b in caps["because"]] or ["Nothing limits today."],
        },
        "spike_guard_L1": {
            "longest_run_last_30_days": f"{spike['longest_miles']:g} miles on {spike['longest_on']:%a %b} {spike['longest_on'].day}"
            if spike["longest_miles"] else None,
            "longest_run_minutes_last_30_days": spike["longest_minutes"],
            "note": "update_training_plan checks every planned run against this, day by day.",
        },
        "check_engine_light_R1": None if not engine else {
            "status": engine["alert"] or "still learning", "signals_off": engine["off"], "of": engine["of"],
            "summary": engine["message"],
            "signals": {s["name"]: (f"{s['value']} {s['unit']}, normal about {s['normal']} ({s['change']})"
                                    + (" OUTSIDE normal" if s["off"] else "")) if not s["learning"] else "still learning"
                        for s in engine["signals"]},
        },
        "hrv_and_resting_hr_7_day_R2": {
            "hrv": facts["hrv"] or "still learning their normal (needs 3 weeks)",
            "resting_hr": facts["resting"] or "still learning their normal (needs 3 weeks)",
        },
        "check_in_R3": {k: facts["checkin"][k] for k in ["score", "out_of", "low", "energy", "stress", "soreness",
                                                         "sleep_quality", "judged"]}
        if facts["checkin"] else "Not done yet today. If it matters, ask how they feel.",
        "pain_B2": {k: facts["pain"][k] for k in ["light", "pain", "where", "message"]}
        if facts["pain"] and facts["pain"]["pain"] or (facts["pain"] and facts["pain"]["light"] != "green") else None,
        "sleep": None if not tonight else {
            "last_night_hours": facts["slept"],
            "tonight_in_bed_by": clock(tonight["bed"]), "wake_up": clock(tonight["wake"]),
            "sleep_need_hours": tonight["need_hours"],
            "last_coffee_by_S4": clock(tonight["caffeine_cutoff"]),
            "energy_drinks_or_pre_workout_by_S4": clock(tonight["energy_drink_cutoff"]),
            "hard_workouts_over_by_S4": clock(tonight["hard_workout_end"]),
            "sleep_regularity_score_S1": facts["regularity"], "regularity_two_weeks_before": facts["regularity_before"],
        },
        "exams_coming_up_A2": [f"{e['title']} on {e['date']:%a %b} {e['date'].day}" + (f" at {clock(e['start'])}" if e.get("start") else "")
                               for e in facts["exams"]] or None,
        "what_moves_their_recovery_P1": [
            {"question": p["question"], "label": p["label"], **({"finding": p["sentence"]} if p.get("sentence") else {}),
             **({"but_also": p["but_also"]} if p.get("but_also") else {}),
             **({"P3_applies_today": p["action"]} if p.get("can_make_easier") and p.get("applies_today") else {})}
            for p in facts["patterns"]],
        "rules": {rule: f"{name}: {text}" for rule, (name, text, _) in RULES.items()},
    }


def fallback(facts, mode):
    """A plain reply built only from the facts, for when the checker still finds problems after a retry."""
    caps = facts["limits"]
    head = {"rest": "Rest day", "easy": "Easy day", "moderate": "Moderate at most today", "hard": "Follow your plan"}
    reasons = [f"{b['text']} [{b['rule']}]." for b in caps["because"][:2]]
    tonight = facts["nights"][0] if facts["nights"] else None
    if tonight:
        reasons.append(f"Tonight, be in bed by {clock(tonight['bed'])} [S3].")
    if (facts["engine"] and facts["engine"]["alert"] == "rest") or (facts["pain"] and facts["pain"]["light"] == "red"):
        reasons.append("If it continues, check in with a doctor or athletic trainer.")
    if mode == "plan":
        return f"{head[caps['max_intensity']]}\n{' '.join(reasons)}"
    return "I couldn't double-check a full answer to that, so here's what your data says today: " + " ".join(reasons)
