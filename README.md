# GoalSeeker

A pre-match **over/under value-bet finder** that only uses real data and keeps an honest record.

Every few hours it:

1. downloads real fixtures, results and bookmaker prices (football-data.co.uk, free, no key),
2. fits a Dixon-Coles goal model to stored results (no AI, no guesses),
3. compares the model with each bookmaker's price, after removing the bookmaker's margin,
4. writes a **pick** when the edge clears your rules, and never changes it afterwards,
5. grades picks when results come in and updates the **Record**,
6. publishes the website.

It costs nothing to run: GitHub runs the job on a schedule and hosts the website.

---

## Read this first: what the testing showed

Before trusting any pick, look at the **Backtest** at the bottom of the Record screen. It replays
past seasons week by week with exactly the live rules, using only data that existed before each game.

When this version was tested on July 2025 to early September 2026 (22 leagues, Bet365
over/under 2.5 prices collected before each game):

| | |
|---|---|
| Backtest picks | 2,039 |
| Return per pick, level stakes | **−5.8%** (−119 units) |
| Winners: model expected / actual | 1,168 / 1,026 |
| Over/under 2.5 log-loss: model / Bet365 (lower is better; coin flip 0.693) | 0.684 / 0.679 |

The model has not beaten Bet365's prices. Mixing the model into the bookmaker's own probabilities
made predictions *worse* at every weight tried, which means the model knows nothing the price
doesn't already contain. A 4-point "edge" mostly measures where the model is wrong. The
app shows this up front and treats its picks as **paper trades** until its own live record (with
closing-line value) says otherwise. The site recomputes the backtest every week, so this banner
updates itself.

---

## Set it up (about 15 minutes, once)

You need a free GitHub account: <https://github.com/signup>.

**1. Create the repository**
- On GitHub, click **+** (top right) → **New repository**.
- Name it `goalseeker`. Choose **Public** (free hosting of the website needs a public repository;
  your picks will be publicly visible, and no secrets are stored). Click **Create repository**.

**2. Upload the files**
- On the new repository page, click **uploading an existing file**.
- Unzip `goalseeker.zip` on your computer and drag **everything inside the `goalseeker` folder**
  into the upload box, then click **Commit changes**.
- The `.github` folder must come along, because it holds the schedule. On a Mac it is hidden:
  in Finder press **Cmd + Shift + .** to show it before dragging. Check afterwards that the
  repository shows a `.github/workflows/goalseeker.yml` file.

**3. Turn on the website**
- In the repository: **Settings** → **Pages** → under *Build and deployment*, set **Source** to
  **GitHub Actions**.

**4. Run it for the first time**
- Go to the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
- Click **GoalSeeker** on the left → **Run workflow** → **Run workflow**.
- The first run takes about 5 minutes. It downloads several seasons of results and runs the
  backtest. When both jobs show a green tick, the site is live at
  `https://<your-github-username>.github.io/goalseeker/`

After that it runs by itself four times a day. If a run ever fails, GitHub emails you, and the
site's Settings screen shows which step failed and why.

---

## Change the settings

Open `config.yaml` in the repository, click the pencil icon, edit, **Commit changes**. The next
run uses the new values. Each pick stores the settings it was made with.

| Setting | Default | Meaning |
|---|---|---|
| `bankroll_units` | 100 | Bankroll in units. Stakes are shown in units, never currency |
| `kelly_fraction` | 0.25 | Quarter Kelly |
| `max_stake_pct` | 0.03 | Never more than 3% of bankroll on one pick |
| `edge_threshold` | 0.04 | Model must beat the bookmaker's fair probability by 4 points |
| `min_model_probability` | 0.50 | |
| `min_odds` | 1.30 | |
| `max_edge` | 0.15 | Bigger "edges" are treated as model error and never picked |
| `markets` | `[ou]` | Add `1x2` to also pick match results |
| `bettable_bookmakers` | `[B365, BFE]` | Bet365 and Betfair Exchange (5% commission applied) |

Turn leagues on or off in `leagues.yaml`.

---

## How every number is produced

**Model (Layer 1, `goalseeker/model.py`).** Dixon-Coles: each team has an attack and a defence
rating, plus home advantage per division and a correction for low scores (0-0, 1-0, 0-1, 1-1).
- Ratings learn from goals blended 50/50 with shots on target, because shots are a steadier
  signal of quality than goals.
- Older matches count less: a match one year old counts half.
- All divisions of a country share one scale, so a promoted team is rated against its new league.
- Teams with little data are pulled toward their division's average.
- Uncertainty comes from the model's own fit. Each prediction is recomputed for 400 plausible rating
  sets, which gives the 10–90% range and the confidence tier ("+EV in X% of model draws").

The three model settings were chosen by how well they predicted 2025/26 scorelines, not by
profit. They overlap the backtest period, so if anything the backtest flatters the model.

**Context (Layer 2, `goalseeker/teamstats.py`).** Short factual notes (recent goal counts, short
rest, promoted or relegated, last meeting) built from stored results. Display only. This version
uses no language model at all, so it stays free and every sentence can be checked.

**Edge and staking (Layer 3, `goalseeker/edge.py`).**
- Fair probability = 1/price for each outcome, scaled so one bookmaker's over/under pair sums to 100%.
- Edge = model − fair.
- EV and Kelly use the price you would actually get, after exchange commission.
- Picks are ranked by edge, never by probability.

**Picks are immutable (`data/picks.jsonl`).** A pick is written once, the first time it
qualifies. It is never edited, and there's at most one pick per fixture per market. It stores the
fixture, the odds snapshot id, the model numbers, the model run and the settings. Results are
written separately to `data/settlements.jsonl`. The git history of this repository is the audit
trail: every change to every file is timestamped.

**Record.** Built only from those two files. Every figure shows its sample size, and breakdowns
under 20 settled picks are hidden. Closing-line value compares your price with the same
bookmaker's closing price, published with the results.

---

## Limits you should know

- **Prices update twice a week.** The source collects them on Tuesday and Friday afternoons
  (UK time), so the price on a card can be days old by kickoff. Re-check before betting. Each
  card has a box where you can type your own bookmaker's price and see the edge and stake.
- **South African bookmakers aren't covered.** Hollywoodbets, Betway SA and Supabets aren't in
  any free data feed. The price box is how you check their prices against the model.
- **Only the 2.5 goals line has prices** in the free feed. The model prices 0.5/1.5/3.5 too, and
  shows them as "no price published", but they can never become picks.
- **Results arrive a day or two late** (the source updates a few times a week), so settlement
  lags. A pick with no result 10 days after kickoff is voided (postponed or abandoned).
- **Injuries and lineups aren't modelled.** No free structured source exists, and the spec
  forbids inventing them. The adjustment is zero.

## Possible next steps

- **Optional paid odds feed.** The Odds API (free tier, 500 requests a month) or SportMonks odds
  would give fresher snapshots and true closing lines. Both need a key stored as a GitHub secret.
- **A "sharp vs soft" view.** Flag when one bookmaker's price beats the de-margined Betfair
  Exchange price. In practice that is how most genuine edges are found. It isn't a statistical
  model, so it would need to be labelled clearly as a different method.

## Files

```
config.yaml, leagues.yaml       your settings
goalseeker/                     the pipeline (Python)
  sources/footballdata.py       download + parse the data source
  sync.py                       fixtures, prices, results (idempotent, logged)
  model.py                      Dixon-Coles + uncertainty
  edge.py                       margin removal, edge, rules, Kelly
  picks.py                      generatePicks (immutable picks, model runs)
  settle.py                     settleResults
  record.py                     the scoreboard
  backtest.py                   weekly walk-forward backtest
  teamstats.py                  team form + context notes
  export.py                     builds site/data/app.json
site/                           the website (plain HTML/JS, no build step; site/data is built each run)
data/                           everything the pipeline stores (committed = audit trail;
                                data/derived and data/cache are rebuilt each run)
tests/test_core.py              checks: python tests/test_core.py
.github/workflows/goalseeker.yml  the schedule
```

Run locally (optional): `pip install -r requirements.txt && python -m goalseeker run`, then
serve the `site` folder (`python -m http.server -d site`) and open <http://localhost:8000>.
