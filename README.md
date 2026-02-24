# Agentic Prediction Market Agent

A math-first multi-agent system that trades prediction markets (Kalshi + Polymarket). The system estimates true probabilities for active contracts using AI agent desks, identifies mispricings against market prices, and places limit orders when the Kelly criterion confirms positive expected value.

## How It Works

```
Scanner (every N hours)
  -> Pull active markets from Kalshi + Polymarket
  -> Filter by volume, spread, expiry
  -> For each qualifying market:
       3 Agent Desks estimate probability in parallel
         - Research Desk (web search + analysis)
         - Base Rate Desk (historical frequency)
         - Model Desk (Bayesian/statistical)
       If desks diverge > 10%: Debate Chatroom resolves
       Else: take median estimate
       -> Edge = system_probability - market_price
       -> Kelly gate (EV + half-Kelly sizing)
       -> Place limit order as MAKER
  -> Track positions, P&L, calibration
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Start the API
uvicorn app.main:app --reload

# Start the dashboard
streamlit run frontend/app.py
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/markets` | GET | List markets from Kalshi/Polymarket |
| `/scan/run` | POST | Trigger market scan |
| `/scan/results` | GET | View qualifying markets |
| `/positions` | GET | List positions |
| `/positions/summary` | GET | Portfolio summary |
| `/positions/{id}/close` | POST | Close position manually |
| `/positions/daily-pnl` | GET | Daily P&L + kill-switch status |
| `/calibration` | GET | Overall Brier score |
| `/calibration/agents` | GET | Per-agent accuracy |
| `/calibration/chart` | GET | Calibration chart data |

## Architecture

```
agents/                  # AI agent desks
  research_desk/         # Web research via Tavily
  base_rate_desk/        # Historical frequency analysis
  model_desk/            # Bayesian/statistical models
  debate/                # Round-robin debate chatroom

app/
  routes/                # FastAPI endpoints
    markets.py           # Market data
    scanner.py           # Scan triggers + results
    positions.py         # Position management
    calibration.py       # Brier score tracking
  services/
    kalshi_client.py     # Kalshi API (RSA-PSS auth)
    polymarket_client.py # Polymarket Gamma + CLOB APIs
    scanner_service.py   # Market scanning + filtering
    agent_orchestrator.py# LangGraph fan-out/fan-in pipeline
    edge_calculator.py   # Kelly gate wrapping math_utils
    execution.py         # Order placement + position lifecycle

core/
  math_utils.py          # Kelly criterion + EV (untouched crown jewel)
  constants.py           # Safety rails (drawdown, position limits)
  config.py              # pydantic-settings config

database/
  models.py              # Market, Position, EdgeAnalysis, CalibrationRecord
  connection.py          # Async SQLite via SQLModel

frontend/
  app.py                 # 5-page Streamlit dashboard
```

## Tech Stack

- **AI**: Claude via OpenClaw (self-hosted proxy) + smolagents + LiteLLM
- **Orchestration**: LangGraph (fan-out/fan-in, conditional debate)
- **Search**: Tavily
- **Markets**: Kalshi API v2 + Polymarket CLOB/Gamma APIs
- **Backend**: FastAPI + async SQLite (SQLModel)
- **Dashboard**: Streamlit
- **Math**: Kelly criterion, EV gating, half-Kelly sizing

## Safety Rails

- **EV Gate**: Every trade must have positive expected value
- **Half-Kelly**: Conservative position sizing (75% growth, 50% variance)
- **Max Position**: 5% of bankroll per trade
- **Max Concurrent**: 15 open positions
- **Daily Drawdown**: -2% kill switch stops all trading
- **Maker Only**: Limit orders only, never market orders

## Environment Variables

See `.env.example` for the full list. Key variables:

- `OPENCLAW_BASE_URL` / `OPENCLAW_API_KEY` - LLM proxy
- `TAVILY_API_KEY` - Web search
- `KALSHI_API_KEY_ID` / `KALSHI_PRIVATE_KEY_PATH` - Kalshi auth
- `POLY_PRIVATE_KEY` / `POLY_SAFE_ADDRESS` - Polymarket auth
- `BANKROLL` - Total capital for position sizing
