# CLAUDE.md — Agentic Prediction Market System

## What This Project Is

A **math-first multi-agent system** that trades prediction markets (Kalshi + Polymarket).
Instead of reacting to news headlines, the system continuously estimates true probabilities
for active prediction market contracts, compares them against market prices, identifies
mispricings, and places limit orders when the Kelly criterion confirms positive expected value.

**Core philosophy:** Math decides → AI debates → News confirms → Kelly sizes → Market executes.

This was originally an equity-trading system. It is being **fully rebuilt** for prediction markets.
Some components are reusable. Most need to be replaced.

---

## CRITICAL: What to KEEP vs REMOVE vs REWRITE

### KEEP AS-IS (copy directly, these are battle-tested)

- `core/math_utils.py` — Kelly criterion, EV calculation, half-Kelly sizing.
  These formulas are PERFECT for binary prediction markets. Kelly was literally
  invented for binary bets. DO NOT modify the math. Only change: rename references
  from "stock trade" to "contract" in docstrings.

- `core/constants.py` — Safety rails: max position cap (25%), daily drawdown
  kill-switch (2%), stop-loss per position (5% of bankroll). Keep all of these.
  Add new constant: `MAX_CONCURRENT_POSITIONS = 15`

- `database/connection.py` — Async SQLite connection setup. Reusable as-is.

- `app/main.py` — FastAPI app factory pattern. Keep the structure, update the
  router imports to new routes.

- `.gitignore`, `.env.example` — Keep, update env vars for new API keys.

### REMOVE ENTIRELY (equity-trading artifacts, no longer needed)

- `app/services/alpaca.py` — Alpaca broker integration. Replace with Kalshi/Polymarket clients.
- `app/routes/trades.py` — Stock trade endpoints. Replace with contract/position endpoints.
- `app/routes/portfolio.py` — Equity portfolio logic. Replace with prediction market portfolio.
- `frontend/app.py` — Old Streamlit dashboard. Will be completely rewritten.

### REWRITE (keep the file, gut the contents)

- `app/services/agent_orchestrator.py` — Currently a linear 4-agent pipeline
  (Scraper→Theorist→FactChecker→Quant). Rewrite as the new probability estimation
  pipeline with Research Desk, Base Rate Desk, Model Desk, and conditional Debate Chatroom.
  Keep the LangGraph + smolagents + LiteLLM wiring pattern.

- `app/routes/agents.py` — Currently returns `BacktestQuantResult` and `EVAnalysis`.
  Rewrite to return `ProbabilityEstimate`, `EdgeAnalysis`, `MarketScan` responses.
  The old Pydantic models (`BacktestQuantResult`, `EVAnalysis`, `RunAgentsResponse`)
  are all equity-specific and must be replaced.

- `database/models.py` — Old models are equity-oriented (Trade, Position with ticker/side).
  Replace with prediction market models (see PROJECT_SPEC.md for full schemas).

- `core/config.py` — Add Kalshi API key, Polymarket private key, scanner schedule config.
  Remove Alpaca credentials.

- `agents/__init__.py` — Redefine agent registry for new agent types.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    SCHEDULED SCANNER                     │
│              (runs every N hours or nightly)             │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. Pull all active markets from Kalshi + Polymarket    │
│  2. Filter by: volume > threshold, days to expiry,      │
│     liquidity, spread width                             │
│  3. For EACH qualifying market:                         │
│     ┌──────────────────────────────────────────┐        │
│     │  Research Desk    → probability estimate  │        │
│     │  Base Rate Desk   → historical frequency  │        │
│     │  Model Desk       → calibrated model p    │        │
│     │                                           │        │
│     │  IF divergence > 10%:                     │        │
│     │    → Trigger Debate Chatroom              │        │
│     │    → Agents argue, converge               │        │
│     │  ELSE:                                    │        │
│     │    → Take median estimate                 │        │
│     │                                           │        │
│     │  system_probability = consensus output    │        │
│     │  market_price = current contract price    │        │
│     │  edge = system_probability - market_price │        │
│     │                                           │        │
│     │  IF edge > minimum_edge_threshold:        │        │
│     │    → Kelly gate (EV + position sizing)    │        │
│     │    → Confirmation filter                  │        │
│     │    → Place limit order as MAKER           │        │
│     └──────────────────────────────────────────┘        │
│                                                         │
│  4. Store all estimates in SetupBoard                   │
│  5. Monitor active positions for exit conditions        │
│  6. Log results for calibration tracking                │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## Project Structure (Target State)

```
Agentic_predictionmarket_agent/
├── agents/
│   ├── __init__.py              # Agent registry
│   ├── research_desk/           # NEW — web research agents
│   │   ├── __init__.py
│   │   └── researcher.py        # Tavily-powered research for specific markets
│   ├── base_rate_desk/          # NEW — historical frequency analysis
│   │   ├── __init__.py
│   │   └── base_rate.py         # "In last N similar events, X% resolved YES"
│   ├── model_desk/              # NEW — mathematical model agents
│   │   ├── __init__.py
│   │   ├── economic_model.py    # For Fed/CPI/GDP markets
│   │   ├── statistical_model.py # Generic Bayesian/frequentist estimation
│   │   └── polling_model.py     # For political markets
│   └── debate/                  # NEW — chatroom debate system
│       ├── __init__.py
│       └── chatroom.py          # Round-robin debate when agents diverge
│
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app (KEEP structure, update imports)
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── scanner.py           # NEW — POST /scan, GET /scan/results
│   │   ├── markets.py           # NEW — GET /markets, GET /markets/{id}
│   │   ├── positions.py         # NEW — GET /positions, POST /positions/close
│   │   └── calibration.py       # NEW — GET /calibration (Brier scores)
│   └── services/
│       ├── __init__.py
│       ├── agent_orchestrator.py # REWRITE — new LangGraph pipeline
│       ├── kalshi_client.py      # NEW — Kalshi API wrapper
│       ├── polymarket_client.py  # NEW — Polymarket CLOB wrapper
│       └── scanner_service.py    # NEW — scheduled scanning logic
│
├── core/
│   ├── __init__.py
│   ├── config.py                # REWRITE — new env vars
│   ├── constants.py             # KEEP + add prediction market constants
│   └── math_utils.py            # KEEP AS-IS — Kelly + EV
│
├── database/
│   ├── __init__.py
│   ├── connection.py            # KEEP AS-IS
│   └── models.py                # REWRITE — new prediction market models
│
├── frontend/
│   └── app.py                   # REWRITE — new Streamlit dashboard
│
├── CLAUDE.md                    # THIS FILE
├── PROJECT_SPEC.md              # Full technical specification
├── .env.example                 # Updated env template
├── .gitignore
└── requirements.txt             # Updated dependencies
```

---

## Tech Stack

| Layer | Tool | Notes |
|-------|------|-------|
| AI Brain | Claude via OpenClaw on Hostinger | Same as before, no per-call costs |
| Agent Framework | smolagents + LiteLLM | Agents write & execute Python |
| Orchestration | LangGraph | Fan-out/fan-in, cyclic subgraphs for debates |
| Web Search | Tavily | Research desk uses this |
| Market Data | Kalshi API + Polymarket CLOB API | FREE public endpoints for market data |
| Backend API | FastAPI | Async Python |
| Database | SQLite + SQLModel | Lightweight persistence |
| Execution | Kalshi SDK / Polymarket py-clob-client | Limit orders as maker |
| Dashboard | Streamlit | Python-native UI |
| Config | pydantic-settings | Type-safe .env loading |

---

## Implementation Order (BUILD IN THIS SEQUENCE)

### Phase 1: Foundation (do this first)
1. Update `core/config.py` with new environment variables
2. Update `core/constants.py` with prediction market constants
3. Rewrite `database/models.py` with new schemas (see PROJECT_SPEC.md)
4. Build `app/services/kalshi_client.py` — market data fetching
5. Build `app/services/polymarket_client.py` — market data fetching
6. Build `app/routes/markets.py` — basic market listing endpoints
7. Test: can we pull live markets from both platforms?

### Phase 2: Scanner
8. Build `app/services/scanner_service.py` — filters markets by criteria
9. Build `app/routes/scanner.py` — trigger and view scan results
10. Test: does the scanner find and rank qualifying markets?

### Phase 3: Probability Estimation (the core)
11. Build `agents/research_desk/researcher.py`
12. Build `agents/base_rate_desk/base_rate.py`
13. Build `agents/model_desk/` — start with statistical_model.py
14. Rewrite `app/services/agent_orchestrator.py` — new LangGraph graph
15. Test: given a market, do agents produce probability estimates?

### Phase 4: Debate System
16. Build `agents/debate/chatroom.py` — cyclic debate subgraph
17. Wire debate trigger into orchestrator (divergence > 10%)
18. Test: when agents disagree, does the chatroom resolve it?

### Phase 5: Kelly Gate + Execution
19. Wire Kelly math (already exists) to prediction market edge calculation
20. Build execution logic — place limit orders via Kalshi/Polymarket
21. Build `app/routes/positions.py` — position tracking
22. Test: end-to-end from scan → estimate → Kelly → order

### Phase 6: Dashboard + Calibration
23. Rewrite `frontend/app.py` — Setup Board view, active positions, debate logs
24. Build `app/routes/calibration.py` — Brier score tracking
25. Test: full system running on paper/demo accounts

---

## Key Design Decisions

### Binary Bet Kelly Formula
Prediction markets are pure binary bets. The Kelly formula simplifies to:
```
edge = system_probability - market_price
odds = (1 - market_price) / market_price
kelly_fraction = edge / (1 - market_price)
half_kelly = kelly_fraction / 2
position_size = min(half_kelly * bankroll, max_position_cap)
```
The existing `math_utils.py` already handles this. The inputs change
(p_win comes from agent consensus, profit/loss come from contract price)
but the formula is identical.

### Maker vs Taker
ALWAYS place limit orders (maker). Research shows takers lose ~32% on average
on Kalshi while makers lose ~10%. The spread IS the edge for makers.
Never use market orders.

### Debate Chatroom Trigger
Only trigger the expensive LLM debate when agent estimates diverge by >10
percentage points. If Research says 62% and Base Rate says 55% and Model
says 58%, just take the median (58%). If Research says 72% and Model says
48%, that 24-point divergence means something fundamental is disagreed
upon — debate is worth the compute.

### Calibration Tracking
Every resolved market updates the system's Brier score. Track per-agent
and per-category calibration. Over 100+ markets, this shows which agents
are well-calibrated and which are systematically overconfident.

---

## Environment Variables (.env)

```
# AI
OPENCLAW_BASE_URL=https://your-openclaw-server.com/v1
OPENCLAW_API_KEY=your-key
OPENCLAW_MODEL=claude-sonnet-4-6

# Search
TAVILY_API_KEY=your-tavily-key

# Kalshi
KALSHI_API_KEY_ID=your-key-id
KALSHI_PRIVATE_KEY_PATH=./kalshi_private_key.pem
KALSHI_USE_DEMO=true

# Polymarket
POLY_PRIVATE_KEY=your-polygon-wallet-private-key
POLY_SAFE_ADDRESS=your-polymarket-safe-address

# Scanner
SCANNER_INTERVAL_HOURS=6
MIN_MARKET_VOLUME=200
MIN_EDGE_THRESHOLD=0.05
MAX_DAYS_TO_EXPIRY=30

# Safety
MAX_POSITION_PCT=5.0
MAX_CONCURRENT_POSITIONS=15
DAILY_DRAWDOWN_LIMIT_PCT=2.0
BANKROLL=10000
```

---

## Coding Standards

- Python 3.11+
- All async where possible (FastAPI, httpx for API calls)
- Pydantic v2 for all data models
- SQLModel for database models
- Type hints everywhere
- Docstrings on all public functions
- Logging via `logging.getLogger(__name__)`
- No print statements in production code
- Tests in `tests/` mirroring the source structure

---

## Important Context

- The user is in India (Coimbatore, Tamil Nadu). Kalshi geographic restrictions
  may apply for live trading — always support demo/paper mode first.
- Polymarket recently relaunched in the US market; global access varies.
  The system should work with whichever platform is accessible.
- OpenClaw is a self-hosted Claude proxy on Hostinger. Route all LLM calls
  through LiteLLM pointed at this server. No per-call API costs.
- The Kelly math engine is the crown jewel. It must never be weakened or
  bypassed. Every trade must pass the EV gate. No exceptions.