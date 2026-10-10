# Training Coach

An AI training coach website that anyone can use. You sign in with Google,
connect your WHOOP (and later your Google Calendar), and press **Plan my day**.
A Claude agent checks your recovery, sleep, the weather, your schedule, and your
training history, then decides today's workout and explains why.

Built for an AI seminar class at Wake Forest.

> Training suggestions only, **not medical advice**.

## How it's built

| Part | What it does |
|---|---|
| Streamlit | The website, written in Python |
| Supabase | The database. Each person's data is kept separate |
| Claude API | The agent. Claude calls "tools" in a loop until it reaches a decision |
| WHOOP API | Recovery, HRV, resting heart rate, sleep, strain, workouts |
| Open-Meteo | Weather (free, no key needed) |
| Google Calendar API | Finds free time and adds workouts |

## Run it on your computer (Windows)

Open a terminal in this folder, then:

```bash
.venv\Scripts\activate
```

```bash
streamlit run app.py
```

The site opens at http://localhost:8501.

The first time on a new computer, run these before the two commands above:

```bash
py -3.13 -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

Secrets (API keys) go in `.streamlit/secrets.toml`. Copy
`.streamlit/secrets.toml.example`, then fill in real values. The real file is
in `.gitignore`, so it never goes to GitHub.

The database tables come from `schema.sql`. Paste it into Supabase's SQL Editor
and click Run (one time only).

## Files

| File | What it does |
|---|---|
| `app.py` | Starts the website and lists its pages |
| `home.py` | The main page: today's numbers, the coach, this week's plan, and charts |
| `settings.py` | The "Coaching profile" page: goal, city, run times, connections |
| `charts.py` | Every chart and insight on the dashboard (built with Altair) |
| `ui.py` | Small display helpers, like showing the agent's steps |
| `agent.py` | The AI coach: its instructions, its tools, and the tool-use loop |
| `config.py` | Settings: which Claude model, step limit, daily run limits, prices |
| `weather.py` | City lookup and forecast from Open-Meteo |
| `privacy.py` | Privacy policy and "Delete my data" |
| `whoop.py` | Connects to WHOOP and downloads your data |
| `db.py` | Reads and writes the database (encrypts login tokens) |
| `schema.sql` | Creates the database tables |
| `.streamlit/config.toml` | The look: colors for light and dark mode, font, rounded corners |

## Build progress

- [x] Phase 0: Setup
- [x] Phase 1: Google sign-in, WHOOP, and the database
- [x] Phase 2: The agent (weather + Claude tool loop + "Plan my day")
- [x] Design pass: dashboard, charts, insights, light and dark mode
- [ ] Phase 3: Google Calendar (asks for approval before adding events)
- [ ] Phase 4: Deploy to Streamlit Community Cloud
- [ ] Phase 5: Screen Time
- [ ] Phase 6 (optional): Morning automation

## Limits to know about

- **WHOOP** lets an app connect to at most 10 WHOOP members until WHOOP
  reviews and approves it.
- **Google Calendar**: until Google verifies the app, people see a "Google
  hasn't verified this app" warning, and at most 100 people can connect.
- **Claude credits** are shared by everyone who uses the site, so each person
  gets a small number of agent runs per day.
