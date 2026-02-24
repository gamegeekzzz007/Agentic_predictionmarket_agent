# PROJECT_SPEC.md — Agentic Prediction Market System

## 1. System Overview

### What We're Building
An automated prediction market trading system that:
1. Scans active markets on Kalshi and Polymarket
2. Uses multi-agent AI to estimate true probabilities
3. Identifies mispricings (system estimate ≠ market price)
4. Sizes positions using Kelly criterion
5. Places limit orders as a maker
6. Tracks calibration over time to improve

### Why Prediction Markets (Not Equities)
- Binary outcomes → perfect for Kelly formula
- Inefficient markets → edge exists for analytical systems
- Free data APIs → full order books available
- Competition is weaker → retail bettors, not hedge funds
- Diverse categories → economics, politics, weather, sports
- Maker advantage → research shows makers outperform takers significantly

---

## 2. Database Models (database/models.py)

Replace ALL existing models. The old Trade/Position models are equity-specific.

```python
from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional
from enum import Enum


class Platform(str, Enum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


class MarketCategory(str, Enum):
    ECONOMICS = "economics"      # Fed, CPI, GDP, jobs
    POLITICS = "politics"        # Elections, policy
    WEATHER = "weather"          # Temperature, storms
    CRYPTO = "crypto"            # BTC/ETH price targets
    SPORTS = "sports"            # Game outcomes
    ENTERTAINMENT = "entertainment"
    OTHER = "other"


class MarketStatus(str, Enum):
    ACTIVE = "active"
    RESOLVED_YES = "resolved_yes"
    RESOLVED_NO = "resolved_no"
    EXPIRED = "expired"


class PositionSide(str, Enum):
    YES = "yes"
    NO = "no"


class PositionStatus(str, Enum):
    PENDING = "pending"          # Limit order placed, not filled
    OPEN = "open"                # Filled, position active
    CLOSED_WIN = "closed_win"    # Resolved in our favor
    CLOSED_LOSS = "closed_loss"  # Resolved against us
    CLOSED_EARLY = "closed_early"  # Exited before resolution
    CANCELLED = "cancelled"      # Order cancelled before fill


# ─────────────────────────────────────────────
# Core Market Model
# ─────────────────────────────────────────────
class Market(SQLModel, table=True):
    """A single prediction market contract."""
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # Platform identifiers
    platform: Platform
    platform_market_id: str        # Kalshi ticker or Polymarket condition_id
    platform_event_id: Optional[str] = None  # Parent event grouping
    
    # Market details
    title: str                     # "Will CPI exceed 3.0% in January 2026?"
    category: MarketCategory
    description: Optional[str] = None
    resolution_source: Optional[str] = None  # "Bureau of Labor Statistics"
    
    # Pricing (updated on each scan)
    yes_price: float               # Current YES price (0.00 - 1.00)
    no_price: float                # Current NO price (0.00 - 1.00)
    spread: float                  # Ask - Bid spread
    volume_24h: int = 0            # 24-hour volume in contracts
    
    # Timing
    close_time: Optional[datetime] = None     # When market stops trading
    resolution_time: Optional[datetime] = None # When outcome is determined
    days_to_expiry: Optional[int] = None
    
    # Status
    status: MarketStatus = MarketStatus.ACTIVE
    resolved_outcome: Optional[bool] = None  # True=YES won, False=NO won
    
    # Metadata
    first_seen: datetime = Field(default_factory=datetime.utcnow)
    last_updated: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────
# Probability Estimate (per scan cycle)
# ─────────────────────────────────────────────
class ProbabilityEstimate(SQLModel, table=True):
    """One agent desk's probability estimate for a market."""
    id: Optional[int] = Field(default=None, primary_key=True)
    
    market_id: int = Field(foreign_key="market.id")
    scan_id: str                   # Groups estimates from same scan cycle
    
    # Which desk produced this
    desk: str                      # "research", "base_rate", "model", "debate_consensus"
    agent_name: Optional[str] = None  # Specific agent within desk
    
    # The estimate
    probability: float             # 0.00 - 1.00
    confidence: float              # How confident the agent is (0-1)
    reasoning: str                 # Why this probability
    
    # For model desk: which model was used
    model_type: Optional[str] = None  # "bayesian", "economic_regression", "polling"
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────
# Edge Analysis (Kelly gate output)
# ─────────────────────────────────────────────
class EdgeAnalysis(SQLModel, table=True):
    """The final edge calculation and Kelly sizing for a market."""
    id: Optional[int] = Field(default=None, primary_key=True)
    
    market_id: int = Field(foreign_key="market.id")
    scan_id: str
    
    # Consensus probability
    system_probability: float      # Final estimate after debate/median
    market_price: float            # What the market says (YES price)
    
    # Edge calculation
    edge: float                    # system_probability - market_price
    expected_value: float          # EV per contract in dollars
    
    # Kelly sizing
    kelly_fraction: float          # Raw Kelly fraction
    half_kelly_fraction: float     # Half-Kelly (what we use)
    position_size_dollars: float   # Actual dollar amount
    num_contracts: int             # How many contracts to buy
    
    # Decision
    recommended_side: PositionSide # YES or NO
    tradeable: bool                # Did it pass the Kelly gate?
    rejection_reason: Optional[str] = None  # Why not tradeable
    
    # Debate metadata
    debate_triggered: bool = False
    debate_transcript: Optional[str] = None  # Full chatroom log
    estimates_divergence: float = 0.0  # Max - Min of agent estimates
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────
# Position (an actual bet we placed)
# ─────────────────────────────────────────────
class Position(SQLModel, table=True):
    """An active or closed position in a prediction market."""
    id: Optional[int] = Field(default=None, primary_key=True)
    
    market_id: int = Field(foreign_key="market.id")
    edge_analysis_id: int = Field(foreign_key="edgeanalysis.id")
    
    # Position details
    platform: Platform
    side: PositionSide             # YES or NO
    num_contracts: int
    entry_price: float             # Price per contract we paid
    total_cost: float              # Total dollars committed
    
    # Exit details (filled when position closes)
    exit_price: Optional[float] = None
    pnl_dollars: Optional[float] = None
    pnl_percent: Optional[float] = None
    
    # Status
    status: PositionStatus = PositionStatus.PENDING
    
    # Platform order ID for tracking
    platform_order_id: Optional[str] = None
    
    # Timing
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None


# ─────────────────────────────────────────────
# Calibration Record (post-resolution tracking)
# ─────────────────────────────────────────────
class CalibrationRecord(SQLModel, table=True):
    """Tracks prediction accuracy after markets resolve."""
    id: Optional[int] = Field(default=None, primary_key=True)
    
    market_id: int = Field(foreign_key="market.id")
    
    # What we predicted
    system_probability: float
    market_price_at_entry: float
    
    # What actually happened
    actual_outcome: bool           # True = YES, False = NO
    
    # Accuracy metrics
    brier_score: float             # (probability - outcome)^2, lower is better
    
    # Per-desk accuracy
    research_estimate: Optional[float] = None
    base_rate_estimate: Optional[float] = None
    model_estimate: Optional[float] = None
    
    # Category for per-category calibration
    category: MarketCategory
    
    resolved_at: datetime = Field(default_factory=datetime.utcnow)
```

---

## 3. API Endpoints (app/routes/)

### scanner.py
```
POST /scan/run
  → Triggers a full scan cycle
  → Returns: scan_id, num_markets_found, num_qualifying, num_tradeable

GET /scan/results/{scan_id}
  → Returns all EdgeAnalysis results for a scan cycle
  → Sorted by edge (highest mispricing first)

GET /scan/history
  → Returns list of past scan cycles with summary stats
```

### markets.py
```
GET /markets
  → List all tracked markets
  → Query params: platform, category, status, min_volume

GET /markets/{id}
  → Full market detail including price history and estimates

GET /markets/{id}/estimates
  → All probability estimates from all desks for this market
```

### positions.py
```
GET /positions
  → All positions (open and closed)
  → Query params: status, platform

GET /positions/summary
  → Portfolio summary: total invested, total PnL, win rate

POST /positions/{id}/close
  → Manually close a position early

GET /portfolio/daily-pnl
  → Today's P&L and kill-switch status
```

### calibration.py
```
GET /calibration
  → Overall Brier score, per-category Brier scores

GET /calibration/agents
  → Per-agent accuracy: which desk is best calibrated?

GET /calibration/chart
  → Calibration chart data: predicted probability bins vs actual outcomes
```

---

## 4. Agent Definitions

### Research Desk (agents/research_desk/researcher.py)

**Purpose:** For a given market question, search the web and synthesize relevant
information into a probability estimate.

**Tools:** Tavily web search, web scraping

**Prompt pattern:**
```
You are a research analyst estimating probabilities for prediction markets.

Market: "{market_title}"
Resolution criteria: "{market_description}"
Current market price: {yes_price} (implies {yes_price*100}% probability)

Your job:
1. Search for the most relevant, recent information about this question
2. Identify key factors that affect the outcome
3. Estimate the TRUE probability (0.00 to 1.00) based on your research
4. Do NOT anchor on the market price — form your own view

Respond with:
- probability: float (0.00 - 1.00)
- confidence: float (0.00 - 1.00, how confident you are in your estimate)
- reasoning: string (2-3 sentences explaining your estimate)
- key_sources: list of sources consulted
```

### Base Rate Desk (agents/base_rate_desk/base_rate.py)

**Purpose:** Compute historical base rates for similar events.

**Tools:** Web search for historical data, Python for computation

**Prompt pattern:**
```
You are a statistical analyst focused on base rates and historical frequencies.

Market: "{market_title}"
Category: {category}

Your job:
1. Find the historical base rate for this type of event
   - For economic data: "In the last N releases, how often did X exceed Y?"
   - For politics: "What is the base rate for incumbent party winning?"
   - For weather: "Historical frequency of this weather event"
2. Adjust for any known trend or structural change
3. Produce a probability based PURELY on historical frequencies

Do NOT use current news or sentiment. Only historical data.

Respond with:
- probability: float
- confidence: float
- reasoning: string
- sample_size: int (how many historical data points)
- base_rate_raw: float (unadjusted historical frequency)
```

### Model Desk (agents/model_desk/)

**Purpose:** Run calibrated mathematical models specific to the market category.

**For economics markets (economic_model.py):**
- Pull leading indicators (PPI, employment, shelter costs for CPI markets)
- Run simple regression or threshold analysis
- Output: probability based on model

**For political markets (polling_model.py):**
- Pull polling averages
- Apply historical polling error distribution
- Output: probability based on polls + error model

**For general markets (statistical_model.py):**
- Bayesian estimation with available priors
- Simple trend extrapolation
- Output: probability based on statistical model

### Debate Chatroom (agents/debate/chatroom.py)

**Purpose:** When agent estimates diverge by >10 percentage points, agents
enter a shared conversation to argue and converge.

**Structure:**
```python
# State for the debate
class DebateState(TypedDict):
    market: Market
    estimates: dict[str, float]      # desk_name → probability
    transcript: list[dict]           # {agent, message, round}
    round: int
    converged: bool
    consensus_probability: float

# Debate flow:
# Round 1: Each agent posts their estimate + reasoning (no responses)
# Round 2: Each agent must critique one other agent's estimate
# Round 3+: Open debate, agents defend or concede
# Max 5 rounds, then moderator forces consensus
# Convergence: all estimates within 5 percentage points
```

**Moderator agent** enters if max rounds hit:
- Picks the most conservative estimate (safety-first)
- OR takes weighted average based on confidence scores
- Records dissent in the transcript

---

## 5. Scanner Service (app/services/scanner_service.py)

```python
async def run_scan() -> ScanResult:
    """
    Full scan cycle:
    1. Fetch all active markets from Kalshi and Polymarket
    2. Filter by criteria (volume, spread, days to expiry)
    3. For each qualifying market:
       a. Run all three desks in parallel
       b. Check divergence
       c. If divergence > 10%: run debate
       d. Compute final probability
       e. Compare to market price
       f. Run Kelly gate
       g. If tradeable: place limit order
    4. Store all results
    5. Return summary
    """
```

**Filtering criteria:**
- `volume_24h >= MIN_MARKET_VOLUME` (default: 200 contracts)
- `days_to_expiry <= MAX_DAYS_TO_EXPIRY` (default: 30 days)
- `spread <= 0.15` (don't trade in illiquid markets)
- `status == ACTIVE`
- Not already in our position book

---

## 6. Kalshi Client (app/services/kalshi_client.py)

```python
class KalshiClient:
    """Wrapper around Kalshi's REST API."""
    
    BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
    DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"
    
    async def get_markets(
        self,
        status: str = "open",
        series_ticker: str = None,
        limit: int = 100,
        cursor: str = None
    ) -> list[dict]:
        """Fetch active markets. No auth needed for public data."""
    
    async def get_market(self, ticker: str) -> dict:
        """Get single market detail."""
    
    async def get_orderbook(self, ticker: str) -> dict:
        """Get current orderbook (bid/ask depth)."""
    
    async def get_event(self, event_ticker: str) -> dict:
        """Get event with all child markets."""
    
    async def get_market_history(
        self, ticker: str, limit: int = 100
    ) -> list[dict]:
        """Get price history for a market."""
    
    # --- Authenticated (for trading) ---
    
    async def place_order(
        self,
        ticker: str,
        side: str,       # "yes" or "no"
        action: str,     # "buy" or "sell"
        count: int,
        price: int,      # In cents (1-99)
        order_type: str = "limit"
    ) -> dict:
        """Place a limit order. ALWAYS use limit, never market."""
    
    async def get_positions(self) -> list[dict]:
        """Get current open positions."""
    
    async def get_balance(self) -> float:
        """Get available balance."""
```

**Auth:** RSA signature-based. Private key in PEM file.
**Key package:** `kalshi-python` or raw `httpx` with crypto signing.

---

## 7. Polymarket Client (app/services/polymarket_client.py)

```python
class PolymarketClient:
    """Wrapper around Polymarket's CLOB API."""
    
    CLOB_URL = "https://clob.polymarket.com"
    GAMMA_URL = "https://gamma-api.polymarket.com"
    
    async def get_markets(
        self,
        active: bool = True,
        limit: int = 100,
        offset: int = 0
    ) -> list[dict]:
        """Fetch active markets from Gamma API."""
    
    async def get_orderbook(self, token_id: str) -> dict:
        """Get current orderbook for a token."""
    
    async def get_market_trades(
        self, condition_id: str, limit: int = 100
    ) -> list[dict]:
        """Get recent trades for a market."""
    
    # --- Authenticated (for trading) ---
    
    async def place_order(
        self,
        token_id: str,
        side: str,       # "BUY" or "SELL"
        price: float,    # 0.01 - 0.99
        size: float,     # Number of contracts
    ) -> dict:
        """Place a limit order via CLOB API."""
    
    async def get_positions(self) -> list[dict]:
        """Get current open positions."""
```

**Auth:** EIP-712 wallet signature on Polygon network.
**Key package:** `py-clob-client` or `polymarket-apis`

---

## 8. Edge Calculation & Kelly Gate

```python
def calculate_edge(
    system_probability: float,
    market_price: float,
    bankroll: float,
    max_position_pct: float = 0.05,
    min_edge: float = 0.05
) -> EdgeAnalysis:
    """
    Core edge and Kelly calculation for a prediction market contract.
    
    A YES contract costs `market_price` and pays $1.00 if YES.
    A NO contract costs `(1 - market_price)` and pays $1.00 if NO.
    
    For a YES bet:
        p_win = system_probability
        profit_if_win = 1.0 - market_price   (per contract)
        loss_if_lose = market_price           (per contract)
        
    For a NO bet:
        p_win = 1.0 - system_probability
        profit_if_win = market_price          (per contract)
        loss_if_lose = 1.0 - market_price     (per contract)
    
    Choose YES if system_probability > market_price (we think YES is underpriced)
    Choose NO if system_probability < market_price (we think NO is underpriced)
    
    Edge = |system_probability - market_price|
    If edge < min_edge: not tradeable (noise, not signal)
    
    Kelly fraction = edge / loss_if_lose
    Half-Kelly = kelly_fraction / 2
    Position = min(half_kelly * bankroll, max_position_pct * bankroll)
    """
```

This function should CALL the existing `math_utils.py` functions internally.
The EV formula and Kelly formula are already implemented — just map the
prediction market inputs (contract price, system probability) to the
existing function parameters (p_win, profit_pct, loss_pct).

---

## 9. Frontend Dashboard (frontend/app.py)

### Page 1: Setup Board (default view)
- Table of all markets scanned in latest cycle
- Columns: Market Title, Category, Market Price, System Estimate, Edge, Kelly Size, Status
- Color coding: Green = tradeable (positive EV), Red = rejected, Yellow = watching
- Click a row to see full detail

### Page 2: Active Positions
- All open positions with current P&L
- Days remaining until resolution
- Exit conditions
- Daily drawdown status (kill-switch indicator)

### Page 3: Debate Logs
- For markets that triggered a debate, show the full chatroom transcript
- Agent names color-coded
- Final consensus highlighted

### Page 4: Calibration
- Overall Brier score (lower = better, 0 = perfect)
- Calibration chart: 10 bins of predicted probability vs actual outcome frequency
- Per-category breakdown
- Per-agent accuracy ranking

### Page 5: Run Scanner
- Manual trigger button for a scan cycle
- Configuration overrides (min volume, max expiry, etc.)
- Real-time progress display

---

## 10. Dependencies (requirements.txt)

```
# API Framework
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
pydantic>=2.0
pydantic-settings>=2.0

# Database
sqlmodel>=0.0.14
aiosqlite>=0.19.0

# AI / Agents
langgraph>=0.2.0
smolagents>=1.0.0
litellm>=1.0.0

# Search
tavily-python>=0.3.0

# Market Data Clients
httpx>=0.25.0                    # Async HTTP for Kalshi API
py-clob-client>=0.1.0           # Polymarket CLOB client
web3>=6.11.0                    # For Polymarket wallet signing
cryptography>=41.0.0            # For Kalshi RSA auth

# Data
pandas>=2.0.0

# Dashboard
streamlit>=1.28.0

# Utilities
python-dotenv>=1.0.0
apscheduler>=3.10.0              # For scheduled scanning
```

---

## 11. Migration Checklist

Use this checklist to track progress. Each item maps to a specific file change.

```
[ ] Phase 1: Foundation
    [ ] Update core/config.py — new env vars (remove ALPACA_*, add KALSHI_*, POLY_*, SCANNER_*)
    [ ] Update core/constants.py — add MAX_CONCURRENT_POSITIONS, MIN_EDGE_THRESHOLD, etc.
    [ ] Rewrite database/models.py — replace all models with spec above
    [ ] Create app/services/kalshi_client.py
    [ ] Create app/services/polymarket_client.py  
    [ ] Create app/routes/markets.py
    [ ] Update app/main.py — swap router imports
    [ ] Update requirements.txt
    [ ] Update .env.example

[ ] Phase 2: Scanner
    [ ] Create app/services/scanner_service.py
    [ ] Create app/routes/scanner.py
    [ ] Test: scanner pulls and filters live markets

[ ] Phase 3: Probability Estimation
    [ ] Create agents/research_desk/researcher.py
    [ ] Create agents/base_rate_desk/base_rate.py
    [ ] Create agents/model_desk/statistical_model.py
    [ ] Create agents/model_desk/economic_model.py
    [ ] Rewrite app/services/agent_orchestrator.py — new LangGraph graph
    [ ] Test: agents produce probability estimates for a market

[ ] Phase 4: Debate
    [ ] Create agents/debate/chatroom.py
    [ ] Wire into orchestrator with divergence trigger
    [ ] Test: debate fires when agents disagree >10%

[ ] Phase 5: Kelly + Execution
    [ ] Create edge calculation wrapper using existing math_utils
    [ ] Wire execution into scanner pipeline
    [ ] Create app/routes/positions.py
    [ ] Test: end-to-end scan → estimate → Kelly → order

[ ] Phase 6: Dashboard + Calibration
    [ ] Rewrite frontend/app.py
    [ ] Create app/routes/calibration.py
    [ ] Test: full system on demo/paper accounts

[ ] Phase 7: Cleanup
    [ ] Delete app/services/alpaca.py
    [ ] Delete app/routes/trades.py (old)
    [ ] Delete app/routes/portfolio.py (old)
    [ ] Remove all equity-trading references from codebase
    [ ] Update README.md
```