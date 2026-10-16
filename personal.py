# personal.py: what the coach knows about one person, worked out from their own data.
#
# The coach compares each person with THEIR OWN normal, not with everyone else's,
# because that's how the training research does it (see RESEARCH.md):
#   - HRV and resting heart rate: this week's average vs. the person's normal range
#   - sleep: their usual bedtime, wake time, sleep need, and how steady their schedule is
#   - training: how much they've done lately vs. the weeks before
#   - patterns: the same findings as the Insights cards (charts.insights)

from datetime import datetime, timedelta

import pandas as pd

import charts


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

def trend(days, column, higher_is_better=True):
    """This week's average compared with the person's normal range.

    Normal range = the up-to-60 days before this week: average ± half a standard deviation.
    (That's the "smallest worthwhile change" HRV-guided training studies use. A 7-day average
    is used because one morning on its own jumps around a lot.)"""
    if days.empty or column not in days:
        return None
    values = days.dropna(subset=[column]).sort_values("date")
    if len(values) < 21:
        return None
    week, before = values.tail(7)[column], values.iloc[:-7].tail(60)[column]
    middle, spread = before.mean(), 0.5 * before.std()
    average = week.mean()
    place = "below" if average < middle - spread else "above" if average > middle + spread else "within"
    better = place == "within" or (place == "above") == higher_is_better
    return {
        "last_7_day_average": round(average, 1),
        "your_normal_range": [round(middle - spread, 1), round(middle + spread, 1)],
        "status": f"{place} your normal range",
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


# ---------- Training load ----------

def training_load(workouts, today):
    """How much they've trained lately compared with the weeks before.
    A big jump is the main thing that gets newer runners hurt."""
    if workouts.empty:
        return {"note": "No WHOOP workouts saved yet."}
    dates = workouts["date"].dt.date
    runs = workouts[workouts["sport"].str.contains("run", case=False, na=False)]
    run_dates = runs["date"].dt.date

    def miles(first, last):
        return round(float(runs[(run_dates >= first) & (run_dates <= last)]["distance_miles"].fillna(0).sum()), 1)

    def minutes(first, last):
        return int(workouts[(dates >= first) & (dates <= last)]["minutes"].fillna(0).sum())

    monday = today - timedelta(days=today.weekday())
    week_ago = today - timedelta(days=6)
    month_before = [(week_ago - timedelta(days=7 * (k + 1)), week_ago - timedelta(days=7 * k + 1)) for k in range(4)]
    easy = hard = 0.0
    for zones in workouts[dates >= today - timedelta(days=27)].get("zone_minutes", []):
        if isinstance(zones, list) and len(zones) >= 6:
            easy += zones[1] + zones[2]
            hard += zones[3] + zones[4] + zones[5]
    last_7, before_7 = miles(week_ago, today), miles(week_ago - timedelta(days=7), week_ago - timedelta(days=1))
    return {
        "running_miles_this_week_so_far": miles(monday, today),
        "running_miles_last_7_days": last_7,
        "running_miles_7_days_before_that": before_7,
        "change_vs_week_before_percent": round(100 * (last_7 - before_7) / before_7) if before_7 else None,
        "average_weekly_running_miles_4_weeks_before": round(sum(miles(a, b) for a, b in month_before) / 4, 1),
        "workout_minutes_last_7_days": minutes(week_ago, today),
        "average_weekly_workout_minutes_4_weeks_before": round(sum(minutes(a, b) for a, b in month_before) / 4),
        "share_of_workout_time_easy_percent": round(100 * easy / (easy + hard)) if easy + hard >= 30 else None,
        "sports_last_4_weeks": sorted(set(workouts[dates >= today - timedelta(days=27)]["sport"].dropna())),
    }


# ---------- Everything together, for the coach ----------

def coach_summary(days, workouts, time_zone, today):
    habits = sleep_habits(days, time_zone)
    return {
        "hrv": trend(days, "hrv_ms") or "Needs 3 weeks of data.",
        "resting_heart_rate": trend(days, "resting_hr", higher_is_better=False) or "Needs 3 weeks of data.",
        "sleep": {k: v for k, v in habits.items() if not k.startswith("_")} if habits else "Needs 3 nights of data.",
        "training_load": training_load(workouts, today),
        "patterns_in_their_data": [
            {"finding": title, "result": headline, "meaning": text, "based_on": count}
            for _, title, headline, text, count in (charts.insights(days, workouts, time_zone) if not days.empty else [])
        ],
    }
