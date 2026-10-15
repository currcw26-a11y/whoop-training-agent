# Training Coach

An AI agent that works as a personal running coach. You sign in with Google and
connect your WHOOP and Google Calendar. When you press **Plan my day**, a Claude
agent checks your recovery, sleep, the weather, your class schedule, and your
training history, then decides today's workout, explains why, and suggests a
time on your calendar.

Built by Clive Curry for an AI seminar class at Wake Forest.

**[See the live demo](https://whoop-training-agent-docs.vercel.app/)**: a mock-up of the dashboard
with sample data. It doesn't need any sign-in, WHOOP, or Google Calendar.

> Training suggestions only, **not medical advice**.

## Progress

- [x] **Phase 0:** Project setup
- [x] **Phase 1:** Google sign-in, WHOOP connection, and a Supabase database (the class minimum: a website that reads live data from a database)
- [x] **Phase 2:** The agent: weather plus a Claude tool-use loop behind a "Plan my day" button
- [x] **Design pass:** Dashboard, charts, insights, and light and dark mode
- [x] **Phase 3:** Google Calendar. The agent suggests a workout time, and nothing is added until you approve it
- [x] **Dashboard update:** last, this, and next week on the calendar (with all-day events), 7/30/90-day trends, running miles done vs. planned, and WHOOP data that downloads itself
- [ ] **Phase 4:** Put it online (Streamlit Community Cloud)
- [ ] **Phase 5:** Screen Time, and how late-night phone use affects sleep
- [ ] **Phase 6 (optional):** A morning plan sent automatically

## How the agent works

It follows Anthropic's [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents):
the code calls the Claude API directly (no big framework), gives Claude a set of
**tools**, and runs a simple loop:

1. The website downloads your newest WHOOP data into the database.
2. Claude gets instructions, a description of you (goal, run times), and **11 tools**.
3. Claude asks to use tools. The code runs them and sends back the results.
4. That repeats until Claude decides, with at most 10 rounds so it can't run forever.
5. Every step is saved and shown on the page.

| Tool | What it gives Claude |
|---|---|
| `get_whoop_recovery`, `get_whoop_sleep`, `get_whoop_strain`, `get_whoop_workouts` | Your WHOOP numbers, compared with your own 30-day normal |
| `get_weather` | Hour-by-hour forecast for your city (Open-Meteo) |
| `get_calendar_events` | Your classes and events, plus free time that fits when you like to run |
| `get_training_plan`, `get_training_history` | The plan for the next 2 weeks, and what happened before |
| `update_training_plan` | Writes or changes the plan (the coach builds one if you don't have one) |
| `create_calendar_event` | *Suggests* a workout time. You approve it on the website |
| `save_daily_log` | Saves today's decision and the reason |

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
| `home.py` | The main page: today's numbers, the coach, the week on your calendar, trends, and insights |
| `settings.py` | The "Coaching profile" page: goal, city, run times, connections |
| `agent.py` | The AI coach: its instructions, its tools, and the tool-use loop |
| `config.py` | Settings: which Claude model, step limit, daily run limits, prices |
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
| `docs/index.html` | The demo website (one page, sample data) |

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
- **Claude credits** are shared by everyone who uses the site, so each person gets
  5 agent runs per day.
