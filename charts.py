# charts.py: the dashboard's charts and "insights".
#
# Built with Altair, the charting library that comes with Streamlit.
# The same design rules apply to every chart (and match the demo page):
#   - every chart covers the WHOLE time range you picked, so 7, 30, and 90 days look different
#   - thin marks: bars at most 24px wide with rounded tops, 2px lines
#   - one y-axis per chart, see-through background, quiet gray axes
#   - colors checked for colorblind safety in light and dark mode; the legends are drawn
#     above each chart on the page (in home.py), and every number is in the table too
#   - hover any chart for exact numbers

import re
from datetime import datetime, timedelta

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
DATE = alt.Tooltip("date:T", title="Date", format="%a, %b %-d")
MILES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mi\b|miles?\b)", re.IGNORECASE)


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
    """The card color, used for the thin gaps between stacked pieces."""
    return "#1a1a19" if _dark() else "#fdfdfc"


def _blue():
    return "#3987e5" if _dark() else "#2a78d6"


def _finish(chart, height):
    """The same quiet look for every chart: see-through background, gray axes, no border."""
    grid = "#2c2c2a" if _dark() else "#e7e6e0"
    return chart.properties(height=height, background="transparent").configure_view(strokeWidth=0).configure_axis(
        labelColor=MUTED, titleColor=MUTED, gridColor=grid, domainColor=grid, tickColor=grid,
        labelFontSize=11, titleFontSize=11, labelFont="Inter", titleFont="Inter", labelPadding=6,
    ).configure_text(color="#ffffff" if _dark() else "#0b0b0b", font="Inter")  # number labels on the chart


def _when(moment):
    """A day as Altair's DateTime (it can't take Python dates directly in axis settings)."""
    return alt.DateTime(year=moment.year, month=moment.month, date=moment.day, hours=moment.hour)


def _date_axis(start, end):
    """An x-axis that always shows the whole range from start to end (both dates),
    with a label every day (7 days), every week (30 days), or every 2 weeks (90 days)."""
    days = (end - start).days + 1
    step = 1 if days <= 10 else 7 if days <= 45 else 14
    ticks = [end - timedelta(days=step * k) for k in range(days // step + 1) if end - timedelta(days=step * k) >= start]
    return alt.X(
        "date:T", title=None,
        scale=alt.Scale(domain=[_when(datetime.combine(start, datetime.min.time()) - timedelta(hours=12)),
                                _when(datetime.combine(end, datetime.min.time()) + timedelta(hours=12))]),
        axis=alt.Axis(values=[_when(datetime.combine(t, datetime.min.time())) for t in reversed(ticks)],
                      # labelFlush: a date right at the edge lines up with the edge instead of getting cut off
                      format="%b %-d", grid=False, labelAngle=0, labelFlush=16),
    )


def _bar_width(start, end):
    """Bars are at most 24px wide, and thinner for longer ranges, so there's air between them."""
    days = (end - start).days + 1
    return max(3, min(24, int(330 / days)))


# ---------- Recovery ----------

def recovery_chart(days, start, end):
    data = days.dropna(subset=["recovery_score"]).copy()
    data["zone"] = data["recovery_score"].map(lambda score: zone(score)[0])
    chart = alt.Chart(data).mark_bar(
        size=_bar_width(start, end), cornerRadiusTopLeft=4, cornerRadiusTopRight=4,
    ).encode(
        x=_date_axis(start, end),
        y=alt.Y("recovery_score:Q", title=None, scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(values=[0, 25, 50, 75, 100])),
        color=alt.Color("zone:N", scale=alt.Scale(domain=ZONES, range=[GREEN, YELLOW, RED]), legend=None),
        tooltip=[DATE, alt.Tooltip("recovery_score:Q", title="Recovery %"), alt.Tooltip("zone:N", title="Zone")],
    )
    return _finish(chart, 220)


def add_normal_range(days, column):
    """Add columns with the person's normal range for a number: their 30-day
    average, plus or minus one standard deviation (how much it usually varies)."""
    data = days.sort_values("date").copy()
    rolling = data[column].rolling(30, min_periods=5)
    data[f"{column}_low"] = (rolling.mean() - rolling.std()).round(1)
    data[f"{column}_high"] = (rolling.mean() + rolling.std()).round(1)
    return data


def trend_chart(days, column, label, unit, start, end):
    """A daily line with a shaded band for the person's normal range.
    `days` must already have the range columns from add_normal_range()."""
    data = days.dropna(subset=[column]).copy()
    if data.empty:
        return None
    data["normal"] = [
        f"{low:.0f}–{high:.0f} {unit}" if pd.notna(low) and pd.notna(high) else "not enough days yet"
        for low, high in zip(data[f"{column}_low"], data[f"{column}_high"])
    ]
    x = _date_axis(start, end)
    base = alt.Chart(data).encode(x=x)

    # Make the y-axis fit both the line and the band, with a little room above and below.
    values = pd.concat([data[column], data[f"{column}_low"], data[f"{column}_high"]]).dropna()
    room = max((values.max() - values.min()) * 0.12, 2)
    y_scale = alt.Scale(domain=[float(values.min() - room), float(values.max() + room)], nice=True)

    band = base.mark_area(opacity=0.22 if _dark() else 0.13, color=_blue()).encode(
        y=alt.Y(f"{column}_low:Q", title=None, scale=y_scale), y2=f"{column}_high:Q",
    )
    line = base.mark_line(strokeWidth=2, strokeCap="round", strokeJoin="round", color=_blue(),
                          point=alt.OverlayMarkDef(size=18, filled=True, color=_blue()) if len(data) <= 10 else False
                          ).encode(y=alt.Y(f"{column}:Q", scale=y_scale))

    # Hovering anywhere picks the nearest day: a thin rule, a dot, and a tooltip.
    hover = alt.selection_point(nearest=True, on="pointerover", fields=["date"], empty=False)
    catcher = base.mark_rule(opacity=0).encode(
        tooltip=[DATE, alt.Tooltip(f"{column}:Q", title=f"{label} ({unit})"), alt.Tooltip("normal:N", title="Your normal")],
    ).add_params(hover)
    rule = base.mark_rule(color=MUTED, strokeWidth=1).transform_filter(hover)
    dot = base.mark_point(filled=True, size=80, opacity=1, color=_blue()).encode(y=f"{column}:Q").transform_filter(hover)

    # Label only the latest value, at the end of the line.
    last = alt.Chart(data.tail(1)).encode(x="date:T", y=f"{column}:Q")
    end_dot = last.mark_point(filled=True, size=80, opacity=1, color=_blue())
    end_label = last.mark_text(dy=-14, fontWeight="bold", fontSize=12).encode(text=alt.Text(f"{column}:Q", format=".0f"))
    return _finish(alt.layer(band, line, rule, dot, end_dot, end_label, catcher), 220)


# ---------- Sleep ----------

def sleep_chart(days, start, end):
    """Each night's sleep stages stacked into one bar, with a gray tick for how much sleep WHOOP says you needed."""
    long = days.melt(id_vars=["date"], value_vars=list(SLEEP_STAGES), var_name="stage", value_name="hours").dropna()
    if long.empty:
        return None
    long["stage"] = long["stage"].map(SLEEP_STAGES)
    long["order"] = long["stage"].map({name: i for i, name in enumerate(SLEEP_STAGES.values())})
    width = _bar_width(start, end)
    # The y-axis has to reach the tallest bar AND the highest "needed" tick.
    totals = days[list(SLEEP_STAGES)].sum(axis=1)
    top = max(float(totals.max()), float(days["sleep_need_hours"].max() or 0)) + 0.5
    y_scale = alt.Scale(domain=[0, max(10, int(top) + 1)])

    bars = alt.Chart(long).mark_bar(size=width, stroke=_surface(), strokeWidth=1.5 if width >= 10 else 0.5).encode(
        x=_date_axis(start, end),
        y=alt.Y("sum(hours):Q", title=None, scale=y_scale, axis=alt.Axis(labelExpr="datum.label + 'h'")),
        color=alt.Color("stage:N", scale=alt.Scale(domain=list(SLEEP_STAGES.values())), legend=None),
        order=alt.Order("order:Q"),
        tooltip=[DATE, alt.Tooltip("stage:N", title="Stage"), alt.Tooltip("hours:Q", title="Hours", format=".1f")],
    )
    needed = alt.Chart(days.dropna(subset=["sleep_need_hours"])).mark_tick(
        orient="horizontal", color=MUTED, thickness=2, size=width + 8,
    ).encode(
        x="date:T", y=alt.Y("sleep_need_hours:Q", scale=y_scale),
        tooltip=[DATE, alt.Tooltip("sleep_need_hours:Q", title="Sleep needed (h)", format=".1f")],
    )
    return _finish(alt.layer(bars, needed), 220)


# Axis labels for the bedtime chart. Its numbers are "hours after 6pm", turned into clock times.
_CLOCK = ("((datum.value + 18) % 24) == 0 ? '12am' : ((datum.value + 18) % 24) < 12 ? ((datum.value + 18) % 24) + 'am' : "
          "((datum.value + 18) % 24) == 12 ? '12pm' : ((datum.value + 18) % 24 - 12) + 'pm'")


def _clock_text(moment):
    return f"{moment:%I:%M %p}".lstrip("0")


def bedtime_chart(days, time_zone, start, end):
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
    chart = alt.Chart(data).mark_bar(size=_bar_width(start, end), cornerRadius=4, color=_blue()).encode(
        x=_date_axis(start, end),
        y=alt.Y("begin:Q", title=None, scale=alt.Scale(domain=[low, high], reverse=True),
                axis=alt.Axis(labelExpr=_CLOCK, values=list(range(low, high + 1, 2)))),
        y2="end:Q",
        tooltip=[DATE, alt.Tooltip("asleep:N", title="Fell asleep"), alt.Tooltip("awake:N", title="Woke up")],
    )
    return _finish(chart, 240)


def strain_chart(days, start, end):
    data = days.dropna(subset=["day_strain"])
    chart = alt.Chart(data).mark_bar(
        size=_bar_width(start, end), cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color=_blue(),
    ).encode(
        x=_date_axis(start, end),
        y=alt.Y("day_strain:Q", title=None, scale=alt.Scale(domain=[0, 21]), axis=alt.Axis(values=[0, 7, 14, 21])),
        tooltip=[DATE, alt.Tooltip("day_strain:Q", title="Strain"), alt.Tooltip("calories:Q", title="Calories"),
                 alt.Tooltip("steps:Q", title="Steps")],
    )
    return _finish(chart, 200)


# ---------- Running: done vs. planned ----------

def _monday(day):
    return day - timedelta(days=day.weekday())


def planned_miles(workout):
    """Miles in a planned run or walk, like 'Easy run, 2.5 miles' -> 2.5. Rest and cross-training are 0."""
    text = (workout or "").lower()
    if "rest" in text or not ("run" in text or "walk" in text):
        return 0.0
    found = MILES.search(text)
    return float(found.group(1)) if found else 0.0


def weekly_running(workouts, plan_rows, today, weeks):
    """Running miles per week: done (from WHOOP) for past weeks and this week, and
    planned (from the coach's plan) for this week and next week."""
    this_monday = _monday(today)
    mondays = [this_monday - timedelta(weeks=weeks - 1 - i) for i in range(weeks)] + [this_monday + timedelta(weeks=1)]
    done = {m: 0.0 for m in mondays}
    if not workouts.empty:
        for _, run in workouts[workouts["sport"].str.contains("run", case=False, na=False)].iterrows():
            monday = _monday(run["date"].date())
            if monday in done and not pd.isna(run["distance_miles"]):
                done[monday] += run["distance_miles"]
    planned = {m: 0.0 for m in mondays}
    for row in plan_rows:
        day = datetime.fromisoformat(row["date"]).date()
        monday = _monday(day)
        if monday in planned and monday >= this_monday:
            planned[monday] += planned_miles(row.get("adjusted_workout") or row.get("planned_workout"))
    return pd.DataFrame([{
        "week": f"{m:%b} {m.day}", "done": round(done[m], 1), "planned": round(planned[m], 1),
        "when": "now" if m == this_monday else "next" if m > this_monday else "past",
    } for m in mondays])


def weekly_miles_chart(weekly):
    """Solid bars = miles you ran. Dashed outline = miles planned for this week and next."""
    width = 26
    x = alt.X("week:N", sort=list(weekly["week"]), title=None, axis=alt.Axis(labelAngle=0))
    top = max(float(weekly[["done", "planned"]].max().max()), 2.0)
    scale = alt.Scale(domain=[0, top * 1.3])
    weekly = weekly.assign(
        label_height=weekly[["done", "planned"]].max(axis=1),
        label=[f"{d:g} so far" if w == "now" else f"{p:g} planned" if w == "next" and p > 0 else ""
               for d, p, w in zip(weekly["done"], weekly["planned"], weekly["when"])],
    )
    data = alt.Chart(weekly)
    planned = data.transform_filter("datum.when != 'past' && datum.planned > 0").mark_bar(
        size=width, fillOpacity=0, stroke=_blue(), strokeDash=[4, 3], strokeWidth=1.5, cornerRadius=4,
    ).encode(x=x, y=alt.Y("planned:Q", title=None, scale=scale),
             tooltip=[alt.Tooltip("week:N", title="Week of"), alt.Tooltip("planned:Q", title="Planned miles")])
    done = data.mark_bar(size=width, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color=_blue()).encode(
        x=x, y=alt.Y("done:Q", title=None, scale=scale),
        tooltip=[alt.Tooltip("week:N", title="Week of"), alt.Tooltip("done:Q", title="Miles run")],
    )
    labels = data.mark_text(dy=-9, fontWeight="bold", fontSize=12).encode(
        x=x, y=alt.Y("label_height:Q", scale=scale), text="label:N",
    )
    return _finish(alt.layer(planned, done, labels), 220)


def effort_chart(workouts, today, weeks):
    """Minutes per week spent easy, moderate, and hard (from heart-rate zones)."""
    this_monday = _monday(today)
    mondays = [this_monday - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
    rows = []
    for _, workout in workouts.iterrows():
        zones = workout.get("zone_minutes")
        if not isinstance(zones, list) or len(zones) < 6:
            continue
        monday = _monday(workout["date"].date())
        if monday < mondays[0]:
            continue
        for level, minutes in zip(EFFORT_LEVELS, [zones[1] + zones[2], zones[3], zones[4] + zones[5]]):
            rows.append({"week": f"{monday:%b} {monday.day}", "level": level, "minutes": minutes,
                         "order": EFFORT_LEVELS.index(level)})
    if not rows:
        return None
    data = pd.DataFrame(rows).groupby(["week", "level", "order"], as_index=False)["minutes"].sum().round(0)
    chart = alt.Chart(data).mark_bar(size=22, stroke=_surface(), strokeWidth=1.5).encode(
        x=alt.X("week:N", sort=[f"{m:%b} {m.day}" for m in mondays], title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("sum(minutes):Q", title=None),
        color=alt.Color("level:N", scale=alt.Scale(domain=EFFORT_LEVELS, range=EFFORT_COLORS), legend=None),
        order=alt.Order("order:Q"),
        tooltip=[alt.Tooltip("week:N", title="Week of"), alt.Tooltip("level:N", title="Effort"),
                 alt.Tooltip("minutes:Q", title="Minutes")],
    )
    return _finish(chart, 220)


# ---------- Patterns in the person's own data ----------

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
    dots = base.mark_point(filled=True, size=80, opacity=0.9, color=_blue(), stroke=_surface(), strokeWidth=2)
    # Bigger invisible circles make the dots easy to hover.
    targets = base.mark_point(size=500, opacity=0).encode(
        tooltip=[DATE, alt.Tooltip("sleep_hours:Q", title="Hours asleep"), alt.Tooltip("recovery_score:Q", title="Recovery %")],
    )
    return _finish(alt.layer(trend, dots, targets), 240)


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
    faded = "#383835" if _dark() else "#c3c2b7"
    chart = alt.Chart(averages).mark_bar(size=24, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("weekday:N", sort=order, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("recovery_score:Q", title=None, scale=alt.Scale(domain=[0, 100])),
        color=alt.condition(alt.datum.best, alt.value(_blue()), alt.value(faded)),
        tooltip=[alt.Tooltip("weekday:N", title="Day"), alt.Tooltip("recovery_score:Q", title="Average recovery %")],
    )
    return _finish(chart, 200)


def insights(days, workouts, time_zone):
    """Plain-English findings from the person's own data. Each needs enough data to be fair.
    Returns a list of (icon, title, headline, explanation, how_many) - short, so the cards line up."""
    found = []
    mornings = days.dropna(subset=["recovery_score"]).sort_values("date")

    # 1. Does a full night of sleep help their recovery?
    long_nights = mornings[mornings["sleep_hours"] >= 7]["recovery_score"]
    short_nights = mornings[mornings["sleep_hours"] < 7]["recovery_score"]
    if len(long_nights) >= 3 and len(short_nights) >= 3:
        found.append(("bedtime", "Sleep", f"{long_nights.mean():.0f}% vs {short_nights.mean():.0f}%",
                      "Your recovery after 7+ hours of sleep, compared with shorter nights.",
                      f"{len(long_nights)} vs {len(short_nights)} nights"))

    # 2. How do they bounce back after a hard day? (strain of 14+, then the next morning's recovery)
    by_date = mornings.set_index("date")["recovery_score"]
    after_hard, after_easy = [], []
    for _, day in mornings.iterrows():
        next_morning = by_date.get(day["date"] + pd.Timedelta(days=1))
        if next_morning is None or pd.isna(day["day_strain"]):
            continue
        (after_hard if day["day_strain"] >= 14 else after_easy).append(next_morning)
    if len(after_hard) >= 2 and len(after_easy) >= 3:
        found.append(("local_fire_department", "After hard days",
                      f"{sum(after_hard) / len(after_hard):.0f}% vs {sum(after_easy) / len(after_easy):.0f}%",
                      "Your recovery the morning after a day with strain of 14+, compared with easier days.",
                      f"{len(after_hard)} hard days"))

    # 3. Does going to bed before midnight help?
    def before_midnight(start):
        return pd.notna(start) and datetime.fromisoformat(start).astimezone(time_zone).hour >= 18
    early = mornings[mornings["sleep_start"].map(before_midnight)]["recovery_score"]
    late = mornings[~mornings["sleep_start"].map(before_midnight) & mornings["sleep_start"].notna()]["recovery_score"]
    if len(early) >= 3 and len(late) >= 3:
        found.append(("dark_mode", "Bedtime", f"{early.mean():.0f}% vs {late.mean():.0f}%",
                      "Your recovery when you fell asleep before midnight, compared with after.",
                      f"{len(early)} vs {len(late)} nights"))

    # 4. The 80/20 rule: most training time should be easy.
    easy = hard = 0.0
    for zones in workouts.get("zone_minutes", pd.Series(dtype=object)):
        if isinstance(zones, list) and len(zones) >= 6:
            easy += zones[1] + zones[2]
            hard += zones[3] + zones[4] + zones[5]
    if easy + hard >= 60:
        found.append(("favorite", "Easy vs hard", f"{100 * easy / (easy + hard):.0f}% easy",
                      "Share of your workout time in easy heart-rate zones. Coaches suggest about 80%.",
                      f"{(easy + hard) / 60:.0f} hours of workouts"))

    # 5. HRV this week compared with the weeks before.
    hrv = days.dropna(subset=["hrv_ms"]).sort_values("date")
    if len(hrv) >= 14:
        recent, before = hrv.tail(7)["hrv_ms"].mean(), hrv.iloc[:-7]["hrv_ms"].mean()
        change = 100 * (recent - before) / before
        meaning = ("Rising, a sign you're adapting well." if change >= 3
                   else "Dipping, so recovery matters this week." if change <= -3 else "Holding steady.")
        found.append(("monitor_heart", "HRV trend", f"{change:+.0f}%",
                      f"Your HRV this week compared with before. {meaning}", f"{recent:.0f} vs {before:.0f} ms"))
    return found
