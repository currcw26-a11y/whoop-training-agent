# charts.py: the dashboard's charts and "insights".
#
# Built with Altair, the charting library that comes with Streamlit.
# The same design rules apply to every chart:
#   - thin marks: bars at most 24px wide with rounded tops, 2px lines
#   - one y-axis per chart (never two different scales on one chart)
#   - colors checked for colorblind safety in light and dark mode,
#     and status colors (green/yellow/red) always come with a label
#   - hover any chart for exact numbers; every number is also in the "All data" tab

from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

# WHOOP's recovery zones. Status colors, always shown with a label.
GREEN, YELLOW, RED = "#0ca30c", "#fab219", "#d03b3b"
ZONES = ["Green (67-100%)", "Yellow (34-66%)", "Red (0-33%)"]

# Heart-rate effort: one blue, light to dark.
EFFORT_LEVELS = ["Easy (zones 1-2)", "Moderate (zone 3)", "Hard (zones 4-5)"]
EFFORT_COLORS = ["#86b6ef", "#2a78d6", "#184f95"]

# Sleep stages in stacking order (bottom to top). Their colors come from the theme in config.toml.
SLEEP_STAGES = {"deep_sleep_hours": "Deep", "rem_sleep_hours": "REM", "light_sleep_hours": "Light", "awake_hours": "Awake"}

MUTED = "#898781"  # a gray that's readable in both light and dark mode
# Legends sit above the chart, with room between items so labels never overlap.
LEGEND = alt.Legend(title=None, orient="top", columnPadding=24, symbolType="circle", labelLimit=0)
DATE = alt.Tooltip("date:T", title="Date", format="%a, %b %-d")


def zone(recovery):
    """WHOOP's zone for a recovery score: (label, emoji, short advice)."""
    if recovery is None or recovery != recovery:  # missing or NaN
        return None, "⚪", "No score yet"
    if recovery >= 67:
        return ZONES[0], "🟢", "Ready to train"
    if recovery >= 34:
        return ZONES[1], "🟡", "Take it a bit easier"
    return ZONES[2], "🔴", "Rest and recover"


def _dark():
    return (st.context.theme or {}).get("type") == "dark"


def _surface():
    """The page background, used for the thin gaps between stacked pieces."""
    return "#1a1a19" if _dark() else "#fcfcfb"


def _bar_width(count):
    """Bars are at most 24px wide, and thinner when there are many, so there's air between them."""
    return max(3, min(24, 560 // max(count, 1)))


def _date_axis():
    return alt.X("date:T", title=None, axis=alt.Axis(format="%b %-d", grid=False, labelOverlap=True))


# ---------- Recovery tab ----------

def recovery_chart(days):
    data = days.dropna(subset=["recovery_score"]).copy()
    data["zone"] = data["recovery_score"].map(lambda score: zone(score)[0])
    return alt.Chart(data).mark_bar(
        size=_bar_width(len(data)), cornerRadiusTopLeft=4, cornerRadiusTopRight=4,
    ).encode(
        x=_date_axis(),
        y=alt.Y("recovery_score:Q", title="Recovery %", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("zone:N", scale=alt.Scale(domain=ZONES, range=[GREEN, YELLOW, RED]),
                        legend=LEGEND),
        tooltip=[DATE, alt.Tooltip("recovery_score:Q", title="Recovery %"), alt.Tooltip("zone:N", title="Zone")],
    ).properties(height=240)


def add_normal_range(days, column):
    """Add columns with the person's normal range for a number: their 30-day
    average, plus or minus one standard deviation (how much it usually varies)."""
    data = days.sort_values("date").copy()
    rolling = data[column].rolling(30, min_periods=5)
    data[f"{column}_low"] = (rolling.mean() - rolling.std()).round(1)
    data[f"{column}_high"] = (rolling.mean() + rolling.std()).round(1)
    return data


def trend_chart(days, column, label, unit):
    """A daily line with a shaded band for the person's normal range.
    `days` must already have the range columns from add_normal_range()."""
    data = days.dropna(subset=[column]).copy()
    data["normal"] = [
        f"{low:.0f}–{high:.0f} {unit}" if pd.notna(low) and pd.notna(high) else "not enough days yet"
        for low, high in zip(data[f"{column}_low"], data[f"{column}_high"])
    ]
    base = alt.Chart(data).encode(x=_date_axis())

    # Make the y-axis fit both the line and the band, with a little room above and below.
    values = pd.concat([data[column], data[f"{column}_low"], data[f"{column}_high"]]).dropna()
    room = max((values.max() - values.min()) * 0.1, 1)
    y_scale = alt.Scale(domain=[float(values.min() - room), float(values.max() + room)], nice=False)

    band = base.mark_area(opacity=0.12).encode(
        y=alt.Y(f"{column}_low:Q", title=f"{label} ({unit})", scale=y_scale),
        y2=f"{column}_high:Q",
    )
    line = base.mark_line(strokeWidth=2, strokeCap="round", strokeJoin="round").encode(
        y=alt.Y(f"{column}:Q", scale=y_scale),
    )

    # Hovering anywhere picks the nearest day: a thin rule, a dot, and a tooltip.
    hover = alt.selection_point(nearest=True, on="pointerover", fields=["date"], empty=False)
    catcher = base.mark_rule(opacity=0).encode(
        tooltip=[DATE, alt.Tooltip(f"{column}:Q", title=f"{label} ({unit})"), alt.Tooltip("normal:N", title="Your normal")],
    ).add_params(hover)
    rule = base.mark_rule(color=MUTED, strokeWidth=1).transform_filter(hover)
    dot = base.mark_point(filled=True, size=80, opacity=1).encode(y=f"{column}:Q").transform_filter(hover)

    # Label only the latest value, at the end of the line.
    last = alt.Chart(data.tail(1)).encode(x="date:T", y=f"{column}:Q")
    end_dot = last.mark_point(filled=True, size=80, opacity=1)
    end_label = last.mark_text(dy=-14, fontWeight="bold").encode(text=alt.Text(f"{column}:Q", format=".0f"))

    return alt.layer(band, line, rule, dot, end_dot, end_label, catcher).properties(height=220)


# ---------- Sleep tab ----------

def sleep_chart(days):
    """Each night's sleep stages stacked into one bar, with a gray tick for how much sleep WHOOP says you needed."""
    long = days.melt(id_vars=["date"], value_vars=list(SLEEP_STAGES), var_name="stage", value_name="hours").dropna()
    long["stage"] = long["stage"].map(SLEEP_STAGES)
    long["order"] = long["stage"].map({name: i for i, name in enumerate(SLEEP_STAGES.values())})
    width = _bar_width(len(days))

    bars = alt.Chart(long).mark_bar(size=width, stroke=_surface(), strokeWidth=2 if width >= 10 else 0.5).encode(
        x=_date_axis(),
        y=alt.Y("sum(hours):Q", title="Hours"),
        color=alt.Color("stage:N", scale=alt.Scale(domain=list(SLEEP_STAGES.values())),
                        legend=LEGEND),
        order=alt.Order("order:Q"),
        tooltip=[DATE, alt.Tooltip("stage:N", title="Stage"), alt.Tooltip("hours:Q", title="Hours", format=".1f")],
    )
    needed = alt.Chart(days.dropna(subset=["sleep_need_hours"])).mark_tick(
        orient="horizontal", color=MUTED, thickness=2, size=width + 10,
    ).encode(
        x="date:T", y="sleep_need_hours:Q",
        tooltip=[DATE, alt.Tooltip("sleep_need_hours:Q", title="Sleep needed (h)", format=".1f")],
    )
    return alt.layer(bars, needed).properties(height=260)


# Axis labels for the bedtime chart. Its numbers are "hours after 6pm", turned into clock times.
_CLOCK = ("((datum.value + 18) % 24) == 0 ? '12am' : ((datum.value + 18) % 24) < 12 ? ((datum.value + 18) % 24) + 'am' : "
          "((datum.value + 18) % 24) == 12 ? '12pm' : ((datum.value + 18) % 24 - 12) + 'pm'")


def _clock_text(moment):
    return f"{moment:%I:%M %p}".lstrip("0")


def bedtime_chart(days, time_zone):
    """A bar from when you fell asleep to when you woke up, for each night."""
    nights = []
    for _, row in days.iterrows():
        if pd.isna(row.get("sleep_start")) or pd.isna(row.get("sleep_end")):
            continue
        asleep = datetime.fromisoformat(row["sleep_start"]).astimezone(time_zone)
        awake = datetime.fromisoformat(row["sleep_end"]).astimezone(time_zone)
        begin = (asleep.hour + asleep.minute / 60 - 18) % 24  # hours after 6pm
        nights.append({
            "date": row["date"], "begin": round(begin, 2),
            "end": round(begin + (awake - asleep).total_seconds() / 3600, 2),
            "asleep": _clock_text(asleep), "awake": _clock_text(awake),
        })
    if not nights:
        return None
    data = pd.DataFrame(nights)
    low, high = int(data["begin"].min()) // 2 * 2, int(data["end"].max()) // 2 * 2 + 2
    return alt.Chart(data).mark_bar(size=_bar_width(len(data)), cornerRadius=4).encode(
        x=_date_axis(),
        y=alt.Y("begin:Q", title=None, scale=alt.Scale(domain=[low, high], reverse=True),
                axis=alt.Axis(labelExpr=_CLOCK, values=list(range(low, high + 1, 2)))),
        y2="end:Q",
        tooltip=[DATE, alt.Tooltip("asleep:N", title="Fell asleep"), alt.Tooltip("awake:N", title="Woke up")],
    ).properties(height=260)


# ---------- Training tab ----------

def _weeks(today, count):
    """The Monday that starts each of the last `count` weeks, oldest first."""
    this_monday = pd.Timestamp(today) - pd.Timedelta(days=pd.Timestamp(today).weekday())
    return [this_monday - pd.Timedelta(weeks=count - 1 - i) for i in range(count)]


def _week_label(monday):
    return f"{monday:%b} {monday.day}"


def weekly_miles_chart(workouts, today, weeks):
    mondays = _weeks(today, weeks)
    totals = {}
    if not workouts.empty:
        runs = workouts[workouts["sport"].str.contains("run", case=False, na=False)]
        for _, run in runs.iterrows():
            monday = run["date"] - pd.Timedelta(days=run["date"].weekday())
            miles = 0 if pd.isna(run["distance_miles"]) else run["distance_miles"]
            totals[monday] = totals.get(monday, 0) + miles
    data = pd.DataFrame({
        "week": [_week_label(m) for m in mondays],
        "miles": [round(totals.get(m, 0), 1) for m in mondays],
    })
    base = alt.Chart(data).encode(
        x=alt.X("week:N", sort=None, title="Week starting", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("miles:Q", title="Running miles"),
    )
    bars = base.mark_bar(size=_bar_width(len(data)), cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        tooltip=[alt.Tooltip("week:N", title="Week starting"), alt.Tooltip("miles:Q", title="Miles")],
    )
    this_week = alt.Chart(data.tail(1)).mark_text(dy=-8, fontWeight="bold").encode(
        x=alt.X("week:N", sort=None), y="miles:Q", text="miles:Q",
    )
    return alt.layer(bars, this_week).properties(height=240)


def effort_chart(workouts, today, weeks):
    """Minutes per week spent easy, moderate, and hard (from heart-rate zones)."""
    mondays = _weeks(today, weeks)
    rows = []
    for _, workout in workouts.iterrows():
        zones = workout.get("zone_minutes")
        if not isinstance(zones, list) or len(zones) < 6:
            continue
        monday = workout["date"] - pd.Timedelta(days=workout["date"].weekday())
        if monday < mondays[0]:
            continue
        for level, minutes in zip(EFFORT_LEVELS, [zones[1] + zones[2], zones[3], zones[4] + zones[5]]):
            rows.append({"week": _week_label(monday), "level": level, "minutes": minutes,
                         "order": EFFORT_LEVELS.index(level)})
    if not rows:
        return None
    data = pd.DataFrame(rows).groupby(["week", "level", "order"], as_index=False)["minutes"].sum().round(0)
    width = _bar_width(weeks)
    return alt.Chart(data).mark_bar(size=width, stroke=_surface(), strokeWidth=2).encode(
        x=alt.X("week:N", sort=[_week_label(m) for m in mondays], title="Week starting", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("sum(minutes):Q", title="Minutes"),
        color=alt.Color("level:N", scale=alt.Scale(domain=EFFORT_LEVELS, range=EFFORT_COLORS),
                        legend=LEGEND),
        order=alt.Order("order:Q"),
        tooltip=[alt.Tooltip("week:N", title="Week starting"), alt.Tooltip("level:N", title="Effort"),
                 alt.Tooltip("minutes:Q", title="Minutes")],
    ).properties(height=240)


def strain_chart(days):
    data = days.dropna(subset=["day_strain"])
    return alt.Chart(data).mark_bar(
        size=_bar_width(len(data)), cornerRadiusTopLeft=4, cornerRadiusTopRight=4,
    ).encode(
        x=_date_axis(),
        y=alt.Y("day_strain:Q", title="Day strain (0-21)", scale=alt.Scale(domain=[0, 21])),
        tooltip=[DATE, alt.Tooltip("day_strain:Q", title="Strain"), alt.Tooltip("calories:Q", title="Calories"),
                 alt.Tooltip("steps:Q", title="Steps")],
    ).properties(height=220)


# ---------- Insights tab: patterns in the person's own data ----------

def sleep_vs_recovery_chart(days):
    """Each dot is one morning: hours slept (across) vs recovery (up). The gray line is the overall trend."""
    data = days.dropna(subset=["sleep_hours", "recovery_score"])
    if len(data) < 5:
        return None
    base = alt.Chart(data).encode(
        x=alt.X("sleep_hours:Q", title="Hours asleep", scale=alt.Scale(zero=False)),
        y=alt.Y("recovery_score:Q", title="Recovery %", scale=alt.Scale(domain=[0, 100])),
    )
    trend = base.transform_regression("sleep_hours", "recovery_score").mark_line(color=MUTED, strokeWidth=2)
    dots = base.mark_point(filled=True, size=80, opacity=0.9, stroke=_surface(), strokeWidth=2)
    # Bigger invisible circles make the dots easy to hover.
    targets = base.mark_point(size=500, opacity=0).encode(
        tooltip=[DATE, alt.Tooltip("sleep_hours:Q", title="Hours asleep"), alt.Tooltip("recovery_score:Q", title="Recovery %")],
    )
    return alt.layer(trend, dots, targets).properties(height=260)


def weekday_chart(days):
    """Average recovery for each day of the week, with the best day highlighted."""
    data = days.dropna(subset=["recovery_score"]).copy()
    if len(data) < 14:
        return None
    order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    data["weekday"] = data["date"].dt.strftime("%a")
    averages = data.groupby("weekday", as_index=False)["recovery_score"].mean().round(0)
    best = averages.loc[averages["recovery_score"].idxmax(), "weekday"]
    averages["best"] = averages["weekday"] == best
    highlight = "#3987e5" if _dark() else "#2a78d6"
    faded = "#383835" if _dark() else "#c3c2b7"
    return alt.Chart(averages).mark_bar(size=24, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("weekday:N", sort=order, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("recovery_score:Q", title="Average recovery %", scale=alt.Scale(domain=[0, 100])),
        color=alt.condition(alt.datum.best, alt.value(highlight), alt.value(faded)),
        tooltip=[alt.Tooltip("weekday:N", title="Day"), alt.Tooltip("recovery_score:Q", title="Average recovery %")],
    ).properties(height=220)


def insights(days, workouts, time_zone):
    """Plain-English findings from the person's own data.
    Returns a list of (icon, title, headline, explanation). Each needs enough data to be fair."""
    found = []
    mornings = days.dropna(subset=["recovery_score"]).sort_values("date")

    # 1. Does a full night of sleep help their recovery?
    long_nights = mornings[mornings["sleep_hours"] >= 7]["recovery_score"]
    short_nights = mornings[mornings["sleep_hours"] < 7]["recovery_score"]
    if len(long_nights) >= 3 and len(short_nights) >= 3:
        found.append((
            "bedtime", "Sleep and recovery",
            f"{long_nights.mean():.0f}% vs {short_nights.mean():.0f}%",
            f"Your average recovery after 7+ hours of sleep ({len(long_nights)} nights), "
            f"compared with shorter nights ({len(short_nights)} nights).",
        ))

    # 2. Does going to bed before midnight help?
    def before_midnight(start):
        return pd.notna(start) and datetime.fromisoformat(start).astimezone(time_zone).hour >= 18
    early = mornings[mornings["sleep_start"].map(before_midnight)]["recovery_score"]
    late = mornings[~mornings["sleep_start"].map(before_midnight) & mornings["sleep_start"].notna()]["recovery_score"]
    if len(early) >= 3 and len(late) >= 3:
        found.append((
            "dark_mode", "Bedtime",
            f"{early.mean():.0f}% vs {late.mean():.0f}%",
            f"Your average recovery when you fell asleep before midnight ({len(early)} nights), "
            f"compared with after midnight ({len(late)} nights).",
        ))

    # 3. How do they bounce back after a hard day? (strain of 14+, then the next morning's recovery)
    by_date = mornings.set_index("date")["recovery_score"]
    after_hard, after_easy = [], []
    for _, day in mornings.iterrows():
        next_morning = by_date.get(day["date"] + pd.Timedelta(days=1))
        if next_morning is None or pd.isna(day["day_strain"]):
            continue
        (after_hard if day["day_strain"] >= 14 else after_easy).append(next_morning)
    if len(after_hard) >= 2 and len(after_easy) >= 3:
        found.append((
            "local_fire_department", "After hard days",
            f"{sum(after_hard) / len(after_hard):.0f}% vs {sum(after_easy) / len(after_easy):.0f}%",
            f"Your recovery the morning after a day with strain of 14 or more ({len(after_hard)} times), "
            f"compared with after easier days.",
        ))

    # 4. The 80/20 rule: most training time should be easy.
    easy = hard = 0.0
    for zones in workouts.get("zone_minutes", pd.Series(dtype=object)):
        if isinstance(zones, list) and len(zones) >= 6:
            easy += zones[1] + zones[2]
            hard += zones[3] + zones[4] + zones[5]
    if easy + hard >= 60:
        found.append((
            "directions_run", "Easy vs hard training",
            f"{100 * easy / (easy + hard):.0f}% easy",
            "Share of your workout time in heart-rate zones 1-2. Many coaches suggest about 80% easy, "
            "20% moderate or hard.",
        ))

    # 5. HRV this week compared with the weeks before.
    hrv = days.dropna(subset=["hrv_ms"]).sort_values("date")
    if len(hrv) >= 14:
        recent, before = hrv.tail(7)["hrv_ms"].mean(), hrv.iloc[:-7]["hrv_ms"].mean()
        change = 100 * (recent - before) / before
        found.append((
            "monitor_heart", "HRV trend",
            f"{change:+.0f}%",
            f"Your average HRV over the last 7 days ({recent:.0f} ms) compared with before that ({before:.0f} ms). "
            "Rising HRV usually means you're adapting well.",
        ))
    return found
