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

# Daily limits, because everyone's runs come out of one Claude account.
RUNS_PER_PERSON_PER_DAY = 5
RUNS_PER_SITE_PER_DAY = 100

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
