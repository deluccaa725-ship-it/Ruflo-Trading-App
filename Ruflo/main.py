import importlib.util
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests


BOT_NAME = "Ruflo"
BASE_DIR = Path(__file__).resolve().parent
LLM_RATE_LIMITED = False
CONTROL_DIR = BASE_DIR.parent / "The Claude Portfolio"
BASELINE_MAIN = CONTROL_DIR / "main.py"
RUFLO_LLM_PROVIDER = os.environ.get("RUFLO_LLM_PROVIDER", "ollama-first").strip().lower()
OLLAMA_BASE_URL = os.environ.get("RUFLO_OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("RUFLO_OLLAMA_MODEL", "qwen3:4b").strip()

_spec = importlib.util.spec_from_file_location("claude_portfolio_baseline", BASELINE_MAIN)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"Could not load baseline bot from {BASELINE_MAIN}")

baseline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(baseline)


def sync_baseline_state():
    baseline.BASE_DIR = str(BASE_DIR)
    control_env = CONTROL_DIR / ".env"
    ruflo_env = BASE_DIR / ".env"
    baseline.ENV_FILE = str(ruflo_env)
    baseline.PORTFOLIO_FILE = str(BASE_DIR / "portfolio.json")
    baseline.TRADES_FILE = str(BASE_DIR / "trades.csv")
    baseline.RUN_SUMMARY_FILE = str(BASE_DIR / "run_summary.json")
    baseline.YF_CACHE_DIR = str(BASE_DIR / ".yf-cache")
    if control_env.exists():
        baseline.load_dotenv(path=str(control_env), override=True)
    baseline.load_dotenv(path=baseline.ENV_FILE, override=True)
    baseline.configure_runtime_dirs()
    baseline.CONFIG = baseline.refresh_runtime_config()
    baseline.llm = None
    baseline.tavily = None
    baseline.sec_company_map = None


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_str(name, default):
    value = os.environ.get(name, default)
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


OLLAMA_TIMEOUT = max(5, env_int("RUFLO_OLLAMA_TIMEOUT", 120))


def log(message):
    print(f"[{BOT_NAME}] {message}", flush=True)


def add_issue(summary, message):
    summary["issues"].append(message)
    log(message)


def add_step(summary, label, detail):
    summary["steps"].append({"label": label, "detail": detail})
    log(f"{label}: {detail}")


def build_summary(status="idle"):
    summary = baseline.build_summary(status=status)
    summary.update(
        {
            "bot_name": BOT_NAME,
            "strategy": "multi_agent_research",
            "research_mode": "specialist_panel",
            "decision_style": os.environ.get("RUFLO_DECISION_STYLE", "aggressive").strip().lower(),
        }
    )
    return summary


def get_research_pool():
    pool = baseline.get_deepseek_candidate_pool()
    limit = max(1, env_int("RUFLO_RESEARCH_POOL", 12))
    return pool[: min(limit, len(pool))]


def assess_market_regime(macro_context):
    text = (macro_context or "").lower()
    bullish_markers = (
        "risk-on",
        "bullish",
        "breadth improving",
        "rate cuts",
        "soft landing",
        "easing inflation",
        "strong earnings",
        "commodities easing",
    )
    cautious_markers = (
        "risk-off",
        "volatile",
        "recession",
        "hawkish",
        "inflation",
        "geopolitical",
        "tightening",
        "credit stress",
        "tariff",
        "selloff",
    )
    bullish_hits = sum(1 for token in bullish_markers if token in text)
    cautious_hits = sum(1 for token in cautious_markers if token in text)
    if bullish_hits >= cautious_hits + 2:
        return "bullish"
    if cautious_hits >= bullish_hits + 2:
        return "risk_off"
    return "mixed"


def resolve_decision_style(market_regime):
    base_style = os.environ.get("RUFLO_DECISION_STYLE", "aggressive").strip().lower()
    if market_regime == "bullish":
        return "aggressive" if base_style != "conservative" else "balanced"
    if market_regime == "risk_off":
        return "conservative" if base_style != "aggressive" else "balanced"
    return "balanced" if base_style == "aggressive" else base_style


def _download_history(ticker, days):
    end = datetime.now().date()
    start = end - timedelta(days=max(30, days))
    if baseline.tradier_ready():
        try:
            data = baseline.tradier_daily_history(ticker, days=max(35, days))
            if not data.empty:
                return data
        except Exception as exc:
            log(f"  [{ticker}] Tradier history fallback failed: {exc}")

    data = baseline.yf.download(
        ticker,
        period="3mo",
        progress=False,
        auto_adjust=True,
        timeout=baseline.CONFIG["screen_timeout"],
        threads=False,
    )
    if data.empty:
        return pd.DataFrame(columns=["Close", "Volume"])
    if "Close" not in data or "Volume" not in data:
        return pd.DataFrame(columns=["Close", "Volume"])
    return data[["Close", "Volume"]].dropna()


def build_technical_context(ticker):
    data = _download_history(ticker, baseline.CONFIG["breakout_lookback"] + 25)
    if data.empty or len(data) < 21:
        return {
            "text": "Technical context unavailable.",
            "features": None,
            "score": 0.0,
        }

    closes = data["Close"].dropna()
    volumes = data["Volume"].dropna()
    features = baseline.compute_anomaly_features(closes, volumes)
    if not features:
        return {
            "text": "Technical context unavailable.",
            "features": None,
            "score": 0.0,
        }

    score = baseline.anomaly_score(features)
    text = (
        f"Price={features['price']:.2f}; rel_vol={features['relative_volume']:.2f}; "
        f"gap={features['gap_pct']*100:.2f}%; breakout={features['breakout_pct']*100:.2f}%; "
        f"distance_from_low={features['distance_from_low']*100:.2f}%; momentum={features['momentum']*100:.2f}%; "
        f"short_momentum={features['short_momentum']*100:.2f}%; volatility_expansion={features['volatility_expansion']:.2f}; "
        f"anomaly_score={score:.1f}"
    )
    return {"text": text, "features": features, "score": score}


def llm_provider_order():
    if RUFLO_LLM_PROVIDER in {"ollama", "ollama-first"}:
        return ["ollama", "groq"]
    if RUFLO_LLM_PROVIDER in {"groq", "groq-first"}:
        return ["groq", "ollama"]
    if RUFLO_LLM_PROVIDER in {"fallback", "deterministic"}:
        return []
    return ["ollama", "groq"]


def ollama_ready():
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        return response.ok
    except Exception:
        return False


def ollama_chat(prompt, expect_json=False, schema=None):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a concise equity research assistant that follows instructions exactly.",
            },
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    if expect_json:
        payload["format"] = schema if schema else "json"
    response = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    message = data.get("message", {}) if isinstance(data, dict) else {}
    content = message.get("content", "")
    if not isinstance(content, str):
        content = str(content)
    return content


def prompt_llm(prompt, expect_json=False, schema=None):
    global LLM_RATE_LIMITED

    last_error = None
    for provider in llm_provider_order():
        if provider == "ollama":
            if not ollama_ready():
                continue
            try:
                return ollama_chat(prompt, expect_json=expect_json, schema=schema)
            except Exception as exc:
                last_error = exc
                log(f"Ollama fallback failed: {exc}")
                continue

        if provider == "groq":
            if LLM_RATE_LIMITED:
                continue
            try:
                return baseline.get_llm().invoke(prompt).content
            except Exception as exc:
                last_error = exc
                message = str(exc).lower()
                if "rate limit" in message or "rate_limit" in message or "429" in message:
                    LLM_RATE_LIMITED = True
                log(f"Groq fallback failed: {exc}")
                continue

    if last_error is not None:
        log(f"LLM fallback exhausted: {last_error}")
    return fallback_llm_text(prompt)


def fallback_llm_text(prompt):
    prompt_lower = str(prompt).lower()
    if "fundamentals specialist" in prompt_lower:
        return "Fundamentals look mixed and the next-month focus should be on revenue growth, margins, and balance-sheet resilience."
    if "catalyst specialist" in prompt_lower:
        return "The setup is mixed, with catalysts offset by macro headwinds and the need for a cleaner earnings or news catalyst."
    if "risk specialist" in prompt_lower:
        return "The biggest bear case is a reversal if macro risk rises, and the hidden risk is crowding or sector overexposure."
    if "technical specialist" in prompt_lower:
        return "Price and volume behavior are neutral to supportive, and the setup would be invalidated by a break below recent support."
    if "return only valid json" in prompt_lower:
        return ""
    return "LLM unavailable; using deterministic fallback analysis."


def get_macro_context():
    try:
        return baseline.get_macro_context()
    except Exception as exc:
        log(f"Baseline macro context unavailable: {exc}")

    headlines = ""
    try:
        tavily = baseline.get_tavily()
        macro_search = tavily.search(
            query="US stock market economic outlook interest rates inflation earnings season tariffs",
            topic="news",
            days=7,
            max_results=5,
        )
        headlines = " | ".join(r.get("content", "")[:300] for r in macro_search.get("results", []))
    except Exception as exc:
        log(f"Macro search fallback failed: {exc}")

    if not headlines:
        return (
            "Macro context unavailable. Treat the regime as mixed, keep sizing moderate, "
            "and prefer names with clear technical support and specific catalysts."
        )

    prompt = f"""You are a macro economist briefing a stock picker.
Based on this week's headlines, write a concise 3-paragraph macro briefing:
1. Current market conditions and sentiment
2. Key risks to equities this week
3. Sectors/themes likely to outperform vs underperform

Headlines:
{headlines}"""

    try:
        return prompt_llm(prompt)
    except Exception as exc:
        log(f"Local macro synthesis failed: {exc}")
        return (
            "Macro context unavailable. Treat the regime as mixed, keep sizing moderate, "
            "and prefer names with clear technical support and specific catalysts."
        )


def run_specialist_panel(ticker, financials_str, stock_news, macro_context, technical_context, decision_style):
    panel = {}
    if decision_style == "aggressive":
        style_line = (
            "Lean into asymmetric upside. Favor actionable setups over neutral ones, "
            "but do not ignore obvious red flags."
        )
    elif decision_style == "balanced":
        style_line = "Stay selective, but still prefer clear setups with a defined catalyst and manageable risk."
    else:
        style_line = "Be conservative and require stronger evidence before assigning a strong positive view."

    fundamentals_prompt = f"""You are the fundamentals specialist for a next-month equity review.
Ticker: {ticker}
Decision style: {style_line}

Financials:
{financials_str}

Macro context:
{macro_context[:700]}

Write 2 concise sentences:
1. What is the strongest fundamental reason to own or avoid this name?
2. Name one metric that matters most for the next month."""

    catalyst_prompt = f"""You are the catalyst specialist for a next-month equity review.
Ticker: {ticker}
Decision style: {style_line}

Recent news:
{stock_news[:900]}

Macro context:
{macro_context[:500]}

Write 2 concise sentences:
1. What is the main catalyst or headwind?
2. Is the setup improving, deteriorating, or mixed?"""

    risk_prompt = f"""You are the risk specialist for a next-month equity review.
Ticker: {ticker}
Decision style: {style_line}

Financials:
{financials_str[:700]}

News:
{stock_news[:700]}

Technical context:
{technical_context['text']}

Write 2 concise sentences:
1. What is the biggest bear case?
2. What hidden risk should the portfolio avoid?"""

    technical_prompt = f"""You are the technical specialist for a next-month equity review.
Ticker: {ticker}
Decision style: {style_line}

Technical context:
{technical_context['text']}

Write 2 concise sentences:
1. Is price/volume behavior supportive, neutral, or weak?
2. What level or condition would invalidate the setup?"""

    panel["fundamentals"] = prompt_llm(fundamentals_prompt)
    panel["catalyst"] = prompt_llm(catalyst_prompt)
    panel["risk"] = prompt_llm(risk_prompt)
    panel["technical"] = prompt_llm(technical_prompt)
    return panel


SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string"},
        "score": {"type": "number"},
        "instrument_type": {"type": "string"},
        "thesis": {"type": "string"},
        "edge": {"type": "string"},
        "risk": {"type": "string"},
    },
    "required": ["ticker", "score", "instrument_type", "thesis", "edge", "risk"],
    "additionalProperties": True,
}


def synthesize_asset_signal(ticker, financials_str, stock_news, macro_context, technical_context, panel, decision_style):
    if decision_style == "aggressive":
        style_rules = """
- Be willing to score 65-85 for strong asymmetry even if the evidence is not perfect.
- Prefer conviction when catalyst + technicals + fundamentals align.
- Only keep scores below 50 when the setup is clearly weak or crowded.
"""
    elif decision_style == "balanced":
        style_rules = """
- Use the full 1-100 scale normally.
- Reserve scores above 75 for strong setups.
- Keep mixed evidence near the middle.
"""
    else:
        style_rules = """
- Stay cautious.
- Require very strong evidence for scores above 70.
- Keep mixed evidence low or midrange.
"""

    synthesis_prompt = f"""You are the chief investment officer synthesizing a multi-agent equity review.
Ticker: {ticker}

Return ONLY valid JSON:
{{"ticker":"{ticker}","score":1-100,"instrument_type":"stock|etf","thesis":"...","edge":"...","risk":"..."}}

Rules:
- Score is next-month attractiveness, not certainty.
- Use the specialist notes below, plus the technical score.
- Penalize weak catalyst quality, crowded valuation, or unclear risk/reward.
- Keep thesis specific and concise.
- Default to lower scores when evidence is mixed.
{style_rules}

Macro context:
{macro_context[:800]}

Financials:
{financials_str[:900]}

News:
{stock_news[:700]}

Technical context:
{technical_context['text']}

Specialist notes:
Fundamentals: {panel['fundamentals'][:500]}
Catalyst: {panel['catalyst'][:500]}
Risk: {panel['risk'][:500]}
Technical: {panel['technical'][:500]}"""

    raw = prompt_llm(synthesis_prompt, expect_json=True, schema=SCORE_SCHEMA)
    parsed = baseline.parse_json(raw)
    if parsed:
        parsed["ticker"] = ticker
        normalized = baseline.normalize_scored_asset(parsed)
        if normalized:
            return normalized

    # Deterministic fallback when the model response is malformed.
    technical_score = float(technical_context.get("score", 0.0))
    fallback_anchor = 52 if decision_style == "aggressive" else 45 if decision_style == "balanced" else 38
    fallback_score = int(max(1, min(100, round(fallback_anchor + technical_score / 4))))
    fallback_score += int(baseline.x_signal_bias_for_ticker(ticker).get("bias", 0))
    fallback_score = max(1, min(100, fallback_score))
    return {
        "ticker": ticker,
        "score": fallback_score,
        "instrument_type": "stock",
        "thesis": (
            f"Fallback synthesis used. Technical score {technical_score:.1f} "
            f"with specialist notes indicating a mixed-to-positive setup."
        )[:300],
        "edge": panel["catalyst"][:220],
        "risk": panel["risk"][:220],
        "market_cap_raw": None,
    }


def score_candidate_asset(ticker, macro_context, decision_style):
    fin = baseline.enrich_ticker(ticker)
    financials_str = baseline.format_financials(fin)
    stock_news = baseline.get_stock_news(ticker)
    technical_context = build_technical_context(ticker)

    panel = run_specialist_panel(
        ticker,
        financials_str,
        stock_news,
        macro_context,
        technical_context,
        decision_style,
    )
    result = synthesize_asset_signal(
        ticker,
        financials_str,
        stock_news,
        macro_context,
        technical_context,
        panel,
        decision_style,
    )

    x_signal = baseline.x_signal_bias_for_ticker(ticker)
    base_score = int(result.get("score", 0))
    adjusted_score = max(1, min(100, base_score + int(x_signal.get("bias", 0))))
    result["base_score"] = base_score
    result["x_bias"] = int(x_signal.get("bias", 0))
    result["x_mentions"] = int(x_signal.get("mentions", 0))
    result["score"] = adjusted_score
    result["research_panel"] = panel
    result["technical_context"] = technical_context["text"]
    if fin.get("marketCapRaw") not in {None, "N/A"}:
        result["market_cap_raw"] = fin.get("marketCapRaw")
    return result


def score_assets(candidates, macro_context, decision_style):
    scored = []
    for i, ticker in enumerate(candidates):
        log(f"Researching {ticker} ({i + 1}/{len(candidates)})...")
        try:
            result = score_candidate_asset(ticker, macro_context, decision_style)
            scored.append(result)
            log(
                f"  {ticker}: score={result['score']} (base={result.get('base_score', 0)}, "
                f"x_bias={result.get('x_bias', 0)}) | {result.get('thesis', '')[:100]}"
            )
        except Exception as exc:
            log(f"  [{ticker}] specialist research failed: {exc}")
    return scored


def run():
    sync_baseline_state()

    summary = build_summary(status="running")
    portfolio = baseline.load_portfolio()
    all_signals = []

    try:
        log(f"\n{BOT_NAME} - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        if tuple(int(part) for part in os.sys.version.split()[0].split(".")[:2]) >= (3, 14):
            add_issue(summary, "Python 3.14+ detected. Python 3.11 is recommended for cleaner LangChain compatibility.")

        current_month = baseline.get_month_key()
        already_rebalanced = portfolio.get("last_rebalance_month") == current_month
        in_window = baseline.in_rebalance_window()
        should_rebalance = baseline.CONFIG.get("force_rebalance", False) or (in_window and not already_rebalanced)

        market_ok, market_issues = baseline.market_data_ready()
        if not market_ok:
            for issue in market_issues:
                add_issue(summary, issue)
            add_step(summary, "Market data", "Skipping scoring and rebalance due to unavailable market data")
            candidates = []
        elif not should_rebalance:
            baseline.mark_to_market_positions(portfolio)
            add_step(summary, "Scheduling", "Outside monthly rebalance window or already rebalanced this month")
            candidates = []
        else:
            baseline.mark_to_market_positions(portfolio)
            candidates = get_research_pool()
            summary["tickers_considered"] = candidates
            add_step(summary, "Screening", f"Prepared {len(candidates)} assets for specialist review")

        ready, issues = baseline.research_pipeline_ready()
        selected_assets = []
        allocations = []
        if not ready:
            for issue in issues:
                add_issue(summary, issue)
            add_step(summary, "Research", "Skipped macro/news/LLM stages")
        elif not candidates:
            add_step(summary, "Research", "Skipped because there were no candidates")
        else:
            macro_context = get_macro_context()
            market_regime = assess_market_regime(macro_context)
            decision_style = resolve_decision_style(market_regime)
            summary["market_regime"] = market_regime
            summary["decision_style"] = decision_style
            add_step(summary, "Regime", f"Detected {market_regime} regime; using {decision_style} research posture")
            scored_assets = score_assets(candidates, macro_context, decision_style)
            selected_assets = baseline.rank_assets(scored_assets, max(1, int(baseline.CONFIG.get("target_assets", 15))))
            allocations = baseline.build_target_allocations(selected_assets, macro_context)
            summary["selected_assets"] = selected_assets
            summary["target_allocations"] = allocations
            all_signals = [
                {
                    "ticker": a["ticker"],
                    "direction": "BUY",
                    "confidence": int(a.get("score", 0)),
                    "bull_prob": int(a.get("score", 0)),
                    "base_prob": max(0, 100 - int(a.get("score", 0))),
                    "bear_prob": 0,
                    "reason": a.get("thesis", ""),
                }
                for a in selected_assets
            ]
            summary["signals"] = all_signals
            add_step(summary, "Research", f"Scored {len(scored_assets)} assets; selected {len(selected_assets)}")

        if should_rebalance and allocations:
            portfolio, executed_buys, executed_sells = baseline.execute_monthly_rebalance(portfolio, allocations)
            summary["executed_buys"] = executed_buys
            summary["executed_sells"] = executed_sells
            summary["rebalance_executed"] = True
            add_step(
                summary,
                "Execution",
                f"Monthly rebalance executed: {len(executed_sells)} sells, {len(executed_buys)} buys",
            )
        elif already_rebalanced and not baseline.CONFIG.get("force_rebalance", False):
            add_step(summary, "Execution", f"Skipped: rebalance for {current_month} already completed")
        else:
            add_step(summary, "Execution", "Skipped: outside monthly rebalance window or no allocations available")
    except Exception as exc:
        add_issue(summary, f"Run failed: {exc}")
        summary["status"] = "failed"
    finally:
        baseline.save_portfolio(portfolio)
        snapshot = baseline.portfolio_snapshot(portfolio)
        summary.update(snapshot)
        summary["metrics"] = baseline.compute_metrics()
        if summary["status"] != "failed":
            summary["status"] = "ok" if not summary["issues"] else "degraded"
        baseline.write_run_summary(summary)
        baseline.send_email_report(summary, all_signals=all_signals)

    log("\nPortfolio: " + json.dumps(portfolio, indent=2))
    log("Metrics: " + str(summary["metrics"]))
    log("Run summary: " + baseline.RUN_SUMMARY_FILE)
    return summary


if __name__ == "__main__":
    run()
