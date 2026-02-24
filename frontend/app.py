"""
frontend/app.py
Streamlit dashboard for the Agentic Prediction Market system.

5 pages: Setup Board | Active Positions | Debate Logs | Calibration | Run Scanner

Run:  streamlit run frontend/app.py
"""

import os

import pandas as pd
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
TIMEOUT = 30
SCAN_TIMEOUT = 300

st.set_page_config(
    page_title="Prediction Market Agent",
    page_icon="📊",
    layout="wide",
)


# ------------------------------------------------------------------
# API helpers
# ------------------------------------------------------------------

def api_get(path: str, timeout: int = TIMEOUT, **params) -> dict | list | None:
    """GET from the FastAPI backend. Returns parsed JSON or None on error."""
    try:
        resp = requests.get(f"{API_BASE}{path}", params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        st.error("Cannot connect to backend. Run: `uvicorn app.main:app --reload`")
        return None
    except requests.HTTPError as exc:
        st.error(f"API error {exc.response.status_code}: {exc.response.text[:300]}")
        return None
    except requests.Timeout:
        st.error(f"Request timed out after {timeout}s.")
        return None


def api_post(path: str, timeout: int = TIMEOUT, **params) -> dict | None:
    """POST to the FastAPI backend."""
    try:
        resp = requests.post(f"{API_BASE}{path}", params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        st.error("Cannot connect to backend. Run: `uvicorn app.main:app --reload`")
        return None
    except requests.HTTPError as exc:
        st.error(f"API error {exc.response.status_code}: {exc.response.text[:300]}")
        return None
    except requests.Timeout:
        st.error(f"Request timed out after {timeout}s.")
        return None


# ------------------------------------------------------------------
# Page 1: Setup Board
# ------------------------------------------------------------------

def page_setup_board():
    st.header("Setup Board")
    st.caption("All qualifying markets from the latest scan cycle")

    data = api_get("/scan/results")
    if data is None:
        return

    markets = data.get("markets", [])
    if not markets:
        st.info("No markets found. Run a scan first from the Scanner page.")
        return

    # Build dataframe
    rows = []
    for m in markets:
        rows.append({
            "ID": m["id"],
            "Title": m["title"],
            "Category": m["category"].title(),
            "Platform": m["platform"].title(),
            "YES Price": m["yes_price"],
            "Spread": m["spread"],
            "Volume (24h)": f"${m['volume_24h']:,}",
            "Expiry (days)": m["days_to_expiry"] if m["days_to_expiry"] else "-",
        })

    df = pd.DataFrame(rows)

    # Filters
    col1, col2 = st.columns(2)
    with col1:
        cat_filter = st.selectbox(
            "Filter by category",
            ["All"] + sorted(df["Category"].unique().tolist()),
        )
    with col2:
        plat_filter = st.selectbox(
            "Filter by platform",
            ["All"] + sorted(df["Platform"].unique().tolist()),
        )

    if cat_filter != "All":
        df = df[df["Category"] == cat_filter]
    if plat_filter != "All":
        df = df[df["Platform"] == plat_filter]

    st.metric("Qualifying Markets", len(df))
    st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        column_config={
            "YES Price": st.column_config.NumberColumn(format="%.4f"),
            "Spread": st.column_config.NumberColumn(format="%.3f"),
        },
    )

    # --- Analyze a market ---
    st.divider()
    st.subheader("Analyze a Market")
    st.caption(
        "Pick a market to run the full pipeline: "
        "3 agent desks estimate probability -> debate if needed -> Kelly gate"
    )

    market_options = {f"{m['id']}: {m['title'][:60]}" : m["id"] for m in markets}
    selected = st.selectbox("Select market", list(market_options.keys()))
    selected_id = market_options[selected]

    col_analyze, col_execute = st.columns([3, 1])
    with col_analyze:
        analyze_btn = st.button("Analyze (estimate only)", type="primary", width="stretch")
    with col_execute:
        execute_btn = st.button("Analyze + Execute", type="secondary", width="stretch")

    if analyze_btn or execute_btn:
        execute_flag = execute_btn
        with st.spinner("Running 3 agent desks... this takes 1-3 minutes"):
            result = api_post(
                f"/analyze/{selected_id}",
                timeout=SCAN_TIMEOUT,
                execute="true" if execute_flag else "false",
            )
        if result:
            st.session_state["last_analysis"] = result

            # Store debate data if triggered
            if result.get("debate_triggered"):
                if "debate_results" not in st.session_state:
                    st.session_state["debate_results"] = []
                st.session_state["debate_results"].append({
                    "market_title": result["market_title"],
                    "divergence": result["divergence"],
                    "consensus_probability": result["system_probability"],
                    "transcript": result.get("debate_transcript", []),
                    "rounds_used": result.get("debate_rounds"),
                    "converged": result.get("debate_converged"),
                })

    # Display analysis results
    if "last_analysis" in st.session_state:
        r = st.session_state["last_analysis"]
        st.divider()
        st.subheader(f"Analysis: {r['market_title']}")

        # Agent estimates
        m1, m2, m3 = st.columns(3)
        m1.metric("Market Price", f"{r['market_price']:.3f}")
        m2.metric("System Estimate", f"{r['system_probability']:.3f}")
        m3.metric("Edge", f"{r['edge']:.3f}")

        # Desk breakdown
        if r.get("estimates"):
            st.caption("Agent desk estimates:")
            for est in r["estimates"]:
                st.markdown(
                    f"- **{est['desk'].replace('_', ' ').title()}**: "
                    f"{est['probability']:.3f} (confidence: {est['confidence']:.2f})"
                )

        if r.get("debate_triggered"):
            st.warning(f"Debate triggered (divergence: {r['divergence']:.3f})")
        st.caption(f"Reasoning: {r['consensus_reasoning']}")

        # Kelly gate result
        st.divider()
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Side", r["recommended_side"].upper())
        k2.metric("EV", f"{r['expected_value']:.4f}")
        k3.metric("Kelly", f"{r['half_kelly_fraction']:.4f}")
        k4.metric("Contracts", r["num_contracts"])

        if r["tradeable"]:
            st.success(
                f"TRADEABLE — ${r['position_size_dollars']:.2f} position | "
                f"{r['num_contracts']} contracts | Side: {r['recommended_side'].upper()}"
            )
            if r.get("order_placed"):
                st.success(f"Order placed! Position ID: {r['position_id']}")
        else:
            st.error(f"REJECTED — {r.get('rejection_reason', 'No edge')}")


# ------------------------------------------------------------------
# Page 2: Active Positions
# ------------------------------------------------------------------

def page_positions():
    st.header("Active Positions")

    # Summary metrics
    summary = api_get("/positions/summary")
    daily = api_get("/positions/daily-pnl")

    if summary:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Positions", summary["total_positions"])
        c2.metric("Open", summary["open_positions"])
        c3.metric("Total P&L", f"${summary['total_pnl']:+.2f}")
        c4.metric(
            "Win Rate",
            f"{summary['win_rate'] * 100:.1f}%" if summary["win_rate"] is not None else "N/A",
        )

    if daily:
        st.divider()
        d1, d2, d3 = st.columns(3)
        d1.metric("Today's P&L", f"${daily['realized_pnl']:+.2f}")
        d2.metric("Open Positions", daily["open_positions"])

        if daily["kill_switch_active"]:
            d3.error("KILL SWITCH ACTIVE")
        else:
            d3.success(f"Drawdown limit: {daily['drawdown_limit_pct']}%")

    st.divider()

    # Positions table
    status_filter = st.selectbox(
        "Filter by status",
        ["All", "open", "pending", "closed_win", "closed_loss", "closed_early"],
    )

    params = {}
    if status_filter != "All":
        params["status"] = status_filter

    data = api_get("/positions", **params)
    if data is None:
        return

    positions = data.get("positions", [])
    if not positions:
        st.info("No positions found.")
        return

    rows = []
    for p in positions:
        pnl_str = f"${p['pnl_dollars']:+.2f}" if p["pnl_dollars"] is not None else "-"
        pnl_pct = f"{p['pnl_percent']:+.1f}%" if p["pnl_percent"] is not None else "-"
        rows.append({
            "ID": p["id"],
            "Market": p["market_id"],
            "Platform": p["platform"].title(),
            "Side": p["side"].upper(),
            "Contracts": p["num_contracts"],
            "Entry": p["entry_price"],
            "Cost": f"${p['total_cost']:.2f}",
            "P&L ($)": pnl_str,
            "P&L (%)": pnl_pct,
            "Status": p["status"].replace("_", " ").title(),
            "Opened": p["opened_at"][:19] if p["opened_at"] else "-",
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True)

    # Manual close
    st.divider()
    with st.expander("Close a Position Manually"):
        pos_id = st.number_input("Position ID", min_value=1, step=1)
        exit_price = st.number_input("Exit Price (0-1)", min_value=0.0, max_value=1.0, step=0.01)
        if st.button("Close Position"):
            result = api_post(f"/positions/{pos_id}/close", exit_price=exit_price)
            if result:
                st.success(
                    f"Position {pos_id} closed. "
                    f"P&L: ${result.get('pnl_dollars', 0):+.2f}"
                )
                st.rerun()


# ------------------------------------------------------------------
# Page 3: Debate Logs
# ------------------------------------------------------------------

def _render_debate_transcript(transcript, consensus_probability=None, converged=None):
    """Render a debate transcript (list of entries) as Streamlit markdown."""
    if not transcript:
        st.caption("No transcript available.")
        return

    # Handle transcript as list of dicts or as a plain string
    if isinstance(transcript, str):
        import json as _json
        try:
            transcript = _json.loads(transcript)
        except (ValueError, TypeError):
            st.text(transcript)
            return

    if not isinstance(transcript, list):
        st.text(str(transcript))
        return

    for entry in transcript:
        if not isinstance(entry, dict):
            st.text(str(entry))
            continue

        agent = entry.get("agent", "unknown")
        round_num = entry.get("round", 0)
        msg_type = entry.get("type", "")
        message = entry.get("message", "")

        colors = {
            "research_desk": "blue",
            "base_rate_desk": "green",
            "model_desk": "orange",
            "moderator": "red",
        }
        color = colors.get(agent, "gray")

        st.markdown(
            f"**Round {round_num}** | "
            f":{color}[**{agent}**] ({msg_type})"
        )
        st.markdown(f"> {message}")
        if "updated_probability" in entry:
            st.markdown(
                f"Updated estimate: **{entry['updated_probability']:.3f}**"
            )
        st.markdown("---")

    if consensus_probability is not None:
        st.success(
            f"Final consensus: **{consensus_probability:.3f}** "
            f"(converged: {converged if converged is not None else 'N/A'})"
        )


def page_debate_logs():
    st.header("Debate Logs")
    st.caption("Agent debates triggered when estimate divergence exceeds 10 percentage points")

    # --- Load persisted debates from API ---
    api_debates = api_get("/analyze/debates")
    has_api_debates = api_debates and api_debates.get("debates")

    # --- Session-state debates (current session only) ---
    session_debates = st.session_state.get("debate_results", [])

    if not has_api_debates and not session_debates:
        st.info(
            "No debate logs yet. Run a probability estimation on a market "
            "where agent desks diverge by more than 10 percentage points."
        )
        return

    # --- Show persisted debates from DB ---
    if has_api_debates:
        st.subheader(f"Saved Debates ({api_debates['count']})")
        for debate in api_debates["debates"]:
            with st.expander(
                f"{debate.get('market_title', 'Unknown')} "
                f"(divergence: {debate.get('estimates_divergence', 0):.1%}) — "
                f"{debate.get('created_at', '')[:19]}"
            ):
                st.markdown(
                    f"**System probability:** {debate['system_probability']:.3f} | "
                    f"**Market price:** {debate['market_price']:.3f}"
                )
                _render_debate_transcript(debate.get("debate_transcript"))

    # --- Show current-session debates ---
    if session_debates:
        st.subheader(f"Current Session Debates ({len(session_debates)})")
        for debate in session_debates:
            with st.expander(
                f"{debate.get('market_title', 'Unknown')} "
                f"(divergence: {debate.get('divergence', 0):.1%})"
            ):
                _render_debate_transcript(
                    debate.get("transcript", []),
                    consensus_probability=debate.get("consensus_probability"),
                    converged=debate.get("converged"),
                )


# ------------------------------------------------------------------
# Page 4: Calibration
# ------------------------------------------------------------------

def page_calibration():
    st.header("Calibration & Accuracy")
    st.caption("How well are our probability estimates matching reality?")

    # Overall calibration
    overview = api_get("/calibration")
    if overview is None:
        return

    if overview["num_resolved_markets"] == 0:
        st.info(
            "No resolved markets yet. Calibration data will appear "
            "after markets resolve and outcomes are recorded."
        )
        return

    # Top-level metrics
    c1, c2 = st.columns(2)
    c1.metric(
        "Overall Brier Score",
        f"{overview['overall_brier_score']:.4f}" if overview["overall_brier_score"] else "N/A",
        help="Lower is better. 0 = perfect, 0.25 = coin flip, 1.0 = always wrong",
    )
    c2.metric("Resolved Markets", overview["num_resolved_markets"])

    # Per-category breakdown
    if overview["per_category_scores"]:
        st.subheader("Brier Score by Category")
        cat_df = pd.DataFrame([
            {"Category": cat.title(), "Brier Score": score}
            for cat, score in overview["per_category_scores"].items()
        ])
        st.dataframe(cat_df, width="stretch", hide_index=True)

    st.divider()

    # Agent calibration
    agents_data = api_get("/calibration/agents")
    if agents_data:
        st.subheader("Per-Agent Accuracy")
        agents = agents_data.get("agents", [])
        if agents:
            cols = st.columns(len(agents))
            for col, agent in zip(cols, agents):
                with col:
                    st.markdown(f"**{agent['agent_name'].replace('_', ' ').title()}**")
                    if agent["brier_score"] is not None:
                        st.metric("Brier Score", f"{agent['brier_score']:.4f}")
                    else:
                        st.metric("Brier Score", "N/A")
                    st.caption(f"Predictions: {agent['num_predictions']}")
                    trend = agent["calibration_trend"]
                    if trend == "improving":
                        st.success(f"Trend: {trend}")
                    elif trend == "degrading":
                        st.error(f"Trend: {trend}")
                    else:
                        st.info(f"Trend: {trend}")
                    if agent["recent_accuracy"] is not None:
                        st.caption(f"Recent accuracy: {agent['recent_accuracy']:.1%}")

    st.divider()

    # Calibration chart
    chart_data = api_get("/calibration/chart")
    if chart_data and chart_data["total_predictions"] > 0:
        st.subheader("Calibration Chart")
        st.caption("Perfect calibration = dots on the diagonal line")

        bins = chart_data["bins"]
        chart_rows = []
        for b in bins:
            if b["predicted_avg"] is not None and b["actual_frequency"] is not None:
                midpoint = (b["bin_lower"] + b["bin_upper"]) / 2
                chart_rows.append({
                    "Bin": f"{b['bin_lower']:.1f}-{b['bin_upper']:.1f}",
                    "Predicted": b["predicted_avg"],
                    "Actual": b["actual_frequency"],
                    "Count": b["count"],
                    "Midpoint": midpoint,
                })

        if chart_rows:
            chart_df = pd.DataFrame(chart_rows)

            # Line chart: predicted vs actual
            plot_df = chart_df.set_index("Bin")[["Predicted", "Actual"]]
            st.line_chart(plot_df)

            # Raw data table
            with st.expander("Raw Calibration Data"):
                st.dataframe(chart_df, width="stretch", hide_index=True)


# ------------------------------------------------------------------
# Page 5: Run Scanner
# ------------------------------------------------------------------

def page_scanner():
    st.header("Run Scanner")
    st.caption("Trigger a market scan and view results")

    # Scan history
    history = api_get("/scan/history")
    if history:
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Active Markets", history["total_markets"])
        c2.metric("Platforms", ", ".join(history.get("platforms", {}).keys()) or "None")
        c3.metric("Last Snapshot", history["timestamp"][:19])

        if history.get("categories"):
            st.caption("Markets by category:")
            cat_cols = st.columns(min(len(history["categories"]), 6))
            for col, (cat, count) in zip(cat_cols, history["categories"].items()):
                col.metric(cat.title(), count)

    st.divider()

    # Scan trigger
    st.subheader("New Scan")

    if st.button("Run Full Scan", type="primary", width="stretch"):
        with st.spinner("Scanning markets... this may take 1-2 minutes"):
            result = api_post("/scan/run", timeout=SCAN_TIMEOUT)
            if result:
                st.session_state["last_scan"] = result
                st.success(
                    f"Scan complete! ID: {result['scan_id']} | "
                    f"Fetched: {result['total_fetched']} | "
                    f"Qualifying: {result['qualifying']} | "
                    f"New: {result['new_markets']} | "
                    f"Updated: {result['updated_markets']}"
                )
                if result.get("errors"):
                    for err in result["errors"]:
                        st.warning(f"Scan error: {err}")
                st.rerun()

    # Show last scan results
    if "last_scan" in st.session_state:
        scan = st.session_state["last_scan"]
        st.info(
            f"Last scan: {scan['scan_id']} | "
            f"{scan['qualifying']} qualifying markets"
        )


# ------------------------------------------------------------------
# Navigation
# ------------------------------------------------------------------

PAGES = {
    "Setup Board": page_setup_board,
    "Active Positions": page_positions,
    "Debate Logs": page_debate_logs,
    "Calibration": page_calibration,
    "Run Scanner": page_scanner,
}

st.sidebar.title("Prediction Market Agent")
st.sidebar.markdown("---")
selection = st.sidebar.radio("Navigate", list(PAGES.keys()))

# Run selected page
PAGES[selection]()

# Footer
st.sidebar.markdown("---")
st.sidebar.caption("v2.0 | Prediction Markets")
