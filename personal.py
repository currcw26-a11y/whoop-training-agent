# personal.py: what the coach knows about one person, worked out from their own data.
#
# The coach compares each person with THEIR OWN normal, not with everyone else's,
# because that's how the training research does it (see RESEARCH.md):
#   - HRV and resting heart rate: this week's average vs. the person's normal range (rule R2)
#   - sleep: their usual bedtime, wake time, sleep need, and how regular their sleep is (S1)
#   - training: how much they've done lately vs. the weeks before, counted from heart-rate zones
#   - patterns: "what moves your recovery" (patterns.py)

import math
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd


def as_frames(day_rows, workout_rows):
    """Turn database rows into tables (DataFrames) with real dates, oldest first."""
    days = pd.DataFrame(day_rows)
    if not days.empty:
        days["date"] = pd.to_datetime(days["date"])
        days = days.sort_values("date")
    workouts = pd.DataFrame(workout_rows)
    if not workouts.empty:
        workouts["date"] = pd.to_datetime(workouts["date"])
    return days, workouts


def _clock(minutes):
    """Minutes after midnight (can be more than a day) -> '23:40'."""
    minutes = int(round(minutes)) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# ---------- HRV and resting heart rate ----------

def trend(days, column, higher_is_better=True, log_scale=False):
    """This week's average compared with the person's normal range (rule R2).

    Normal range = the 60 days before this week: average ± half a standard deviation.
    (That's the "smallest worthwhile change" HRV-guided training studies use. A 7-day average
    is used because one morning on its own jumps around a lot.) HRV is averaged on a log scale,
    like those studies, because it swings more on high days than low ones. Needs 3+ readings
    this week and 21+ before, or it says it's still learning."""
    if days.empty or column not in days:
        return None
    values = days.dropna(subset=[column]).sort_values("date")
    if values.empty:
        return None
    last = values["date"].max()
    week = values[values["date"] > last - pd.Timedelta(days=7)][column]
    before = values[(values["date"] <= last - pd.Timedelta(days=7)) & (values["date"] > last - pd.Timedelta(days=67))][column]
    if len(week) < 3 or len(before) < 21:
        return None
    if log_scale:
        week, before = np.log(week), np.log(before)
    middle, spread = before.mean(), 0.5 * before.std()
    average = week.mean()
    place = "below" if average < middle - spread else "above" if average > middle + spread else "within"
    better = place == "within" or (place == "above") == higher_is_better
    back = math.exp if log_scale else (lambda x: x)  # show HRV in ms again
    return {
        "last_7_day_average": round(float(back(average)), 1),
        "your_normal_range": [round(float(back(middle - spread)), 1), round(float(back(middle + spread)), 1)],
        "status": f"{place} your normal range",
        "place": place,
        "meaning": "normal for you" if place == "within"
        else "a good sign: well recovered" if better else "less recovered than usual: keep training easy",
    }


# ---------- Sleep habits ----------

def sleep_habits(days, time_zone):
    """The person's usual sleep over the last 2 weeks, or None without enough nights.
    Keys starting with _ are for code (minutes after midnight); the rest are for people."""
    if days.empty or "sleep_start" not in days:
        return None
    nights = days.dropna(subset=["sleep_start", "sleep_end"]).sort_values("date").tail(14)
    if len(nights) < 3:
        return None

    def minutes(moment, evening):
        local = datetime.fromisoformat(moment).astimezone(time_zone)
        value = local.hour * 60 + local.minute
        return value + 24 * 60 if evening and value < 12 * 60 else value  # 1am bedtime = 25:00

    bed = pd.Series([minutes(start, True) for start in nights["sleep_start"]])
    wake = pd.Series([minutes(end, False) for end in nights["sleep_end"]])
    need = nights["sleep_need_hours"].dropna()
    efficiency = nights["sleep_efficiency"].dropna()
    return {
        "usual_bedtime": _clock(bed.median()),
        "usual_wake_time": _clock(wake.median()),
        "bedtime_varies_by_minutes": int(round(bed.std())) if len(bed) > 2 else None,
        "average_sleep_last_7_nights": round(float(nights.tail(7)["sleep_hours"].mean()), 1),
        "usual_sleep_need_hours": round(float(need.median()), 1) if not need.empty else None,
        "sleep_efficiency_percent": int(round(efficiency.mean())) if not efficiency.empty else None,
        "_bed": float(bed.median()),
        "_wake": float(wake.median()),
    }


def sleep_nights(days, time_zone, last_morning, count=14):
    """The last `count` nights, oldest first: (wake-up date, fell asleep, woke up) in local time,
    or None for a night with no data."""
    by_date = {}
    if not days.empty and "sleep_start" in days:
        for _, row in days.dropna(subset=["sleep_start", "sleep_end"]).iterrows():
            by_date[row["date"].date()] = (datetime.fromisoformat(row["sleep_start"]).astimezone(time_zone),
                                           datetime.fromisoformat(row["sleep_end"]).astimezone(time_zone))
    mornings = [last_morning - timedelta(days=k) for k in range(count - 1, -1, -1)]
    return [(morning, *by_date[morning]) if morning in by_date else (morning, None, None) for morning in mornings]


def sleep_regularity(nights):
    """Sleep Regularity Index (rule S1): the chance you're in the same state, asleep or awake, at any
    minute as 24 hours later, scaled from -100 (opposite every day) to 100 (exactly the same).
    Each "day" runs noon to noon, so a whole night sits in one day. Only the main sleep counts
    (WHOOP doesn't send naps here). Needs 6+ pairs of back-to-back nights with data, or it returns None."""
    grid, has_data = [], []
    for morning, asleep, awake in nights:
        day = np.zeros(1440, dtype=bool)
        if asleep is not None:
            noon = datetime.combine(morning - timedelta(days=1), time(12), asleep.tzinfo)
            first = max(0, int((asleep - noon).total_seconds() // 60))
            last = min(1440, int((awake - noon).total_seconds() // 60))
            day[first:last] = True
        grid.append(day)
        has_data.append(asleep is not None)
    grid, has_data = np.array(grid), np.array(has_data)
    pairs = has_data[:-1] & has_data[1:]
    if pairs.sum() < 6:
        return None
    same = (grid[:-1] == grid[1:])[pairs]
    return int(round(-100 + 200 * same.mean()))


# ---------- Training load ----------

def trimp(zone_minutes):
    """Training load points for one workout (Edwards' method): minutes in heart-rate zones 1-5
    times 1-5, so an hour easy counts less than an hour hard. Zone 0 (resting) doesn't count.
    WHOOP's Strain is on a curved scale, so adding Strain up would be misleading; this adds up fairly."""
    if not isinstance(zone_minutes, list) or len(zone_minutes) < 6:
        return None
    return round(sum(zone_minutes[i] * i for i in range(1, 6)), 1)


def hard_days(workouts, days=None):
    """Dates with a hard training day: 10+ minutes in zones 4-5, or a training load in the person's
    top quarter. Any sport counts (club tennis too). Without zone data, a WHOOP day strain of 14+."""
    found = set()
    if not workouts.empty and "zone_minutes" in workouts:
        loads = workouts["zone_minutes"].map(trimp).astype(float)
        top_quarter = loads.dropna().quantile(0.75) if loads.notna().sum() >= 8 else None
        for (_, workout), load in zip(workouts.iterrows(), loads):
            zones = workout["zone_minutes"]
            if pd.isna(load):
                continue
            if zones[4] + zones[5] >= 10 or (top_quarter is not None and load >= top_quarter):
                found.add(workout["date"].date())
    if not found and days is not None and not days.empty:
        found = {d.date() for d in days.loc[days["day_strain"] >= 14, "date"]}
    return found


def training_load(workouts, today):
    """How much they've trained lately compared with the weeks before.
    A big jump is the main thing that gets newer runners hurt."""
    if workouts.empty:
        return {"note": "No WHOOP workouts saved yet."}
    dates = workouts["date"].dt.date
    runs = workouts[workouts["sport"].str.contains("run", case=False, na=False)]
    run_dates = runs["date"].dt.date
    loads = workouts["zone_minutes"].map(trimp).astype(float) if "zone_minutes" in workouts else pd.Series(dtype=float)

    def miles(first, last):
        return round(float(runs[(run_dates >= first) & (run_dates <= last)]["distance_miles"].fillna(0).sum()), 1)

    def minutes(first, last):
        return int(workouts[(dates >= first) & (dates <= last)]["minutes"].fillna(0).sum())

    def points(first, last):
        return int(round(loads[(dates >= first) & (dates <= last)].fillna(0).sum()))

    monday = today - timedelta(days=today.weekday())
    week_ago = today - timedelta(days=6)
    month_before = [(week_ago - timedelta(days=7 * (k + 1)), week_ago - timedelta(days=7 * k + 1)) for k in range(4)]
    easy = hard = 0.0
    for zones in workouts[dates >= today - timedelta(days=27)].get("zone_minutes", []):
        if isinstance(zones, list) and len(zones) >= 6:
            easy += zones[1] + zones[2]
            hard += zones[3] + zones[4] + zones[5]
    last_7, before_7 = miles(week_ago, today), miles(week_ago - timedelta(days=7), week_ago - timedelta(days=1))
    usual_points = sum(points(a, b) for a, b in month_before) / 4
    return {
        "running_miles_this_week_so_far": miles(monday, today),
        "running_miles_last_7_days": last_7,
        "running_miles_7_days_before_that": before_7,
        "change_vs_week_before_percent": round(100 * (last_7 - before_7) / before_7) if before_7 else None,
        "average_weekly_running_miles_4_weeks_before": round(sum(miles(a, b) for a, b in month_before) / 4, 1),
        "workout_minutes_last_7_days": minutes(week_ago, today),
        "average_weekly_workout_minutes_4_weeks_before": round(sum(minutes(a, b) for a, b in month_before) / 4),
        # Heart-rate minutes weighted by effort (zone 1 = 1 point a minute ... zone 5 = 5), every sport.
        "training_load_points_last_7_days": points(week_ago, today) if loads.notna().any() else None,
        "average_weekly_training_load_points_4_weeks_before": round(usual_points) if loads.notna().any() else None,
        "share_of_workout_time_easy_percent": round(100 * easy / (easy + hard)) if easy + hard >= 30 else None,
        "sports_last_4_weeks": sorted(set(workouts[dates >= today - timedelta(days=27)]["sport"].dropna())),
    }


# ---------- Everything together, for the coach ----------

def coach_summary(days, workouts, time_zone, today, recovery_patterns=()):
    habits = sleep_habits(days, time_zone)
    nights = sleep_nights(days, time_zone, today)
    return {
        "hrv": trend(days, "hrv_ms", log_scale=True) or "Still learning their normal (needs 3 weeks of data).",
        "resting_heart_rate": trend(days, "resting_hr", higher_is_better=False)
        or "Still learning their normal (needs 3 weeks of data).",
        "sleep": {**{k: v for k, v in habits.items() if not k.startswith("_")},
                  "sleep_regularity_score_last_14_days": sleep_regularity(nights)} if habits else "Needs 3 nights of data.",
        "training_load": training_load(workouts, today),
        # Five fixed questions, each with a likely range and a label for how sure it is (patterns.py).
        "what_moves_their_recovery": [{k: v for k, v in row.items() if not k.startswith("_")} for row in recovery_patterns],
    }
