# Training Coach

An AI agent that works as a personal running coach. You sign in with Google and
connect your WHOOP and Google Calendar. When you press **Plan my day**, a Claude
agent checks your recovery, sleep, the weather, your class schedule, and your
training history, then decides today's workout, explains why, and suggests a
time on your calendar. You can also message the coach on the **Ask the coach** page
(for example, to plan for a race), and it shows a bedtime and wake-up time for each night.

The coach compares you with *your own* normal, remembers what you tell it, and follows
rules from sports and sleep research. **[RESEARCH.md](RESEARCH.md)** explains how, with sources.

Built by Clive Curry for an AI seminar class at Wake Forest.

**[See the live demo](https://whoop-training-agent-docs.vercel.app/)**: the whole site (Home, Ask the coach, and
Coaching profile) with sample data, and no sign-in, WHOOP, or Google Calendar needed. The AI coach is live there too:
press **Plan my day** or message it on **Ask the coach**, and it works on the sample data (nothing is saved).

> Training suggestions only, **not medical advice**.

## Progress

- [x] **Phase 0:** Project setup
- [x] **Phase 1:** Google sign-in, WHOOP connection, and a Supabase database (the class minimum: a website that reads live data from a database)
- [x] **Phase 2:** The agent: weather plus a Claude tool-use loop behind a "Plan my day" button
- [x] **Design pass:** Dashboard, charts, insights, and light and dark mode
- [x] **Phase 3:** Google Calendar. The agent suggests a workout time, and nothing is added until you approve it
- [x] **Dashboard update:** last, this, and next week on the calendar (with all-day events), 7/30/90-day trends, running miles done vs. planned, and WHOOP data that downloads itself
- [x] **Personalized coach:** research-based rules, your own normal ranges and patterns, memory, an
  "Ask the coach" chat, and a sleep plan (bedtime, wake-up time, caffeine cutoff) for the week ahead
- [ ] **Phase 4:** Put it online (Streamlit Community Cloud)
- [ ] **Phase 5 (optional):** A morning plan sent automatically

## How the agent works

It follows Anthropic's [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents):
the code calls the Claude API directly (no big framework), gives Claude a set of
**tools**, and runs a simple loop:

1. The website downloads your newest WHOOP data into the database.
2. Claude gets instructions, a description of you (goal, run times, what it remembers about you),
   and **up to 15 tools**.
3. Claude asks to use tools. The code runs them and sends back the results.
4. That repeats until Claude decides, with at most 10 rounds so it can't run forever.
5. Every step is saved and shown on the page.

| Tool | What it gives Claude |
|---|---|
| `get_whoop_recovery`, `get_whoop_sleep`, `get_whoop_strain`, `get_whoop_workouts` | Your WHOOP numbers, compared with your own 30-day normal |
| `get_personal_patterns` | What's normal for you: 7-day HRV and resting heart rate vs. your range, sleep habits, training load, patterns in your data |
| `get_sleep_plan` | Your bedtime and wake-up time for the next 7 nights |
| `get_weather` | Hour-by-hour forecast for your city (Open-Meteo) |
| `get_calendar_events` | Your classes and events, plus free time that fits when you like to run |
| `get_training_plan`, `get_training_history` | The plan for the next 2 weeks, and what happened before |
| `update_training_plan` | Writes or changes the plan (the coach builds one if you don't have one) |
| `create_calendar_event` | *Suggests* a workout time. You approve it on the website |
| `save_daily_log` | Saves today's decision and the reason |
| `remember_about_them` | Saves a short note about you (an injury, a preference). You can edit these |
| `set_goal` | Saves a new goal from the chat (only when you ask) |

The page shows each step the agent took, including a short summary of its reasoning
(open **How the coach decided** in the demo).

The agent runs on `claude-haiku-5-5`, and the model is one setting in `config.py`.

## How it's built

| Part | What it does |
|---|---|
| Streamlit | The website, written in Python |
| Supabase | The database. Each person's data is kept separate |
| Claude API | The agent |
| WHOOP API | Recovery, HRV, resting heart rate, sleep, strain, workouts |
| Open-Meteo | Weather (free, no key needed) |
| Google Calendar API | Finds free time and adds workouts you approve |

**Security:** API keys live in `.streamlit/secrets.toml`, which is never uploaded
to GitHub. WHOOP and Google login tokens are encrypted before they're saved.
The database blocks public access, and only the website's server can read it.

## Files

| File | What it does |
|---|---|
| `app.py` | Starts the website and lists its pages |
| `home.py` | The main page: today's numbers, the coach, the sleep plan, the week on your calendar, trends, and insights |
| `chat.py` | The "Ask the coach" page: message the coach |
| `settings.py` | The "Coaching profile" page: goal, city, run times, what the coach remembers, connections |
| `agent.py` | The AI coach: its instructions, its tools, and the tool-use loop |
| `personal.py` | Works out what's normal for you: HRV and heart-rate ranges, sleep habits, training load |
| `sleep_plan.py` | Recommended bedtimes and wake-up times |
| `RESEARCH.md` | How the coach is personalized, and the studies behind its rules |
| `config.py` | Settings: which Claude model, step limit, daily limits, sleep plan settings |
| `whoop.py` | Connects to WHOOP and downloads your data |
| `google_calendar.py` | Connects Google Calendar, finds free time, adds approved workouts |
| `weather.py` | City lookup and forecast from Open-Meteo |
| `charts.py` | Every chart and insight on the dashboard (built with Altair) |
| `ui.py` | Display helpers: the recovery ring, number tiles, today's agenda, the week calendar, the agent's steps |
| `db.py` | Reads and writes the database (encrypts login tokens) |
| `schema.sql` | Creates the database tables |
| `privacy.py` | Privacy policy and "Delete my data" |
| `.streamlit/config.toml` | The look: colors for light and dark mode, the Inter font |
| `style.css` | The look of the site (matches the demo): cards, tiles, the agenda, the week calendar |
| `assets/` | The logo shown in the top bar (light and dark versions) |
| `docs/index.html` | The demo website (sample data; Home, Ask the coach, and Coaching profile) |
| `docs/api/chat.py` | The demo's live AI coach: a small Vercel function that runs the tool loop on the sample data |

## Run it on your computer (Windows)

The first time, run these in the project folder:

```bash
py -3.13 -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in
your own keys. Then paste `schema.sql` into Supabase's SQL Editor and click Run.

After that, start the site with:

```bash
.venv\Scripts\streamlit.exe run app.py
```

It opens at http://localhost:8501.

## Limits to know about

- **WHOOP** lets an app connect to at most 10 WHOOP members until WHOOP approves it.
- **Google Calendar:** until Google verifies the app, people see a "Google hasn't
  verified this app" warning, and at most 100 people can connect.
- **Claude credits** are shared by everyone who uses the site, so there's a daily cap per
  person in `config.py`. Each run costs a fraction of a cent, so most people never reach it.
