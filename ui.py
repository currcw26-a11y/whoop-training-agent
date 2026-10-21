# ui.py: display helpers shared by the pages: icons, labels, the recovery ring, number tiles,
# today's agenda, the week calendar, and the agent's steps.
# Icons are Google's Material Symbols (built into Streamlit). Browse them at
# https://fonts.google.com/icons and write them like ":material/favorite:".

import html
import math
import re
from datetime import datetime, timedelta, timezone

import anthropic
import streamlit as st

import facts

TOOL_ICONS = {
    "get_whoop_recovery": "monitor_heart", "get_whoop_sleep": "bedtime", "get_whoop_strain": "local_fire_department",
    "get_whoop_workouts": "directions_run", "get_weather": "partly_cloudy_day", "get_training_plan": "calendar_month",
    "get_training_history": "history", "update_training_plan": "edit_calendar", "save_daily_log": "save",
    "get_calendar_events": "event", "create_calendar_event": "calendar_add_on",
    "get_personal_patterns": "person_search", "get_sleep_plan": "bedtime", "remember_about_them": "bookmark_add",
    "set_goal": "flag",
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


def clock(moment):
    """A time like 7:00 AM."""
    return f"{moment:%I:%M %p}".lstrip("0")


def clock_range(start, end):
    """'9:00–9:50 AM', or '11:30 AM–12:15 PM' when it crosses noon."""
    if f"{start:%p}" == f"{end:%p}":
        return f"{start:%I:%M}".lstrip("0") + "–" + clock(end)
    return f"{clock(start)}–{clock(end)}"


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


def draw(markup):
    """Show our own HTML. (st.markdown keeps drawings like the ring; extra spaces at the
    start of a line would turn it into a code block, so they're removed first.)"""
    st.markdown("".join(line.strip() for line in markup.splitlines()), unsafe_allow_html=True)


def recovery_ring(score, color, zone_name, note):
    """A WHOOP-style ring that fills up to the recovery score, in the zone's color."""
    has_score = score is not None and score == score
    filled = max(0, min(100, round(score))) if has_score else 0
    number = f"{filled}%" if has_score else "–"
    around = 2 * math.pi * 50  # the ring's length, so we can fill part of it
    return f"""
<div class="ring">
<svg viewBox="0 0 120 120" role="img" aria-label="Recovery {number}">
<circle cx="60" cy="60" r="50" fill="none" stroke="var(--grid)" stroke-width="11"/>
<circle cx="60" cy="60" r="50" fill="none" stroke="{color}" stroke-width="11" stroke-linecap="round"
stroke-dasharray="{around * filled / 100:.1f} {around:.1f}" transform="rotate(-90 60 60)"/>
<text x="60" y="63" text-anchor="middle" class="ring-num">{number}</text>
<text x="60" y="81" text-anchor="middle" class="ring-cap">RECOVERY</text>
</svg>
<div class="zone"><i class="dot" style="background:{color}"></i>{html.escape(zone_name)}</div>
<div class="zone-note">{html.escape(note)}</div>
</div>"""


def sparkline(values):
    """A tiny trend line of recent values (gray, so the number stays the star)."""
    values = [v for v in values if v is not None and v == v]
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    width, height = 140, 34
    points = " ".join(
        f"{3 + i * (width - 6) / (len(values) - 1):.1f},{height - 4 - (v - low) / ((high - low) or 1) * (height - 8):.1f}"
        for i, v in enumerate(values)
    )
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{points}" fill="none" stroke="var(--muted)" stroke-width="2" stroke-linejoin="round" '
            f'stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>')


def tile(icon, name, value, unit, change=None, digits=0, higher_is_better=True, compared_to="", trend=()):
    """One number tile: icon + name, the value, how it compares (green/red chip), and a trend line.
    higher_is_better can be True, False, or None (neither is better)."""
    missing = value is None or value != value
    shown = "–" if missing else (f"{value:.{digits}f}" if digits else f"{round(value)}")
    if change is None or change != change:
        chip = f'<span class="delta flat">{html.escape(compared_to)}</span>' if compared_to else ""
    elif round(change, digits) == 0:
        chip = f'<span class="delta flat">same</span><span class="vs">{compared_to}</span>'
    else:
        better = None if higher_is_better is None else (change > 0) == higher_is_better
        style = "flat" if better is None else ("good" if better else "bad")
        arrow = "arrow_upward" if change > 0 else "arrow_downward"
        chip = (f'<span class="delta {style}"><span class="msr">{arrow}</span>{abs(change):.{digits}f} {unit}</span>'
                f'<span class="vs">{compared_to}</span>')
    return (f'<div class="tile"><div class="label"><span class="msr">{icon}</span>{name}</div>'
            f'<div class="value">{shown}<small>{unit}</small></div>{chip}{sparkline(trend)}</div>')


def tiles(*tiles_html):
    return f'<div class="tiles">{"".join(tiles_html)}</div>'


def badges(items):
    """Small rounded tags, like "Added to Google Calendar". items: (icon, text, extra CSS class)."""
    if not items:
        return ""
    return '<div class="badges">' + "".join(
        f'<span class="badge {style}"><span class="msr">{icon}</span>{html.escape(text)}</span>'
        for icon, text, style in items) + "</div>"


def workout_kind(text):
    """Sort a workout into run / walk / cross-training / rest, for its icon."""
    words = (text or "").lower()
    if "rest" in words:
        return "rest"
    if any(word in words for word in ["bike", "cycl", "swim", "cross", "strength", "yoga", "elliptical", "tennis"]):
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


def agenda(events):
    """Today's events as a list. Each event: start, end (datetimes), title, calendar, kind, suggested."""
    colors = {"cal1": "var(--s1)", "cal2": "var(--s3)", "other": "var(--muted)", "coach": "var(--s2)"}
    rows = []
    for event in events:
        name = html.escape(event["title"])
        if event["kind"] == "coach":
            name = f'<span class="msr">directions_run</span>{name}'
        when = "All day" if event.get("all_day") else clock_range(event["start"], event["end"])
        note = "Suggested · waiting for your OK" if event.get("suggested") else html.escape(event["calendar"])
        rows.append(
            f'<li class="{"suggested" if event.get("suggested") else ""}">'
            f'<div class="t">{"All day" if event.get("all_day") else clock(event["start"])}</div>'
            f'<div class="e" style="border-color:{colors[event["kind"]]}"><b>{name}</b>'
            f'<span class="sub">{when} · {note}</span></div></li>')
    return f'<ul class="agenda">{"".join(rows)}</ul>'


def week_calendar(days, first_hour, last_hour, now):
    """A Google-Calendar-style week. Each day: date, is_today, plan (icon, text, done) or None,
    events (start, end, title, calendar, kind, exam, suggested), and all_day (same, for all-day events)."""
    px = 38  # pixels per hour
    height = (last_hour - first_hour) * px
    head = ["<div></div>"]
    for day in days:
        plan = ""
        if day["plan"]:
            icon, text, done = day["plan"]
            check = ' <span class="msr ok">check_circle</span>' if done else ""
            moving = " move" if icon in ("directions_run", "directions_walk", "exercise") else ""
            plan = f'<div class="wk-plan{moving}"><span class="msr">{icon}</span>{html.escape(text)}{check}</div>'
        name = "Today" if day["is_today"] else day["date"].strftime("%a")
        head.append(f'<div class="{"is-today" if day["is_today"] else ""}"><div class="wk-dow">{name}</div>'
                    f'<div class="wk-num">{day["date"].day}</div>{plan}</div>')
    # All-day events (like "Fall break" or a due date) get their own row, like in Google Calendar.
    all_day = ['<div class="wk-allday-label">All day</div>']
    for day in days:
        chips = "".join(f'<div class="wk-chip {e["kind"]}" title="{html.escape(e["title"])}">'
                        f'{"★ " if e.get("exam") else ""}{html.escape(e["title"])}</div>' for e in day["all_day"][:3])
        if len(day["all_day"]) > 3:
            chips += f'<div class="wk-more">+{len(day["all_day"]) - 3} more</div>'
        all_day.append(f'<div class="{"is-today" if day["is_today"] else ""}">{chips}</div>')
    times = "".join(
        f'<div style="top:{(hour - first_hour) * px}px">{(hour + 11) % 12 + 1} {"AM" if hour < 12 else "PM"}</div>'
        for hour in range(first_hour + 1, last_hour))
    columns = []
    for day in days:
        lines = "".join(f'<div class="wk-line" style="top:{(hour - first_hour) * px}px"></div>'
                        for hour in range(first_hour + 1, last_hour))
        blocks = []
        for event in day["events"]:
            start = event["start"].hour + event["start"].minute / 60
            end = start + (event["end"] - event["start"]).total_seconds() / 3600
            top, bottom = max(start, first_hour), min(end, last_hour)
            if bottom <= first_hour or top >= last_hour:
                continue  # outside the hours shown
            classes = f'wk-ev {event["kind"]}{" suggested" if event.get("suggested") else ""}'
            title = ("★ " if event.get("exam") else "") + event["title"]
            blocks.append(
                f'<div class="{classes}" title="{html.escape(title)} · {clock(event["start"])}–{clock(event["end"])}" '
                f'style="top:{(top - first_hour) * px + 1:.0f}px;height:{max((bottom - top) * px - 3, 20):.0f}px">'
                f'<b>{html.escape(title)}</b><span>{clock(event["start"])}</span></div>')
        now_line = ""
        hour_now = now.hour + now.minute / 60
        if day["is_today"] and first_hour <= hour_now < last_hour:
            now_line = f'<div class="wk-now" style="top:{(hour_now - first_hour) * px:.0f}px"></div>'
        columns.append(f'<div class="wk-col{" is-today" if day["is_today"] else ""}" style="height:{height}px">'
                       f'{lines}{"".join(blocks)}{now_line}</div>')
    all_day_row = f'<div class="wk-allday">{"".join(all_day)}</div>' if any(d["all_day"] for d in days) else ""
    return (f'<div class="wk-scroll"><div class="wk"><div class="wk-head">{"".join(head)}</div>{all_day_row}'
            f'<div class="wk-body"><div class="wk-times" style="height:{height}px">{times}</div>'
            f'{"".join(columns)}</div></div></div>')


def _hours_minutes(minutes):
    """95 -> '1 h 35 min', 120 -> '2 h', 35 -> '35 min'."""
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours} h {minutes} min" if hours and minutes else f"{hours} h" if hours else f"{minutes} min"


def _big_time(moment):
    return f'{moment:%I:%M}'.lstrip("0") + f'<small>{moment:%p}</small>'


def sleep_card(nights, now):
    """Tonight's bedtime and wake-up time in big type, and a timeline of the week ahead.
    nights come from sleep_plan.plan_week(); the first one is tonight."""
    tonight = nights[0]
    first = tonight["first"]
    late = tonight["bed"] <= now < tonight["wake"]  # it's already past bedtime
    if late:
        left = int((tonight["wake"] - now).total_seconds() // 60)
        bed_label, bed_value = "Go to sleep", "Now"
        why = (f"Your bedtime was {clock(tonight['bed'])}. If you fall asleep soon you'll still get about "
               f"{_hours_minutes(left - left % 15)} before your alarm.")
    else:
        bed_label, bed_value = "In bed by", _big_time(tonight["bed"])
        if tonight["set_by_first"]:
            why = f"Set by <b>{html.escape(first['title'])}</b> at {clock(first['start'])}."
        elif first:
            why = (f"Near your usual wake-up time, which keeps your body clock steady. "
                   f"First up: <b>{html.escape(first['title'])}</b> at {clock(first['start'])}.")
        else:
            why = "Nothing early tomorrow. Waking near your usual time keeps your body clock steady."
        # The math, so it's clear where the bedtime comes from.
        why += (f'<span class="sl-math">{tonight["need_hours"]:g} h needed ÷ {tonight["efficiency"]}% sleep efficiency '
                f'= {_hours_minutes(tonight["in_bed_minutes"])} in bed</span>')
    chips = []
    shift = tonight["earlier_than_usual_minutes"]
    if shift is not None and abs(shift) >= 15:
        chips.append(("schedule", f"{_hours_minutes(abs(shift))} {'earlier' if shift > 0 else 'later'} than usual", ""))
    left_side = (
        f'<div class="sl-tonight"><div class="sl-pair">'
        f'<div class="sl-time"><div class="k"><span class="msr">bedtime</span>{bed_label}</div>'
        f'<div class="big">{bed_value}</div></div>'
        f'<div class="sl-time"><div class="k"><span class="msr">alarm</span>Wake up</div>'
        f'<div class="big">{_big_time(tonight["wake"])}</div></div></div>'
        f'<p class="sl-why">{why}</p>'
        f'<div class="sl-cut">'
        f'<div><span class="msr">coffee</span><b>{clock(tonight["caffeine_cutoff"])}</b><small>Last coffee</small></div>'
        f'<div><span class="msr">bolt</span><b>{clock(tonight["energy_drink_cutoff"])}</b><small>Energy drinks</small></div>'
        f'<div><span class="msr">fitness_center</span><b>{clock(tonight["hard_workout_end"])}</b><small title="Hard workouts done by">Hard workouts</small></div>'
        f'</div>{badges(chips)}'
        f'<p class="rule-line">{rule_chip("S3")} At least 7 hours of sleep {rule_chip("S4")} Coffee 9 h, energy '
        f'drinks 13 h, and hard workouts 2 h before bed</p></div>')

    # The week ahead: each night is a bar from bedtime to wake-up on a 8 PM to 1 PM scale.
    span = 17 * 60  # minutes from 8 PM to 1 PM

    def place(moment, night):
        start = datetime.combine(night, datetime.min.time(), moment.tzinfo).replace(hour=20)
        return max(0.0, min(100.0, 100 * (moment - start).total_seconds() / 60 / span))

    axis = "".join(f'<span style="left:{100 * (hour - 20) * 60 / span:.2f}%">{(hour - 1) % 12 + 1} {"PM" if hour % 24 >= 12 else "AM"}</span>'
                   for hour in (21, 24, 27, 30, 33, 36))
    rows = []
    for k, night in enumerate(nights):
        day = night["night"]
        name = "Tonight" if k == 0 else f"{day:%a}"
        left, right = place(night["bed"], day), place(night["wake"], day)
        times = f'{clock(night["bed"])} → {clock(night["wake"])}'
        marker = ""
        if night["first"]:
            title = f'{night["first"]["title"]} · {clock(night["first"]["start"])}'
            marker = (f'<i class="ev" style="left:{place(night["first"]["start"], day):.2f}%" '
                      f'title="{html.escape(title)}"></i>')
        rows.append(
            f'<div class="sl-row{" is-tonight" if k == 0 else ""}"><div class="d">{name}</div>'
            f'<div class="track"><div class="bar" style="left:{left:.2f}%;width:{right - left:.2f}%" '
            f'title="{name}: {times}"></div>{marker}</div><div class="tm">{times}</div></div>')
    week = (f'<div class="sl-week"><div class="sl-row sl-head"><div class="d"></div><div class="track sl-axis">{axis}</div>'
            f'<div class="tm"></div></div>{"".join(rows)}'
            f'<div class="legend"><span><i class="swatch" style="background:var(--s1)"></i>Time in bed</span>'
            f'<span><i class="ev-key"></i>First thing the next morning</span></div></div>')
    return f'<div class="sleep">{left_side}{week}</div>'


# ---------- Rule IDs, like [R1, L1] ----------

def rule_chip(rule):
    """A small chip for one rule ID. Hovering it shows the rule and where it comes from."""
    if rule not in facts.RULES:
        return html.escape(rule)
    name, text, source = facts.RULES[rule]
    return f'<span class="rule" title="{html.escape(f"{rule} · {name}: {text} ({source})")}">{rule}</span>'


facts_cited = re.compile(r"\s?\[([A-Z]\d(?:\s*,\s*[A-Z]\d)*)\]")


def coach_text(text):
    """The coach's reply as safe Markdown (for st.markdown with unsafe_allow_html). The rule IDs it
    cites, like [L1], are left out of the text; rules_used() lists them underneath instead."""
    return plain(html.escape(facts_cited.sub("", text), quote=False))


def rules_used(text):
    """'Rules this plan follows': each rule the text cites, with its sentence and source."""
    cited = []
    for group in facts_cited.findall(text or ""):
        for rule in group.split(","):
            if rule.strip() in facts.RULES and rule.strip() not in cited:
                cited.append(rule.strip())
    if not cited:
        return ""
    rows = "".join(f'<div class="ru">{rule_chip(rule)}<div><b>{html.escape(facts.RULES[rule][0])}.</b> '
                   f'{html.escape(facts.RULES[rule][1])} <span class="src">{html.escape(facts.RULES[rule][2])}</span></div></div>'
                   for rule in cited)
    return (f'<details class="rules-used"><summary><span class="msr">menu_book</span>Research rules this follows'
            f'{"".join(rule_chip(rule) for rule in cited)}<span class="msr chev">expand_more</span></summary>{rows}</details>')


def limits_strip(caps):
    """Today's limits as code set them, so it's clear the AI works inside them."""
    level = caps["max_intensity"]
    rules = sorted({b["rule"] for b in caps["because"] if b["cap"] == level}) if level != "hard" else []
    parts = [{"rest": "Rest or easy walks only", "easy": "Easy only", "moderate": "Moderate at most",
              "hard": "Hard sessions OK"}[level] + "".join(rule_chip(r) for r in rules)]
    if caps["afternoon_max"]:
        parts.append(f"Easy after 10 AM{rule_chip('R4')}")
    if not caps["running_allowed"]:
        parts.append(f"No running today{rule_chip('B2')}")
    elif caps["longest_run_miles"] and level != "rest":
        parts.append(f"Runs up to {caps['longest_run_miles']:g} mi{rule_chip('L1')}")
    return (f'<div class="limits"><span class="msr">shield</span><span><b>Today\'s limits, set by code:</b> '
            f'{" · ".join(parts)}</span></div>')


# ---------- Body check: the check engine light (rule R1) ----------

def _gauge(signal):
    if signal["learning"]:
        return (f'<div class="gauge"><div class="g-name">{html.escape(signal["name"])}</div>'
                f'<div class="g-val">–</div><div class="g-track"></div>'
                f'<div class="g-note">Still learning ({min(signal["nights"], 14)} of 14 nights)</div></div>')
    half = max((signal["high"] - signal["low"]) / 2, 1e-6) * 2.2
    spot = lambda value: max(3.0, min(97.0, 50 + (value - signal["normal"]) / half * 50))
    left, right = spot(signal["low"]), spot(signal["high"])
    unit = signal["unit"]
    note = f'{signal["change"]} · outside normal' if signal["off"] else f'Normal {signal["low"]:g}–{signal["high"]:g}'
    return (f'<div class="gauge{" off" if signal["off"] else ""}"><div class="g-name">{html.escape(signal["name"])}</div>'
            f'<div class="g-val">{signal["value"]:g}<small>{unit}</small></div>'
            f'<div class="g-track" title="Your normal: {signal["low"]:g} to {signal["high"]:g} {unit}">'
            f'<i class="g-band" style="left:{left:.1f}%;width:{right - left:.1f}%"></i>'
            f'<i class="g-dot" style="left:{spot(signal["value"]):.1f}%"></i></div>'
            f'<div class="g-note">{html.escape(note)}</div></div>')


def week_trends(hrv, resting):
    """Rule R2: this week's HRV and resting heart rate averages against the person's normal range."""
    items = []
    for name, unit, found in [("7-day HRV", "ms", hrv), ("7-day resting HR", "bpm", resting)]:
        if not found:
            items.append(f'<div><span class="k">{name}</span><b>Still learning</b><span class="n">needs 3 weeks</span></div>')
            continue
        low, high = found["your_normal_range"]
        good = found["meaning"] != "less recovered than usual: keep training easy"
        items.append(f'<div><span class="k">{name}</span><b>{found["last_7_day_average"]:g} {unit}</b>'
                     f'<span class="n {"" if good else "bad"}">{found["place"]} your normal {low:g}–{high:g}</span></div>')
    return f'<div class="trends7">{"".join(items)}{rule_chip("R2")}</div>'


def body_check(engine, hrv=None, resting=None):
    """The check engine light: a status, one plain sentence, and four little gauges."""
    if not engine:
        return '<p class="empty-note">No WHOOP mornings yet.</p>'
    status = {"ok": ("check_circle", "All clear" if not engine["off"] else "1 signal off, likely noise", "good"),
              "easy": ("warning", "Easy day", "warn"), "rest": ("emergency_home", "Rest day", "bad"),
              None: ("hourglass_top", "Still learning your normal", "flat")}[engine["alert"]]
    signals = "".join(_gauge(s) for s in engine["signals"])
    return (f'<div class="bc-top"><span class="status {status[2]}"><span class="msr">{status[0]}</span>{status[1]}</span>'
            f'<span class="bc-count">{engine["off"]} of {engine["of"]} signals outside your normal</span></div>'
            f'<p class="bc-msg">{html.escape(engine["message"])}</p><div class="gauges">{signals}</div>'
            f'<p class="rule-line">{rule_chip("R1")} Signals can also rise after alcohol, a late meal, a hot room, travel, '
            f'or during your cycle, so it takes 2 together. It never diagnoses anything.</p>'
            + week_trends(hrv, resting))


# ---------- The morning check-in (rules R3 and B2) ----------

def checkin_summary(checkin, pain):
    """What they said this morning, and the pain traffic light."""
    items = " · ".join(f'{name.replace(" quality", "")} <b>{checkin[key]}</b>/5' for key, name in facts.QUESTIONS)
    feel = "lower than usual for you" if checkin["low"] else "a good morning" if checkin["good"] else "about usual"
    light = pain["light"] if pain else "green"
    title = (f'Pain: {html.escape(pain["where"] or "yes")} {pain["pain"]}/10' if pain and pain["pain"] else "No pain")
    lights = "".join(f'<i class="{color}{" on" if color == light else ""}"></i>' for color in ("green", "yellow", "red"))
    return (f'<div class="ck-items">{items}</div>'
            f'<div class="ck-score">{checkin["score"]} of 20 · {feel}{rule_chip("R3")}</div>'
            f'<div class="light {light}"><div class="lamps">{lights}</div><div><b>{title}</b>'
            f'<p>{html.escape(pain["message"] if pain else "")}{rule_chip("B2") if pain and pain["pain"] else ""}</p></div></div>')


# ---------- Sleep regularity and the sleep barcode (rule S1) ----------

def regularity_card(score, before, nights):
    """The Sleep Regularity Index, and a 'barcode' of the last 14 nights (one row each)."""
    if score is None:
        left = ('<div class="sri"><div class="k"><span class="msr">align_justify_space_even</span>Sleep regularity</div>'
                '<div class="big">–</div><p>Needs about a week of nights in a row.</p></div>')
    else:
        change = ""
        if before is not None and abs(score - before) < 3:
            change = f'<span class="delta flat">about the same</span><span class="vs">as the 2 weeks before ({before})</span>'
        elif before is not None:
            better = score > before
            change = (f'<span class="delta {"good" if better else "bad"}"><span class="msr">'
                      f'{"arrow_upward" if better else "arrow_downward"}</span>{abs(score - before)}</span>'
                      f'<span class="vs">vs. the 2 weeks before ({before})</span>')
        left = (f'<div class="sri"><div class="k"><span class="msr">align_justify_space_even</span>Sleep regularity · '
                f'last 14 days</div><div class="big">{score}<small>/100</small></div>{change}'
                f'<p>The chance you\'re asleep or awake at the same minute as the day before. 100 is the same every '
                f'day. Students with steadier sleep timing tend to earn better grades{rule_chip("S1")}</p></div>')
    known = [(a, b) for _, a, b in nights if a is not None]
    if not known:
        return f'<div class="regular">{left}</div>'

    def hours(moment, morning):  # hours after noon of the day before the morning
        noon = datetime.combine(morning - timedelta(days=1), datetime.min.time(), moment.tzinfo).replace(hour=12)
        return (moment - noon).total_seconds() / 3600
    spans = [(morning, hours(a, morning), hours(b, morning)) for morning, a, b in nights if a is not None]
    low = min(8, math.floor(min(s for _, s, _ in spans)))       # 8 = 8 PM
    high = max(24, math.ceil(max(e for _, _, e in spans)))      # 24 = noon
    place = lambda h: 100 * (h - low) / (high - low)
    axis = "".join(f'<span style="left:{place(h):.1f}%">{(h + 11) % 12 + 1} {"PM" if (h + 12) % 24 >= 12 else "AM"}</span>'
                   for h in range(low + (low % 2), high + 1, 4 if high - low > 14 else 2))
    rows = []
    for morning, asleep, awake in nights:
        bar = ""
        if asleep is not None:
            a, b = hours(asleep, morning), hours(awake, morning)
            bar = (f'<i style="left:{place(a):.1f}%;width:{place(b) - place(a):.1f}%" '
                   f'title="{morning - timedelta(days=1):%a} night: {clock(asleep)} to {clock(awake)}"></i>')
        rows.append(f'<div class="bc-row"><div class="d">{morning:%a} {morning.day}</div>'
                    f'<div class="bc-track">{bar}</div></div>')
    return (f'<div class="regular">{left}<div class="barcode"><div class="bc-row bc-head"><div class="d"></div>'
            f'<div class="bc-track bc-axis">{axis}</div></div>{"".join(rows)}'
            f'<div class="legend"><span><i class="swatch" style="background:var(--s1)"></i>Asleep (one row per night, '
            f'by the morning you woke up)</span></div></div></div>')


# ---------- What moves your recovery (rules P1 to P3) ----------

def points(value):
    """+11, 0, or −4 (a real minus sign)."""
    whole = int(round(value))
    return "0" if whole == 0 else f"+{whole}" if whole > 0 else f"−{abs(whole)}"


def patterns_card(rows):
    """One dot-and-whisker row per question: the likely effect on next-morning recovery and its range."""
    scale = 20  # recovery points from the middle to each edge
    place = lambda v: max(0.0, min(100.0, 50 + v / scale * 50))
    styles = {"Consistent pattern": "consistent", "Possible pattern": "possible", "No clear difference yet": "none"}
    head = ('<div class="pat-row pat-head"><div></div><div class="pat-plot pat-axis">'
            + "".join(f'<span style="left:{place(v):.0f}%">{points(v)}</span>' for v in (-20, -10, 0, 10, 20))
            + '</div><div></div></div>')
    out, notes = [], []
    for row in rows:
        question = (f'<div class="pat-q"><b>{html.escape(row["question"])}</b>'
                    f'<span>{html.escape(row["versus"])}</span></div>')
        if row["effect"] is None:
            out.append(f'<div class="pat-row grey">{question}<div class="pat-plot"><i class="zero"></i></div>'
                       f'<div class="pat-res"><span class="pl">Not enough data yet</span>'
                       f'<span>{html.escape(row.get("detail") or "")}</span></div></div>')
            continue
        style = styles[row["label"]]
        out.append(
            f'<div class="pat-row">{question}<div class="pat-plot" title="{html.escape(row["sentence"])}"><i class="zero"></i>'
            f'<i class="whisker {style}" style="left:{place(row["low"]):.1f}%;width:{place(row["high"]) - place(row["low"]):.1f}%"></i>'
            f'<i class="pdot {style}" style="left:{place(row["effect"]):.1f}%"></i></div>'
            f'<div class="pat-res"><span class="pl {style}">{row["label"]}</span><b>{points(row["effect"])} points</b>'
            f'<span>likely {points(row["low"])} to {points(row["high"])} · {html.escape(row["detail"])}</span></div></div>')
        if row["label"] != "No clear difference yet":
            notes.append(html.escape(row["sentence"]) + (f' <i>{html.escape(row["but_also"])}</i>' if row.get("but_also") else ""))
    foot = "".join(f"<p>{note}</p>" for note in notes[:2])
    return (f'<div class="pats">{head}{"".join(out)}</div><div class="pat-foot">{foot}'
            f'<p class="rule-line">{rule_chip("P3")} Points are next-morning recovery compared with your own '
            f'28-day trend. The bar is the likely range; a pattern counts as consistent only if it shows up in both '
            f'halves of your data. Linked to, not proven to cause, and a pattern can only make training easier.</p></div>')


# ---------- Showing what the agent did ----------

def claude_problem(error):
    """A failed request to Claude, in plain English: (short label, one-sentence explanation)."""
    if isinstance(error, anthropic.AuthenticationError):
        return "Claude didn't accept the API key", "The `anthropic_api_key` in secrets.toml isn't valid."
    if isinstance(error, anthropic.BadRequestError):
        if "credit" in error.message.lower():
            return "Claude couldn't run", "The Claude account is out of credits. Add more at console.anthropic.com."
        return "Claude couldn't run", f"Claude said the request was invalid: {error.message}"
    if isinstance(error, anthropic.RateLimitError):
        return "Claude is busy", "Too many requests to Claude right now. Wait a minute and try again."
    if isinstance(error, anthropic.APIConnectionError):
        return "Couldn't reach Claude", "Couldn't connect to Claude. Check the internet connection and try again."
    status = getattr(error, "status_code", "?")
    return "Claude had a problem", f"Claude returned an error ({status}). Try again in a minute."


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
        if tool == "update_training_plan" and output.get("turned_down"):
            return f"wrote {output['saved']} days; turned down {len(output['turned_down'])}: {output['turned_down'][0]}"
        if tool == "update_training_plan":
            return f"wrote {output['saved']} days of the plan"
        if tool == "save_daily_log":
            return f"saved today's decision: {tool_input['workout']}"
        if tool == "get_calendar_events":
            today = output["days"][0]
            return f"{len(today['events'])} events today, {len(today['free_windows'])} free windows"
        if tool == "create_calendar_event":
            return f"suggested {tool_input['start_time']} for {tool_input['minutes']} min, waiting for your OK"
        if tool == "get_personal_patterns":
            hrv = output["hrv"]
            found = sum(1 for p in output["what_moves_their_recovery"] if p["label"] != "Not enough data yet")
            patterns = f"{found} of 5 recovery questions answered"
            return f"HRV {hrv['status']}, {patterns}" if isinstance(hrv, dict) else patterns
        if tool == "get_sleep_plan":
            night = output["nights"][0]
            return f"tonight: in bed by {night['bedtime']}, up at {night['wake_up']}"
        if tool == "remember_about_them":
            return f"remembered: {tool_input['note']}"
        if tool == "set_goal":
            return f"saved your goal: {tool_input['goal']}"
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
        elif kind == "facts":
            st.markdown(f":material/fact_check: {plain(step['text'])}")
            if raw:
                st.json({"The facts packet Claude started with": step["packet"]}, expanded=1)
        elif kind == "check":
            icon = "verified" if step["passed"] else "rule"
            st.markdown(f":material/{icon}: {plain(step['text'])}")
        elif kind == "thinking":
            st.caption(f":material/psychology: {plain(step['text'])}")
        elif kind == "note":
            st.write(plain(step["text"]))
        elif kind == "tool_call":
            asked[step["tool"]] = step["input"]  # shown together with its result, below
        elif kind == "tool_result":
            tool_input = step.get("input", asked.get(step["tool"])) or {}
            ahead = step["tool"] == "get_calendar_events"
            days = f" ({'next' if ahead else 'last'} {tool_input['days']} days)" if "days" in tool_input else ""
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
        elif kind == "question":
            pass  # a chat message: the page already shows it
        else:  # "stopped"
            st.warning(plain(step["text"]), icon=":material/warning:")


def show_step(step):
    """Draw one step live, while the agent is running."""
    show_steps([step])
