"""
Cadre Quote Processing Agent — Streamlit App
Entry point: streamlit run app.py
"""

import streamlit as st

st.set_page_config(
    page_title="Cadre Quote Agent",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar navigation ────────────────────────────────────────────────────────
with st.sidebar:
    st.image("assets/logo.png", use_container_width=True) if __import__("os").path.exists("assets/logo.png") else st.title("⚡ Cadre Agent")
    st.markdown("---")
    st.caption("Navigation")
    page = st.radio(
        label="Go to",
        options=["📊 Dashboard", "📤 Upload Quotes", "📬 Inbox Monitor", "⚙️ Settings"],
        label_visibility="collapsed",
    )
    st.markdown("---")

    # Quick status indicator
    from utils.state import get_agent_status
    status = get_agent_status()
    color = "🟢" if status == "running" else "🔴"
    st.caption(f"Inbox Monitor: {color} {status.capitalize()}")

# ── Page routing ──────────────────────────────────────────────────────────────
if page == "📊 Dashboard":
    from pages.dashboard import render
    render()
elif page == "📤 Upload Quotes":
    from pages.upload import render
    render()
elif page == "📬 Inbox Monitor":
    from pages.monitor import render
    render()
elif page == "⚙️ Settings":
    from pages.settings import render
    render()
