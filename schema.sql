-- schema.sql: creates every table the website uses.
-- How to use it: Supabase -> SQL Editor -> New query -> paste this whole file -> Run.
-- It's safe to run more than once ("if not exists" skips tables that already exist).
--
-- Every table has an "email" column so each person's data stays separate.
-- Deleting someone from "profiles" automatically deletes all their other rows
-- too ("on delete cascade"). That's how the "Delete my data" button works.

-- One row per person who has signed in.
create table if not exists profiles (
  email text primary key,          -- their Google email (their ID everywhere else)
  name text,
  goal text,                       -- what they're training for (Phase 2)
  city text,                       -- for the weather (Phase 2)
  latitude double precision,
  longitude double precision,
  timezone text default 'America/New_York',
  run_times text,                  -- when they're willing to run (Phase 3)
  whoop_synced_at timestamptz,     -- last time we downloaded their WHOOP data
  created_at timestamptz default now()
);

-- A short random "ticket" made when someone presses "Connect WHOOP" (or Google).
-- When they come back, the ticket must match, which proves it's the same person.
create table if not exists oauth_states (
  email text references profiles(email) on delete cascade,
  provider text,                   -- 'whoop' or 'google'
  state text not null,
  created_at timestamptz default now(),
  primary key (email, provider)
);

-- Login tokens that let the website read WHOOP / Google for this person.
-- They're ENCRYPTED by the website before they get here.
create table if not exists oauth_tokens (
  email text references profiles(email) on delete cascade,
  provider text,                   -- 'whoop' or 'google'
  access_token text not null,      -- works for about an hour
  refresh_token text,              -- used to get a new access token
  expires_at timestamptz not null,
  primary key (email, provider)
);

-- One row per person per day of WHOOP numbers.
-- The date is the day they WOKE UP (last night's sleep counts as today).
create table if not exists daily_metrics (
  email text references profiles(email) on delete cascade,
  date date,
  recovery_score int,              -- 0-100 %
  hrv_ms double precision,         -- heart rate variability, in milliseconds
  resting_hr int,                  -- beats per minute
  sleep_hours double precision,
  sleep_performance int,           -- 0-100 %
  day_strain double precision,     -- WHOOP strain, 0-21
  weather_summary text,            -- added by the agent (Phase 2)
  primary key (email, date)
);

-- Each WHOOP workout. Its own table because there can be several per day.
create table if not exists workouts (
  id text primary key,             -- WHOOP's ID for the workout
  email text not null references profiles(email) on delete cascade,
  date date not null,
  sport text,
  minutes int,
  strain double precision,
  distance_miles double precision,
  avg_hr int
);

-- The training plan: what was planned each day, and what the agent changed it to.
create table if not exists training_plan (
  email text references profiles(email) on delete cascade,
  date date,
  planned_workout text,
  adjusted_workout text,
  reason text,
  calendar_event_id text,          -- the Google Calendar event (Phase 3)
  primary key (email, date)
);

-- One row each time someone presses "Plan my day" (Phase 2).
create table if not exists agent_runs (
  id bigint generated always as identity primary key,
  email text not null references profiles(email) on delete cascade,
  created_at timestamptz default now(),
  model text,
  steps jsonb,                     -- which tools it called and what came back
  final_decision text,
  cost_usd numeric(10, 5)
);

-- Phone screen time (Phase 5).
create table if not exists screen_time (
  email text references profiles(email) on delete cascade,
  date date,
  minutes int,
  late_night_minutes int,
  source text,                     -- 'screenshot' or 'typed'
  primary key (email, date)
);

-- SECURITY: turn on Row Level Security and add NO policies.
-- That means the public (publishable) key can't read or change anything.
-- Only the website's server, which uses the secret key, can get in.
alter table profiles      enable row level security;
alter table oauth_states  enable row level security;
alter table oauth_tokens  enable row level security;
alter table daily_metrics enable row level security;
alter table workouts      enable row level security;
alter table training_plan enable row level security;
alter table agent_runs    enable row level security;
alter table screen_time   enable row level security;
