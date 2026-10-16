# config.py: the settings you're most likely to change, all in one place.

# Which Claude model runs the coach.
#   "claude-haiku-5-5"  - cheapest, great for building and testing (about half a cent per run)
#   "claude-sonnet-5-5" - smarter plans, about 20x the price
MODEL = "claude-haiku-5-5"

# How hard Claude thinks before answering: "low", "medium", or "high".
# Higher is smarter but uses more tokens (more money).
EFFORT = "medium"

# Safety limit: the most rounds of tool use in one run. This stops the agent
# from looping forever (and from spending forever).
MAX_STEPS = 10

# The times of day people can pick on their Coaching profile, as (start hour, end hour).
RUN_TIME_WINDOWS = {
    "Early morning (6-9am)": (6, 9),
    "Late morning (9am-12pm)": (9, 12),
    "Midday (12-3pm)": (12, 15),
    "Afternoon (3-6pm)": (15, 18),
    "Evening (6-9pm)": (18, 21),
}

# When looking for free time on someone's calendar: only between these hours,
# and only gaps at least this long.
DAY_START_HOUR = 6
DAY_END_HOUR = 21
MIN_FREE_MINUTES = 30

# Daily limits, because everyone's runs come out of one Claude account. Each "Plan my day"
# and each message to the coach counts as one. The website never shows these numbers;
# people only see a short note if they actually reach one.
RUNS_PER_PERSON_PER_DAY = 15
RUNS_PER_SITE_PER_DAY = 150

# Sleep plan (sleep_plan.py): how long before your first event you should be up,
# and the wake-up time to assume before WHOOP has a couple of weeks of your sleep.
WAKE_BEFORE_EVENT_MINUTES = 60    # time to get ready and get to class
WAKE_BEFORE_WORKOUT_MINUTES = 15  # a morning run only needs shoes and a glass of water
DEFAULT_WAKE_MINUTES = 7 * 60 + 30  # 7:30 AM, as minutes after midnight

# The coach remembers up to this many short notes about each person (injuries, preferences...).
MAX_COACH_NOTES = 25

# Price in dollars per 1 million tokens, from Anthropic's pricing page.
# (Haiku 5.5's price is for prompts up to 100K tokens; ours are much smaller.)
PRICES = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
}
# Repeated input is cached: saving to the cache costs 1.25x the input price,
# and reading from it costs only 0.1x.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.1
