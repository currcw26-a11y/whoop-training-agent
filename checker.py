# checker.py: checks every reply from the AI coach before the person sees it (rule G2).
#
# Code checks are the fastest, most reliable kind of test, so after Claude writes its answer,
# check() looks for problems with plain rules: did it name a medical condition, skip the rule
# IDs, go over today's limits, forget to suggest a doctor when it should, or give a bedtime or
# caffeine time that doesn't match the sleep plan? If so, agent.py sends the problems back to
# Claude once to fix; if the second try still fails, the person gets a plain reply built only
# from the facts (facts.fallback).

import re

import facts

# Naming a condition is diagnosing. The coach describes warning signs and sends people to a person.
CONDITIONS = re.compile(
    r"\b(stress fractures?|shin splints|tendini?tis|tendonitis|plantar fasciitis|it band syndrome|runner'?s knee|"
    r"covid(?:-19)?|the flu|influenza|an infection|a virus|overtraining syndrome|mononucleosis|mono|strep|"
    r"pneumonia|anemia|asthma|a sprain|a fracture)\b", re.IGNORECASE)
SICK = re.compile(r"\byou(?:'re| are)(?: probably| likely)? (?:getting |coming down with |)sick\b", re.IGNORECASE)
NOT_A_DIAGNOSIS = re.compile(r"(can'?t|cannot|not|n't|whether|if|rule out|no way to|unable to|only a|a doctor can|"
                             r"an athletic trainer can)\b[^.]{0,40}$", re.IGNORECASE)
SEE_SOMEONE = re.compile(r"\b(doctor|athletic trainer|physician|student health|health center|nurse)\b", re.IGNORECASE)
CITED = re.compile(r"\[([A-Z]\d(?:\s*,\s*[A-Z]\d)*)\]")
TIME = r"(\d{1,2}:\d{2}\s*[AaPp]\.?\s?[Mm]\.?)"
# A time that comes soon after these words, in the same sentence (the words in between are checked too).
BEDTIME = re.compile(r"\b(?:bedtime|in bed|to bed|lights[- ]out|asleep by|sleep by)\b([^\d.!?]{0,20}?)" + TIME,
                     re.IGNORECASE)
CAFFEINE = re.compile(r"\b(?:coffee|caffeine|energy drinks?|pre-?workout)\b([^\d.!?]{0,25}?)" + TIME, re.IGNORECASE)
NOT_THAT_TIME = re.compile(r"\b(wake|up at|alarm|until|class|run|workout)\b", re.IGNORECASE)


def _times(pattern, reply):
    return {_time(when) for gap, when in pattern.findall(reply) if not NOT_THAT_TIME.search(gap)}


def _time(text):
    """'10:45pm' / '10:45 P.M.' -> '10:45 PM'"""
    found = re.match(r"(\d{1,2}):(\d{2})\s*([AaPp])", text)
    return f"{int(found.group(1))}:{found.group(2)} {found.group(3).upper()}M"


def cited_rules(text):
    return [rule.strip() for group in CITED.findall(text or "") for rule in group.split(",")]


def diagnosis(text):
    """The first condition the reply names as if it were a diagnosis, or None."""
    for found in list(CONDITIONS.finditer(text)) + list(SICK.finditer(text)):
        if not NOT_A_DIAGNOSIS.search(text[max(0, found.start() - 60):found.start()]):
            return found.group(0)
    return None


def check(reply, today, mode, saved_workout=None):
    """Problems with one reply, as plain sentences Claude can act on (an empty list means it passed).
    today: facts.daily() for this person; saved_workout: today's workout if this run saved one."""
    problems, caps = [], today["limits"]
    named = diagnosis(reply)
    if named:
        problems.append(f"It names a medical condition ('{named}'). Don't diagnose: describe the warning sign and "
                        "suggest an athletic trainer or doctor.")
    cited = cited_rules(reply)
    unknown = sorted(set(cited) - set(facts.RULES))
    if unknown:
        problems.append(f"It cites rule IDs that don't exist: {', '.join(unknown)}. Use only IDs from the packet's rules.")
    if (mode == "plan" or saved_workout) and not cited:
        problems.append("It recommends a workout without citing the rules it follows. End those sentences with rule "
                        "IDs in square brackets, like [R1, L1].")
    if saved_workout:
        level = facts.intensity(saved_workout)
        if facts.LEVELS.index(level) > facts.LEVELS.index(caps["max_intensity"]):
            problems.append(f"Today's workout ('{saved_workout}') is {level}, but today's limit is "
                            f"{caps['max_intensity']}. Change it with save_daily_log.")
        if not caps["running_allowed"] and facts.is_run(saved_workout):
            problems.append(f"Today's workout ('{saved_workout}') is a run, but running isn't allowed today [B2]. "
                            "Swap it for cross-training at the same effort with save_daily_log.")
        size = facts.run_size(saved_workout)
        if size and size[0] and caps["longest_run_miles"] and size[0] > caps["longest_run_miles"] + 0.05:
            problems.append(f"Today's run is {size[0]:g} miles, over the spike guard's {caps['longest_run_miles']:g} "
                            "miles [L1].")
    engine, pain = today["engine"], today["pain"]
    if ((engine and engine["alert"] == "rest") or (pain and pain["light"] == "red")) and not SEE_SOMEONE.search(reply):
        problems.append("Today's facts call for rest or a pain warning, so the reply must suggest checking in with a "
                        "doctor or athletic trainer if it continues.")
    nights = today["nights"] or []
    bedtimes = {facts.clock(night["bed"]) for night in nights}
    for said in _times(BEDTIME, reply):
        if bedtimes and said not in bedtimes:
            problems.append(f"It gives a bedtime of {said}, but the sleep plan's bedtimes are "
                            f"{', '.join(sorted(bedtimes))}. Use get_sleep_plan's exact times.")
    cutoffs = {facts.clock(night[k]) for night in nights for k in ("caffeine_cutoff", "energy_drink_cutoff")}
    for said in _times(CAFFEINE, reply):
        if cutoffs and said not in cutoffs:
            problems.append(f"It gives a caffeine time of {said}, which isn't in the sleep plan (tonight: coffee by "
                            f"{facts.clock(nights[0]['caffeine_cutoff'])}, energy drinks by "
                            f"{facts.clock(nights[0]['energy_drink_cutoff'])}) [S4].")
    return problems


CHECKS = 9  # how many kinds of problem check() looks for (shown on the website when a reply passes)
