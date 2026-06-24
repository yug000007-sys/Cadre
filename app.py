"""
Cadre Quote Processing Agent — Streamlit App
Entry point: streamlit run app.py
"""

import streamlit as st

st.set_page_config(
    page_title="Cadre Quote Agent",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Hide default sidebar toggle & pad top ────────────────────────────────────
st.markdown("""
<style>
[data-testid="collapsedControl"] { display: none; }
#MainMenu, footer { visibility: hidden; }
.block-container { padding-top: 1rem !important; }
div[data-testid="stTabs"] button { font-size: 15px; font-weight: 500; padding: 8px 22px; }
</style>
""", unsafe_allow_html=True)

# ── Logo / title row ──────────────────────────────────────────────────────────
col_logo, col_title, col_status = st.columns([1, 6, 2])
with col_logo:
    st.markdown("## ⚡")
with col_title:
    st.markdown("## Cadre Quote Agent")
with col_status:
    from utils.state import get_agent_status, init_state
    init_state()
    status = get_agent_status()
    color  = "🟢" if status == "running" else "🔴"
    st.markdown(f"<div style='text-align:right;margin-top:14px;font-size:13px;color:gray'>Inbox Monitor: {color} {status.capitalize()}</div>", unsafe_allow_html=True)

st.markdown("---")

# ── Top horizontal tab navigation ────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "📊  Dashboard",
    "📤  Upload Quotes",
    "📬  Inbox Monitor",
    "⚙️  Settings",
])

with tab1:
    from pages.dashboard import render as dashboard
    dashboard()

with tab2:
    from pages.upload import render as upload
    upload()

with tab3:
    from pages.monitor import render as monitor
    monitor()

with tab4:
    from pages.settings import render as settings
    settings()
