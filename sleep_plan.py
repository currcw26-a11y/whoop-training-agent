# sleep_plan.py: recommended bedtimes and wake-up times.
#
# For each night:
#   1. Wake-up time: WAKE_BEFORE_EVENT_MINUTES before your first class or event the next
#      morning (WAKE_BEFORE_WORKOUT_MINUTES if it's a workout). With nothing early, wake close
#      to your usual time (at most 30 minutes later), because a steady wake-up time keeps
#      your body clock steady. In a Harvard study, students with irregular sleep had lower grades.
#   2. How much sleep: WHOOP's sleep need for you (it already counts sleep debt and hard days),
#      but never less than 7 hours, the sleep experts' minimum for adults (rule S3), or more than 10.
#   3. Time in bed: sleep need ÷ your sleep efficiency, since nobody is asleep the whole time in bed.
#   4. Bedtime = wake-up time − time in bed.
#   5. Caffeine (rule S4): last coffee 9 hours before bedtime, energy drinks or pre-workout 13 hours
#      before (a 2023 review of 24 studies), and hard workouts done 2+ hours before bedtime.
# Sources are in RESEARCH.md.

from datetime import datetime, time, timedelta

import config


def _round_down(moment, minutes=5):
    """Round down to the nearest 5 minutes, like 11:37 -> 11:35."""
    return moment.replace(minute=moment.minute - moment.minute % minutes, second=0, microsecond=0)


def commitments(events, plan_rows, time_zone):
    """Things you have to be up for: timed, busy calendar events, plus workouts the coach
    suggested that are still waiting for an OK (those aren't on Google Calendar yet)."""
    found = [{"title": e["title"], "start": e["start"], "workout": e["from_coach"]}
             for e in events if not e["all_day"] and (e["busy"] or e["from_coach"])]
    for row in plan_rows:
        if row.get("event_status") == "proposed" and row.get("event_start"):
            found.append({"title": row.get("event_title") or "Workout", "workout": True,
                          "start": datetime.fromisoformat(row["event_start"]).astimezone(time_zone)})
    return sorted(found, key=lambda c: c["start"])


def plan_night(night, found, habits, need_hours, time_zone):
    """The plan for the night that starts on the date `night` (so you wake up the next morning)."""
    morning = night + timedelta(days=1)
    usual_wake = (habits or {}).get("_wake", config.DEFAULT_WAKE_MINUTES)
    efficiency = (habits or {}).get("sleep_efficiency_percent") or 90
    efficiency = max(80, min(97, efficiency)) / 100

    # The first thing that morning, between 4am and 1pm.
    early = [c for c in found if c["start"].date() == morning and 4 <= c["start"].hour < 13]
    first = early[0] if early else None
    relaxed = datetime.combine(morning, time(0), time_zone) + timedelta(minutes=usual_wake + 30)
    needed_by = None
    if first:
        before = config.WAKE_BEFORE_WORKOUT_MINUTES if first["workout"] else config.WAKE_BEFORE_EVENT_MINUTES
        needed_by = first["start"] - timedelta(minutes=before)
    wake = _round_down(min(needed_by, relaxed) if needed_by else relaxed)

    need = max(7.0, min(10.0, need_hours or 8.0))
    in_bed = need / efficiency
    bed = _round_down(wake - timedelta(hours=in_bed))
    return {
        "night": night, "bed": bed, "wake": wake, "first": first,
        "set_by_first": bool(needed_by and needed_by < relaxed),  # True if the first event decided the wake-up time
        "efficiency": round(efficiency * 100),
        "need_hours": round(need, 1), "in_bed_minutes": int(round(in_bed * 60 / 5) * 5),
        "caffeine_cutoff": _round_down(bed - timedelta(hours=config.COFFEE_HOURS_BEFORE_BED), 15),
        "energy_drink_cutoff": _round_down(bed - timedelta(hours=config.ENERGY_DRINK_HOURS_BEFORE_BED), 15),
        "hard_workout_end": _round_down(bed - timedelta(hours=config.HARD_WORKOUT_HOURS_BEFORE_BED), 15),
        "earlier_than_usual_minutes": (
            int(round((habits["_bed"] - (bed.hour * 60 + bed.minute + (24 * 60 if bed.hour < 12 else 0))) / 5) * 5)
            if habits else None),
    }


def plan_week(first_night, events, plan_rows, habits, tonight_need, time_zone, nights=7):
    """Plans for `nights` nights in a row, starting with first_night. Tonight uses WHOOP's latest
    sleep need; later nights use the person's usual need."""
    found = commitments(events, plan_rows, time_zone)
    usual_need = (habits or {}).get("usual_sleep_need_hours") or tonight_need
    return [plan_night(first_night + timedelta(days=k), found, habits, tonight_need if k == 0 else usual_need, time_zone)
            for k in range(nights)]


def tonight(now):
    """The date tonight's sleep starts. Before 4am it's still last night."""
    return now.date() if now.hour >= 4 else now.date() - timedelta(days=1)
