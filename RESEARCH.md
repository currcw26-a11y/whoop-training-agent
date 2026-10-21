# How the coach decides, and the research behind it

A general AI chatbot gives general advice: "get 7–9 hours of sleep," "listen to your body."
This coach is built to give advice that only makes sense for *you*, and to stay inside
safety limits that come from published research. This page explains how, and which studies
each rule comes from.

## Code decides, the AI explains

Small AI models are bad at math done in words. In Google's study of a personal health AI agent,
a model reasoning over wearable numbers in plain text got **22%** of objective questions right;
with code doing the math, **74%**; the full agent, **84%** (Merrill et al., PHIA).

So Training Coach splits the job:

1. **Code works out the facts** (`facts.py`, `patterns.py`, `personal.py`). Every morning it
   computes, from your own data: the check engine light, your 7-day HRV and resting heart rate
   against your normal, your check-in and pain light, the longest run you can safely do, tonight's
   sleep times, exams coming up, and what moves your recovery. Those add up to **today's limits**
   (the highest intensity allowed, whether running is allowed, and the longest run allowed).
2. **Claude gets them as one "facts packet"** at the start of every run, and has to cite the rule
   behind each recommendation by its ID, like `[L1]`. On the website, each ID is a small chip you
   can hover to read the rule and its source.
3. **The tools enforce the limits.** If Claude tries to save a workout that breaks a limit
   (a run that's too long, a hard session on a rest day), the tool turns it down and says why.
4. **A checker reads every reply before you do** (`checker.py`). It looks for 9 kinds of problems:
   naming a medical condition, missing or made-up rule IDs, going over the intensity limit,
   running when running isn't allowed, a run past the spike guard, leaving out "see a doctor or
   athletic trainer" when the facts call for it, and a bedtime or caffeine time that doesn't
   match the sleep plan. If it finds one, Claude gets one try to fix it; if the second try still
   fails, you get a plain reply built only from the facts. "How the coach decided" shows every
   check.

This follows Anthropic's guidance on reducing hallucinations (answer from the provided
information, cite it, and check claims after writing them) and on evaluations (code checks are
the fastest, most reliable kind of test).

## The rules

| ID | Rule | What the research found | Source | Evidence |
|---|---|---|---|---|
| **R1** | **Check engine light.** If 2+ of resting heart rate, breathing rate, skin temperature, and HRV are outside your normal range, today is easy; 3+, or 2+ two days in a row, means rest. | Wearables picked up many infections before symptoms by comparing each person with their own baseline (26 of 32 COVID cases showed changes in one study), but with false alarms, so the coach needs several signals to agree and never names an illness. | [Mishra et al. 2020](https://www.nature.com/articles/s41551-020-00640-6), [Miller et al. 2020](https://pmc.ncbi.nlm.nih.gov/articles/PMC7728254/) (WHOOP breathing rate), [Mason et al. 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC8891385) | Moderate that the signals shift; weak for exact cut-offs |
| **R2** | **Readiness trend.** HRV and resting heart rate are judged by their 7-day average (HRV on a log scale) against your normal range. HRV below and resting HR above means easy days until they're back. A red recovery means easy; red on 2 of the last 3 days means rest. | In 40 recreational runners, doing hard sessions only when the 7-day HRV was in range improved 3000 m times more than a set plan. HRV-guided training helps, but modestly. | [Plews et al. 2013](https://scinapse.io/papers/2082001224), [Vesterinen et al. 2016](https://pubmed.ncbi.nlm.nih.gov/26909534/), [Manresa-Rocamora et al. 2021](https://www.mdpi.com/1660-4601/18/19/10299) | Moderate |
| **R3** | **Morning check-in.** A low check-in (energy, stress, soreness, sleep quality) plus any body signal that's off means an easy day. | A review of 56 studies found that how athletes say they feel tracked training load more reliably than heart-rate measures, and the two often disagreed, so the coach uses both. | [Saw, Main & Gastin 2016](https://bjsm.bmj.com/content/50/5/281) | Strong |
| **R4** | **Short sleep.** After 6 hours of sleep or less, afternoon and evening workouts stay easy and morning ones moderate. | A meta-analysis found 6 hours or less hurt performance, mostly for afternoon and evening exercise. | [Craven et al. 2022](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9584849/) | Moderate |
| **L1** | **Spike guard.** No run longer than 110% of your longest run in the 30 days before it. Planned runs count as done once their day passes, so a plan can still build about 10% per long run. | In 5,205 runners followed for 18 months, injuries rose when a single run went more than 10% past the longest of the past month. Week-to-week changes weren't linked to injury. | [Frandsen et al. 2025](https://research.usc.edu.au/esploro/outputs/journalArticle/How-much-running-is-too-much/991144502402621) | Moderate (mostly experienced adult runners) |
| **L3** | **Weekly build.** Weekly running grows no more than about 30% over two weeks. 10% a week is a comfortable default, not a proven rule. | In 874 novices, more than 30% over two weeks went with more injuries (not quite statistically significant). A randomized trial found the 10% rule didn't lower injuries. | [Nielsen et al. 2014](https://core.ac.uk/display/60599525), [Buist et al. 2008](https://search.pedro.org.au/search-results/record-detail/20220) | Weak |
| **B1** | **Mostly easy.** About 80% of training time easy, at least 2 rest or cross-training days a week, at most one faster session a week for newer runners. Run/walk is fine. | A mostly easy plan improved 10K times at least as much as a harder one; run/walk was about as fast as running in a marathon, with less pain. | [Muñoz et al. 2014](https://pubmed.ncbi.nlm.nih.gov/23752040/), [Hottenrott et al. 2016](https://edoc.unibas.ch/57171) | Moderate |
| **B2** | **Pain check.** Pain on one spot of the bone, pain at rest or at night, pain getting worse, or 5+/10 means no running and see an athletic trainer (red). 3–4/10, or pain still there the morning after a run, means cross-training at the same effort until it's gone (yellow). | Bone stress injuries show up as pain on one spot of the bone; the right load is one that causes no pain during, after, or the day after running. | [Warden et al. 2014](https://stanfordhealthcare.org/publications/812/812516.html), [Warden et al. 2021](https://scholarworks.indianapolis.iu.edu/items/0315cf2c-647a-4e9b-9933-651656a73272) | Moderate for the idea; the cut-offs are design choices |
| **S1** | **Steady sleep.** Keep bedtimes and wake-up times steady. The home page shows a Sleep Regularity Index and a "sleep barcode" of the last 14 nights. | Harvard undergraduates with more regular sleep had better grades; a National Sleep Foundation consensus says regular timing matters for health and performance. | [Phillips et al. 2017](https://doaj.org/article/98978c03a4da4abb894c69dbfb834eb9), [Sletten et al. 2023](https://www.scni.ox.ac.uk/publications/1528898/modal) | Moderate to strong |
| **S3** | **Sleep target.** At least 7 hours, more when WHOOP says you need it (up to 10). | Sleep experts recommend 7+ hours for adults; an athlete consensus says need is personal. | [Watson et al. 2015](https://pmc.ncbi.nlm.nih.gov/articles/PMC4434546/), [Walsh et al. 2021](https://bjsm.bmj.com/content/55/7/356.abstract) | Strong for the floor |
| **S4** | **Caffeine and late workouts.** Last coffee 9 hours before bedtime, energy drinks or pre-workout 13 hours before, hard workouts over at least 2 hours before bedtime. | A 2023 meta-analysis of 24 studies found caffeine cut sleep by 45 minutes and recommends a coffee at least 8.8 hours and a pre-workout at least 13.2 hours before bed. Hard exercise ending 2+ hours before bed didn't hurt sleep; closer than that did. | [Gardiner et al. 2023](https://nutrition-evidence.com/article/420813/the-effect-of-caffeine-on-subsequent-sleep-a-systematic-review-and-meta-analysis), [Frimpong et al. 2021](https://sleepreviewmag.com/sleep-health/sleep-whole-body/intense-workouts-before-bedtime-2/) | Strong |
| **A2** | **Exams.** No hard session within 2 days before an exam and nothing in the 2 hours before it; protect sleep the whole week before. | In 88 MIT students, sleep over the week and month before a test went with grades, not just the night before. Injury restrictions rose in exam weeks in college athletes. | [Okano et al. 2019](https://pmc.ncbi.nlm.nih.gov/articles/PMC6773696), [Mann et al. 2016](https://pubmed.ncbi.nlm.nih.gov/26049791/) | Moderate for sleep; the buffers are design choices |
| **W1** | **Weather.** Move runs away from thunderstorms, heavy rain, ice, and feels-like temperatures above about 90°F or below about 20°F, or go indoors. | Sports medicine guidelines change or cancel activity in high heat stress, and wait 30 minutes after the last thunder. | [Roberts et al. 2021](https://experts.umn.edu/en/publications/acsm-expert-consensus-statement-on-exertional-heat-illness-recogn/), [Walsh et al. 2013](https://pmc.ncbi.nlm.nih.gov/articles/PMC3600929) | Strong guidelines |
| **P3** | **Your patterns.** A consistent pattern in your own data can make a day easier, never harder (and only if it's worth 5+ recovery points). | Acting on noise is the main risk of personal analytics, and an unneeded easy day costs much less than an unneeded hard one. | [Kravitz & Duan 2014](https://www.aafp.org/content/dam/AAFP/documents/patient_care/nrn/design-implementation-n1-trials.pdf) | Weak (a safety principle) |

## What moves your recovery

The home page asks five questions of your own last 90 days, picked in advance (with only a few
weeks of one person's data, searching through dozens of numbers would find patterns by chance):

1. 7+ hours of sleep vs. less
2. Asleep before midnight vs. after
3. The day after a hard workout (10+ minutes in heart-rate zones 4–5, or a training load in your top quarter)
4. The day after your most common other sport (like club tennis)
5. The day after an exam (from your calendar)

For each one (`patterns.py`):

- **Recovery is compared with your own 28-day trend**, so getting fitter over the semester doesn't
  look like an effect. Yesterday's workout or exam goes with this morning's recovery.
- **A likely range**, not just one number: the 10th to 90th percentile from a moving-block
  bootstrap (it resamples whole weeks, because back-to-back days aren't independent, [Daza 2018](https://pmc.ncbi.nlm.nih.gov/articles/PMC6087468)).
- **Shrinkage:** each estimate is pulled toward "no effect" in proportion to how little data backs
  it (a starting point worth about 10 days, as in n-of-1 analysis).
- **Labels:** "Not enough data yet" until there are 8+ days in each group; "No clear difference yet"
  when the range includes zero; "Consistent pattern" only if it shows up in both halves of the data;
  otherwise "Possible pattern".
- **A "but also" line** when the two groups also slept 30+ minutes differently, so the coach doesn't
  blame tennis for what sleep did.
- Always "linked to," never "caused."

## The sleep plan

The home page shows a bedtime and wake-up time for tonight and the next 6 nights (`sleep_plan.py`):

1. **Wake-up time:** an hour before your first class or event the next morning (15 minutes before
   a morning workout). With nothing early, at most 30 minutes after your usual wake-up time, to keep
   your schedule steady [S1].
2. **Sleep needed:** WHOOP's estimate for you, at least 7 hours [S3].
3. **Time in bed** = sleep needed ÷ your sleep efficiency (nobody is asleep the whole time in bed).
4. **Bedtime** = wake-up time − time in bed. **Last coffee** 9 hours before that, **energy drinks**
   13 hours before, and **hard workouts** over 2 hours before [S4].

## Training load

Weekly totals count heart-rate minutes weighted by effort (Edwards' method: zone 1 = 1 point a
minute up to zone 5 = 5), every sport included. WHOOP's Strain is on a curved scale, so adding
Strain up would be misleading ([WHOOP](https://www.whoop.com/thelocker/how-does-whoop-strain-work-101/),
[Edwards' method](https://www.mdpi.com/2075-4663/6/3/68/htm)).

## Honest limits

These numbers are design choices or rest on thin evidence:

- The check engine light's smallest changes (3 bpm, 1 breath a minute, 0.5 °C, about 10% HRV) and the
  2-of-4 rule were chosen to cut false alarms; the illness studies used their own methods, mostly for
  COVID, and had false alarms too. The 0.5 °C step is bigger than the ~0.3 °C menstrual-cycle shift.
- The 10% single-run limit comes from experienced adult runners (average age 46). Whether the same
  cut-off fits college beginners hasn't been tested.
- The pattern minimums (21 days, 8 per group), the shrinkage strength, and the 5-point threshold are
  judgment calls. So are the pain cut-offs and the exam buffers.
- The 9-hour coffee cutoff is an average from pooled studies; people react to caffeine very differently.
- The check-in uses a fixed cut-off (10 of 20) until there are 14 check-ins to compare with.
- This is a class project. It gives **training suggestions, not medical advice**, and nothing here
  shows the coach prevents injuries; it follows safer, better-grounded rules.

## Things it deliberately doesn't do

| It doesn't | Because |
|---|---|
| Show an ACWR "sweet spot" or "danger zone" | The ratio's injury links turned out to be largely statistical artifacts, and in runners a higher ratio even went with fewer injuries ([Impellizzeri et al. 2020](https://opus.lib.uts.edu.au/handle/10453/141898)) |
| Add up or divide WHOOP Strain | Strain is on a curved scale, so sums and ratios are distorted |
| Decide anything from deep/REM sleep or wrist blood oxygen | Wearables are valid for sleep timing and length, not stages, and wrist SpO2 isn't medical grade ([Miller et al. 2022](https://pubmed.ncbi.nlm.nih.gov/36016077/)) |
| Search dozens of variables for "top correlations" | With a few weeks of data, some will look strong by chance |
| Collapse everything into one precise readiness score | False precision; it shows which signals are off and why instead |
| Name a condition ("shin splints," "stress fracture," "COVID") | That's diagnosing; it describes the warning sign and sends you to a person |
| Present the 10% rule as proven | A randomized trial found it didn't lower injuries |
| Let the AI do the math or invent thresholds | Text-only number reasoning was right 22% of the time in the PHIA study |

## Other sources

- Google Research: [A personal health large language model for sleep and fitness coaching](https://research.google/pubs/a-personal-health-large-language-model-for-sleep-and-fitness-coaching/) (PH-LLM)
- Merrill et al.: [The Personal Health Insights Agent (PHIA)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12855967)
- Anthropic: [Reduce hallucinations](https://docs.anthropic.com/en/docs/test-and-evaluate/strengthen-guardrails/reduce-hallucinations) and [Define success criteria and build evaluations](https://docs.anthropic.com/en/docs/test-and-evaluate/develop-tests)
- Schwellnus et al. 2016: [IOC consensus on load and risk of illness](https://pubmed.ncbi.nlm.nih.gov/27535991/)
- Schork 2015: [Time for one-person trials](https://www.jcvi.org/publications/personalized-medicine-time-one-person-trials), *Nature*
