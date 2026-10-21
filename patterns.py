# patterns.py: "What moves your recovery". Five questions, asked of one person's own data.
#
# Patterns in one person's data are easy to get wrong: with a few weeks of days, some differences
# look big by pure chance. So, following the n-of-1 research (RESEARCH.md, rules P1-P3):
#   - only five questions, picked in advance (no fishing through dozens of numbers)
#   - recovery is compared with the person's own slow trend (a 28-day median), so getting fitter
#     over the semester doesn't look like an effect
#   - yesterday's workout or exam goes with this morning's recovery; last night's sleep goes
#     with this morning's recovery (they're on the same WHOOP row)
#   - each answer has a likely range: the 10th to 90th percentile from a "block bootstrap",
#     which resamples whole weeks so back-to-back days stay together
#   - small samples are pulled toward "no effect" (shrinkage worth about 10 days of data)
#   - "consistent" means it showed up in both the first and second half of the data
#   - nothing is shown until there are 8+ days in each group (21+ in all)
#   - a pattern may only make training easier, never harder (rule P3), and it says
#     "linked to", never "caused"

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import personal

MIN_TOTAL, MIN_GROUP = 21, 8   # days needed in all, and in each group (design choices)
BLOCK, BOOTS = 7, 2000         # resample whole weeks, 2,000 times
SHRINK = 10                    # the "no effect" starting point is worth about 10 days of data
ACT_POINTS = 5                 # a consistent pattern has to be 5+ recovery points to change a plan

# key, the condition, what it's compared with, how to count each group, and the plain-English start.
QUESTIONS = [
    ("slept7", "7+ hours of sleep", "vs. under 7 hours", ("nights with 7+ hours", "nights under 7 hours"),
     "After nights with 7+ hours of sleep", "than after shorter nights"),
    ("early_bed", "Asleep before midnight", "vs. after midnight", ("nights before midnight", "nights after midnight"),
     "After nights you fell asleep before midnight", "than after later nights"),
    ("hard_yday", "The day after a hard workout", "vs. other days", ("hard days", "easier days"),
     "The morning after a hard workout", "than on other days"),
    ("sport_yday", "The day after {sport}", "vs. other days", ("{sport} days", "days without {sport}"),
     "The morning after {sport}", "than on other days"),
    ("exam_yday", "The day after an exam", "vs. other days", ("exam days", "days without an exam"),
     "The morning after an exam", "than on other days"),
]


def favorite_sport(workouts):
    """The person's most common workout that isn't running (like 'tennis'), or None."""
    if workouts.empty:
        return None
    other = workouts[~workouts["sport"].str.contains("run", case=False, na=True)]["sport"]
    return other.value_counts().index[0].lower() if not other.empty else None


def table(days, workouts, exam_days, time_zone):
    """One row per calendar day, oldest first: recovery compared with its own trend, and the five
    conditions as 1 (yes) / 0 (no) / NaN (unknown)."""
    daily = days.set_index("date").sort_index()
    every_day = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(every_day)
    recovery = daily["recovery_score"].astype(float)
    out = pd.DataFrame(index=every_day)
    out["vs_trend"] = recovery - recovery.rolling("28D", min_periods=14).median()
    out["sleep_hours"] = daily["sleep_hours"].astype(float)
    out["slept7"] = (out["sleep_hours"] >= 7).astype(float).where(out["sleep_hours"].notna())

    def before_midnight(start):
        if not isinstance(start, str):
            return np.nan
        return float(datetime.fromisoformat(start).astimezone(time_zone).hour >= 12)  # noon to midnight
    out["early_bed"] = daily["sleep_start"].map(before_midnight) if "sleep_start" in daily else np.nan
    yesterday = [(day - timedelta(days=1)).date() for day in every_day]
    hard = personal.hard_days(workouts, days)
    out["hard_yday"] = [float(day in hard) for day in yesterday]
    sport = favorite_sport(workouts)
    played = set(workouts.loc[workouts["sport"].str.lower() == sport, "date"].dt.date) if sport else set()
    out["sport_yday"] = [float(day in played) for day in yesterday] if sport else np.nan
    out["exam_yday"] = [float(day in exam_days) for day in yesterday] if exam_days is not None else np.nan
    return out, sport


def _with_range(yes, values, seed):
    """The difference between the two groups, and its likely range from a moving-block bootstrap."""
    n = len(values)
    effect = values[yes].mean() - values[~yes].mean()
    rng = np.random.default_rng(seed)
    starts = rng.choice(np.arange(n - BLOCK + 1), size=(BOOTS, n // BLOCK + 1))
    picks = (starts[:, :, None] + np.arange(BLOCK)).reshape(BOOTS, -1)[:, :n]
    groups, resampled = yes[picks], values[picks]
    count_yes = groups.sum(axis=1)
    count_no = n - count_yes
    usable = (count_yes > 0) & (count_no > 0)
    differences = ((resampled * groups).sum(axis=1)[usable] / count_yes[usable]
                   - (resampled * ~groups).sum(axis=1)[usable] / count_no[usable])
    low, high = np.percentile(differences, [10, 90])
    return float(effect), float(low), float(high)


def _pts(value):
    """+9, 0, or −4 (a real minus sign)."""
    whole = int(round(value))
    return "0" if whole == 0 else f"+{whole}" if whole > 0 else f"−{abs(whole)}"


def _half(yes, values):
    return None if yes.all() or not yes.any() else float(values[yes].mean() - values[~yes].mean())


def find(days, workouts, exam_days, time_zone):
    """The five rows for the "What moves your recovery" card (and for the coach).
    exam_days: a set of dates with an exam, or None if the calendar isn't connected."""
    if days.empty or days["recovery_score"].notna().sum() < 5:
        data, sport = None, favorite_sport(workouts)
    else:
        data, sport = table(days, workouts, exam_days, time_zone)
    rows = []
    for seed, (key, condition, versus, units, starts_with, compared) in enumerate(QUESTIONS):
        fill = (lambda text: text.format(sport=sport)) if sport else (lambda text: text.format(sport="another sport"))
        row = {"key": key, "question": fill(condition), "versus": versus, "label": "Not enough data yet",
               "effect": None, "low": None, "high": None}
        rows.append(row)
        if key == "sport_yday" and not sport:
            row["detail"] = "No sport besides running recorded yet"
            continue
        if key == "exam_yday" and exam_days is None:
            row["detail"] = "Connect Google Calendar to check this"
            continue
        if data is None:
            row["detail"] = "Needs 3 weeks of recovery scores"
            continue
        part = data[[key, "vs_trend", "sleep_hours"]].dropna(subset=[key, "vs_trend"])
        yes = part[key].to_numpy() == 1.0
        values = part["vs_trend"].to_numpy()
        n_yes, n_no = int(yes.sum()), int((~yes).sum())
        row.update(n_yes=n_yes, n_no=n_no)
        if len(part) < MIN_TOTAL or n_yes < MIN_GROUP or n_no < MIN_GROUP:
            short, unit = (n_yes, units[0]) if n_yes <= n_no else (n_no, units[1])
            row["detail"] = f"{min(short, MIN_GROUP)} of {MIN_GROUP} {fill(unit)}" if len(part) >= MIN_TOTAL \
                else f"{len(part)} of {MIN_TOTAL} days"
            continue
        effect, low, high = _with_range(yes, values, seed)
        middle = len(part) // 2
        first, second = _half(yes[:middle], values[:middle]), _half(yes[middle:], values[middle:])
        if low <= 0 <= high:
            label = "No clear difference yet"
        elif first is not None and second is not None and np.sign(first) == np.sign(second) == np.sign(effect):
            label = "Consistent pattern"
        else:
            label = "Possible pattern"
        # Shrinkage: pull small samples toward "no effect" before showing or using them.
        n_eff = 2 / (1 / n_yes + 1 / n_no)
        keep = n_eff / (n_eff + SHRINK)
        shown, low_s, high_s = effect * keep, low * keep, high * keep
        counts = f"{n_yes} vs. {n_no} {'nights' if key in ('slept7', 'early_bed') else 'days'}"
        start = fill(starts_with)
        if label == "No clear difference yet":
            sentence = (f"{start}, your recovery wasn't clearly different {compared} (likely range {_pts(low_s)} to "
                        f"{_pts(high_s)} points; {counts}).")
        else:
            sentence = (f"{start}, your recovery was about {abs(shown):.0f} points {'higher' if shown > 0 else 'lower'} "
                        f"{compared} (likely range {_pts(low_s)} to {_pts(high_s)}; {counts}). "
                        + ("This held in both halves of your data." if label == "Consistent pattern"
                           else "It hasn't shown up in both halves of your data yet."))
        but_also = None
        if key != "slept7":
            gap = part.loc[yes, "sleep_hours"].mean() - part.loc[~yes, "sleep_hours"].mean()
            if abs(gap) >= 0.5:
                but_also = (f"But on those {fill(units[0])} you also slept {abs(gap) * 60:.0f} minutes "
                            f"{'more' if gap > 0 else 'less'}, so sleep may be part of it.")
        # Does today fall in the group that recovers worse?
        latest = data[key].dropna()
        today_value = latest.iloc[-1] if not latest.empty and latest.index[-1] == data.index[-1] else None
        applies = today_value is not None and (today_value == 1.0) == (shown < 0)
        row.update(label=label, effect=round(shown, 1), low=round(low_s, 1), high=round(high_s, 1), detail=counts,
                   sentence=sentence, but_also=but_also,
                   can_make_easier=bool(label == "Consistent pattern" and abs(shown) >= ACT_POINTS),
                   applies_today=bool(applies),
                   action=f"Your recovery is consistently lower {fill(WHEN[key][shown > 0])}, so today stays easy"
                   if applies else None)
    return rows


# For P3: when recovery is lower (condition yes, condition no).
WHEN = {
    "slept7": ("after nights with 7+ hours of sleep", "after short nights, and last night was short"),
    "early_bed": ("after nights before midnight", "after late nights, and last night was late"),
    "hard_yday": ("the day after a hard workout", "on days that don't follow a hard workout"),
    "sport_yday": ("the day after {sport}", "on days that don't follow {sport}"),
    "exam_yday": ("the day after an exam", "on days that don't follow an exam"),
}
