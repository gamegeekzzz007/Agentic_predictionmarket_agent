"""
frontend/app.py
Streamlit dashboard for the Agentic Trading multi-agent pipeline.

Run:  streamlit run frontend/app.py
"""

import requests
import streamlit as st

API_URL = "http://127.0.0.1:8000/run-agents"
TIMEOUT_SECONDS = 300

st.set_page_config(page_title="AI Hedge Fund", layout="wide")
st.title("Agentic Trading Dashboard")
st.subheader("Live Multi-Agent Reasoning Loop")

# ---------------------------------------------------------------------------
# Trigger button
# ---------------------------------------------------------------------------

if st.button("Run AI Research Loop", type="primary", use_container_width=True):
    with st.spinner("Running 4-agent pipeline... (this may take 1-3 minutes)"):
        try:
            resp = requests.post(API_URL, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            st.session_state["result"] = resp.json()
            st.session_state["error"] = None
        except requests.ConnectionError:
            st.session_state["error"] = (
                "Could not connect to the backend. "
                "Make sure it is running: `uvicorn app.main:app --reload`"
            )
            st.session_state["result"] = None
        except requests.Timeout:
            st.session_state["error"] = (
                f"Request timed out after {TIMEOUT_SECONDS}s. "
                "The orchestrator may still be running."
            )
            st.session_state["result"] = None
        except requests.HTTPError as exc:
            # Extract JSON detail if available, otherwise show raw text
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except Exception:
                detail = exc.response.text
            st.session_state["error"] = f"Backend error ({exc.response.status_code}): {detail}"
            st.session_state["result"] = None

# ---------------------------------------------------------------------------
# Display errors
# ---------------------------------------------------------------------------

if st.session_state.get("error"):
    st.error(st.session_state["error"])

# ---------------------------------------------------------------------------
# Display results (persisted in session_state)
# ---------------------------------------------------------------------------

result = st.session_state.get("result")
if result is None:
    st.info("Click the button above to start the AI research loop.")
    st.stop()

# ---- Section 1: The Catalyst ----
st.markdown("---")
st.header("1. The Catalyst")
st.info(result.get("news_catalyst", "No catalyst found."))

# ---- Section 2: The Brain ----
st.markdown("---")
st.header("2. The Brain")
col_thesis, col_facts = st.columns(2)

with col_thesis:
    st.subheader("Macro Thesis")
    theses = result.get("theses", [])
    if theses:
        for i, thesis in enumerate(theses, 1):
            st.markdown(f"**Thesis {i}:** {thesis}")
    else:
        st.warning("No theses generated.")

with col_facts:
    st.subheader("Fact Check")
    facts = result.get("verified_facts", [])
    if facts:
        for fact in facts:
            upper_fact = fact.upper()
            if "VERIFIED" in upper_fact:
                st.success(fact)
            elif "FALSE" in upper_fact:
                st.error(fact)
            else:
                st.warning(fact)
    else:
        st.warning("No fact-check results.")

# ---- Section 3: The Quant ----
st.markdown("---")
st.header("3. The Quant")

backtest = result.get("backtest_results", {})
ev_analysis = result.get("ev_analysis")

if backtest.get("raw_output") and not backtest.get("ticker"):
    # Fallback: could not parse structured fields
    st.warning("Backtest output could not be parsed into structured fields.")
    st.code(backtest["raw_output"], language="text")
else:
    # Structured quant metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Ticker", backtest.get("ticker", "N/A"))
    m2.metric("Win Rate", f"{(backtest.get('p_win') or 0) * 100:.1f}%")
    m3.metric("EV", f"{ev_analysis['ev']:.4f}" if ev_analysis else "N/A")
    m4.metric("Side", (backtest.get("side") or "N/A").upper())

    if ev_analysis:
        if ev_analysis["tradeable"]:
            st.success(
                f"TRADE APPROVED — EV = {ev_analysis['ev']:.4f} | "
                f"Kelly position size: {ev_analysis['position_pct'] * 100:.1f}% of portfolio"
            )
        else:
            st.error(
                f"TRADE REJECTED — Negative EV = {ev_analysis['ev']:.4f} | No edge detected."
            )
    else:
        st.warning("Could not compute EV analysis (missing quant fields).")

    # Reasoning expander
    reasoning = backtest.get("reasoning")
    if reasoning:
        with st.expander("Quant Reasoning"):
            st.write(reasoning)
