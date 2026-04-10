# Ruflo

Ruflo is a separate experimental bot built to test whether multi-agent research improves results versus `The Claude Portfolio`.

## What is different

- `The Claude Portfolio` stays as the control bot.
- `Ruflo` uses a specialist-panel research flow:
  - fundamentals
  - catalyst/news
  - risk
  - technicals
  - synthesis

The execution and portfolio accounting remain deterministic and shared with the baseline bot.

## Files

- `main.py` - Ruflo entrypoint
- `portfolio.json` - local portfolio state
- `trades.csv` - local trade log
- `run_summary.json` - local run output
- `.env` - local secrets/config
- `compare_metrics.py` - shared side-by-side metrics generator

## Setup

1. Open a terminal in this folder.
2. Create a virtual environment if needed:
   `python -m venv .venv`
3. Activate it:
   `.\.venv\Scripts\Activate.ps1`
4. Install dependencies:
   `pip install -r requirements.txt`
5. Copy `.env.example` to `.env` and fill in your keys.
6. Run Ruflo:
   `python main.py`

## Launch dashboard automatically

Run the launcher script to start a local web server, open the dashboard, and then execute the bot:

`.\run_ruflo.ps1`

If PowerShell file associations are acting up, use the Windows wrappers instead:

- `.\launch_ruflo.cmd`
- `.\launch_ruflo_daily.cmd`

## Daily 08:00 AM run

Install the Windows scheduled task that runs Ruflo daily at 08:00 AM local time:

`.\install_ruflo_daily_task.ps1`

That task calls the headless runner:

`.\run_ruflo_daily.ps1`

If `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, and optionally `GMAIL_TO` are set, Ruflo will email the daily report after the run completes.

## Weekly comparison report

Run this from the `Projects` folder to generate and email the weekly comparison report:

`.\run_weekly_compare.ps1`

## Research style

Ruflo defaults to a more assertive research posture than the control bot. You can tune it with:

- `RUFLO_DECISION_STYLE=aggressive`
- `RUFLO_DECISION_STYLE=balanced`
- `RUFLO_DECISION_STYLE=conservative`

For the experiment, `aggressive` is the default because it gives the multi-agent design more room to express a viewpoint.

## Local LLM mode

Ruflo can use Ollama first, then Groq, then a deterministic fallback. That helps when cloud token limits get tight.

Suggested setup:

1. Install Ollama.
2. Pull a model, for example:
   `ollama pull qwen3:8b`
3. Set these in `.env`:
   - `RUFLO_LLM_PROVIDER=ollama-first`
   - `RUFLO_OLLAMA_BASE_URL=http://localhost:11434`
   - `RUFLO_OLLAMA_MODEL=qwen3:4b`
   - `RUFLO_OLLAMA_TIMEOUT=120`

If Ollama is not running or the model is unavailable, Ruflo will fall back to Groq and then to deterministic scoring.

If you have a much stronger local machine and want higher reasoning quality, you can try `qwen3:8b` later. For the current setup, `qwen3:4b` is the better speed-to-quality tradeoff.

If you keep your Gmail credentials in the control bot's `.env`, Ruflo will inherit them automatically when launched from the scheduled task or the launcher.

## Comparison tip

For a fair test, keep both bots aligned on:

- same starting cash
- same broker mode
- same universe and rebalance schedule
- same risk limits
- same market data sources

Then compare:

- total return
- max drawdown
- win rate
- average trade return
- turnover
- number of false positives

## Notes

Ruflo is still a trading research tool, not a guarantee of better performance.
The point of this bot is to test whether deeper research improves outcomes enough to justify the extra complexity.
