1. Vision: Autonomous system using OpenClaw/Moltbot & FastAPI. 24/7 cycle. Math-based (EV & Kelly Criterion), not vibes.
2. Architecture:
   - Orchestration: OpenClaw (Skill architecture + Heartbeat scheduler).
   - Backend: FastAPI for data ingestion/orders.
   - DB: SQLModel (SQLite) for Agent State, Audit Trails, Daily P&L.
   - API: Alpaca (Paper Trading).
3. Math Logic:
   - EV Calculation: (P_win * Profit) - (P_loss * Loss). Trade only if EV > 0.
   - Sizing: Half-Kelly Criterion.
4. Data: Alpaca (Price), NewsAPI (Context), Sentiment Analysis.
5. Workflow: Sense (Heartbeat) -> Think (LLM P_win) -> Model (Math) -> Act (Limit Order) -> Report (Telegram).
6. Safety: Stop-Loss (-5%), Daily Drawdown (-2% Kill-Switch), Market Hours only.
