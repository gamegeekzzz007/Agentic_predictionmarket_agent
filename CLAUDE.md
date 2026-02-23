# CLAUDE.md - Agentic Trading

## ⚡ Quick Commands
- **Run App:** `uvicorn app.main:app --reload`
- **Run Tests:** `pytest`
- **Install:** `pip install -r requirements.txt`
- **Database:** `python -m database.init_db` (Future command)

## 🧠 Core Principles
1.  **Math is Law:** Logic MUST use `core/math_utils.py` for trade decisions. No "vibes."
2.  **Safety First:**
    * Never commit API keys. Use `os.getenv`.
    * Hard-coded Stop-Loss (-5%) and Drawdown Limit (-2%).
3.  **Simple Stack:**
    * FastAPI for the backend.
    * SQLModel for the database.
    * Alpaca SDK (`alpaca-py`) for trading.

## 📂 Architecture Map
* `core/`: The brain (Math, Config, Constants).
* `app/`: The body (API routes, Endpoints).
* `agents/`: The hands (Execution logic, OpenClaw skills).
* `database/`: The memory (Models, DB connection).

## 🛠️ Style Guide
* **Type Hints:** Always use strict typing (e.g., `def func(x: int) -> bool:`).
* **Async:** Use `async/await` for all external calls (DB, APIs).
* **No Fluff:** Keep functions small and single-purpose.
