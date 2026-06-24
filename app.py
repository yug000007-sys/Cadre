import streamlit as st

st.set_page_config(
    page_title="Cadre Quote Agent",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
[data-testid="collapsedControl"] { display: none; }
#MainMenu, footer { visibility: hidden; }
.block-container { padding-top: 1.5rem !important; }
</style>
""", unsafe_allow_html=True)

from utils.state import init_state
init_state()

st.markdown("## ⚡ Cadre Quote Agent")
st.markdown("---")

tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Dashboard",
    "📤 Upload Quotes",
    "📬 Inbox Monitor",
    "⚙️ Settings",
])

with tab1:
    from pages.dashboard import render; render()
with tab2:
    from pages.upload import render; render()
with tab3:
    from pages.monitor import render; render()
with tab4:
    from pages.settings import render; render()
