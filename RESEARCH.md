# How the coach is personalized, and the research behind it

A general AI chatbot gives general advice: "get 7–9 hours of sleep," "listen to your body."
This coach is built to give advice that only makes sense for *you*. This page explains how,
and which studies each rule comes from.

## Why not "fine-tune" the AI?

Fine-tuning means retraining a model on thousands of examples. Google tried this for health
coaching: they fine-tuned their Gemini model on wearable data (the PH-LLM study, *Nature
Medicine*, 2025). It matched human experts on fitness questions, but its answers weren't always
consistent, and it sometimes made things up.

Recent research points to a cheaper approach that works for a class project too: give a strong
model **the person's own data and context**, plus **expert rules**, every time it runs. Studies
of sleep and fitness coaching chatbots found that systems grounded in the user's wearable data
and in behavior-change theory did better than generic chatbots (HealthGuru, CHI 2025). Google's
"Personal Health Agent" research split the job into three parts: a data analyst, a health
expert, and a coach.

Training Coach does the same three jobs:

| Job | In this project |
|---|---|
| **Data analyst:** what's normal for *this* person? | `personal.py` works out your own normal ranges, sleep habits, training load, and patterns |
| **Expert:** what does the research say? | The coaching rules in `agent.py` (the table below) |
| **Coach:** knows you over time | Memory (`remember_about_them`), your goal, your calendar, and a chat page |

## What the coach knows about you

Before it decides anything, the coach can look up:

- **Your normal, not everyone's.** Your 7-day HRV and resting heart rate averages against your
  own normal range (from your last 60 days).
- **Your patterns.** For example, how your recovery changes after short nights or hard days,
  found in your own WHOOP history.
- **Your sleep.** Your usual bedtime and wake-up time, your sleep need, and how much your
  bedtime varies.
- **Your training load.** This week compared with the 4 weeks before, and how much of your
  training is easy.
- **Your schedule.** Classes, exams, and club tennis from Google Calendar.
- **What you told it.** Short notes it saves, like "left shin gets sore after long runs." You can
  see, edit, or delete them on the Coaching profile page.

The coach is told never to give advice on its own if it isn't tied to one of these. When it
quotes a number, it has to say what it's compared with. For example: "HRV 52 ms this week, below
your normal 58–64."

## The rules, and where they come from

| Rule the coach follows | What the research found | Source |
|---|---|---|
| Judge HRV by the **7-day average** against your normal range, not one morning | In a study of 40 recreational runners, training planned around the 7-day HRV average improved 3000 m speed. Single mornings varied about twice as much as the weekly average. | Vesterinen et al., *Medicine & Science in Sports & Exercise*, 2016 ([record](https://jyx.jyu.fi/jyx/Record/jyx_123456789_50625/Description)) |
| Raise weekly running about **10%**, never **more than ~30% over two weeks** | In 874 new runners, those who increased distance by more than 30% had more distance-related injuries (a warning sign, not a proven cutoff). | Nielsen et al., *Journal of Orthopaedic & Sports Physical Therapy*, 2014 ([abstract](https://vbn.aau.dk/da/publications/excessive-progression-in-weekly-running-distance-and-risk-of-runn)) |
| **About 80% of training easy** | Recreational runners on a mostly-easy plan improved their 10K at least as much as runners doing more medium-hard training (a small study). | Muñoz et al., *International Journal of Sports Physiology and Performance*, 2014 ([PubMed](https://pubmed.ncbi.nlm.nih.gov/23752040/)) |
| **Sleep need is personal** (uses WHOOP's estimate, not "8 hours for everyone") | An expert consensus on athletes' sleep says one number for everyone is unlikely to be right. | Walsh et al., *British Journal of Sports Medicine*, 2021 ([abstract](https://bjsm.bmj.com/content/55/7/356.abstract)) |
| Keep your **wake-up time steady** (the sleep plan won't let you sleep in more than 30 min) | Harvard students with irregular sleep had lower GPAs, even when they slept the same total hours. | Phillips et al., *Scientific Reports*, 2017 ([article](https://pmc.ncbi.nlm.nih.gov/articles/PMC5468315)) |
| **No caffeine within 6 hours** of bedtime | Caffeine taken 6 hours before bed still cut sleep by more than an hour, and people didn't notice. | Drake et al., *Journal of Clinical Sleep Medicine*, 2013 ([article](https://pmc.ncbi.nlm.nih.gov/articles/PMC3805807/)) |
| No **hard workout ending within an hour of bed**; easy evening exercise is fine | A review of 23 studies found evening exercise doesn't hurt sleep in general, except possibly vigorous exercise right before bed. | Stutz et al., *Sports Medicine*, 2019 ([PubMed](https://pubmed.ncbi.nlm.nih.gov/30374942/)) |
| **Exams:** keep training easy and protect sleep the night before | Students' HRV is lower around exams (small studies). | e.g. *Behavioral Sciences*, 2018 ([article](https://doaj.org/article/4db60bcd6b0e493e9ba2f80addcbea1b)) |

## The sleep plan

The home page shows a recommended bedtime and wake-up time for tonight and the next 6 nights
(`sleep_plan.py`):

1. **Wake-up time:** an hour before your first class or event the next morning (15 minutes before
   a morning workout). With nothing early, it's at most 30 minutes after your usual wake-up time,
   to keep your schedule steady.
2. **Sleep needed:** WHOOP's estimate for you, which already includes sleep debt and hard days.
3. **Time in bed** = sleep needed ÷ your sleep efficiency (nobody is asleep the whole time in bed).
4. **Bedtime** = wake-up time − time in bed. **Caffeine cutoff** = 6 hours before that.

## Honest limits

- Most of these studies are small. The coach treats them as good guidelines, not laws.
- This is a class project. It gives **training suggestions, not medical advice**. If something
  hurts or the numbers look like illness, the coach says to rest and see a doctor or athletic
  trainer.

## Other sources

- Google Research: [A personal health large language model for sleep and fitness coaching](https://research.google/pubs/a-personal-health-large-language-model-for-sleep-and-fitness-coaching/) (PH-LLM)
- Google Research: [The anatomy of a personal health agent](https://research.google/blog/the-anatomy-of-a-personal-health-agent/)
- [Exploring Personalized Health Support through Data-Driven, Theory-Guided LLMs: A Case Study in Sleep Health](https://arxiv.org/pdf/2502.13920) (HealthGuru, CHI 2025)
