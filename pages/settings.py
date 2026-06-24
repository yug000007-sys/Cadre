"""
Settings page — Groq API key + output config only.
Outlook section kept but collapsed since user uploads manually.
"""

import streamlit as st
import os
from utils.state import init_state

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
]

def _load_from_secrets():
    """Pull values from st.secrets or env vars into session state once."""
    init_state()
    if not st.session_state.get("groq_api_key"):
        try:
            st.session_state["groq_api_key"] = st.secrets["groq"]["api_key"]
        except Exception:
            st.session_state["groq_api_key"] = os.environ.get("GROQ_API_KEY", "")
    if not st.session_state.get("groq_model"):
        try:
            st.session_state["groq_model"] = st.secrets["groq"]["model"]
        except Exception:
            st.session_state["groq_model"] = "llama-3.3-70b-versatile"


def render():
    _load_from_secrets()
    st.title("⚙️ Settings")

    # ── Groq API ──────────────────────────────────────────────────────────────
    st.subheader("🤖 Groq API")
    st.markdown("Free API key at [console.groq.com](https://console.groq.com) — 2 min signup.")

    api_key = st.text_input(
        "Groq API key",
        value=st.session_state.get("groq_api_key", ""),
        type="password",
        placeholder="gsk_...",
    )
    model = st.selectbox(
        "Model",
        options=GROQ_MODELS,
        index=GROQ_MODELS.index(st.session_state.get("groq_model", GROQ_MODELS[0]))
              if st.session_state.get("groq_model") in GROQ_MODELS else 0,
    )

    col_test, _ = st.columns([1, 3])
    with col_test:
        if st.button("🔌 Test connection", use_container_width=True):
            if not api_key:
                st.error("Enter an API key first.")
            else:
                with st.spinner("Testing…"):
                    try:
                        from groq import Groq
                        client = Groq(api_key=api_key)
                        resp = client.chat.completions.create(
                            model=model,
                            messages=[{"role": "user", "content": "Reply with one word: connected"}],
                            max_tokens=5, temperature=0,
                        )
                        st.success(f"✅ Connected to Groq ({model})")
                    except Exception as e:
                        st.error(f"❌ {e}")

    st.markdown("---")

    # ── Output path ───────────────────────────────────────────────────────────
    st.subheader("📁 Output")
    output_xlsx = st.text_input(
        "Excel output path",
        value=st.session_state.get("output_xlsx", "data/cadre_quotes.xlsx"),
    )

    st.markdown("---")

    # ── Save ──────────────────────────────────────────────────────────────────
    if st.button("💾 Save settings", type="primary", use_container_width=True):
        st.session_state["groq_api_key"] = api_key
        st.session_state["groq_model"]   = model
        st.session_state["output_xlsx"]  = output_xlsx
        st.success("✅ Saved — go to **📤 Upload Quotes** to process files.")

    # ── Inbox Monitor (collapsed, optional) ───────────────────────────────────
    st.markdown("---")
    with st.expander("📬 Outlook Inbox Monitor (optional — for auto-processing)"):
        st.caption("Only needed if you want the agent to watch your inbox automatically. For manual upload, ignore this.")
        outlook_email = st.text_input(
            "Outlook email",
            value=st.session_state.get("outlook_email", ""),
            placeholder="cadre.quote@distributor-systems.com",
        )
        outlook_password = st.text_input(
            "App Password",
            value=st.session_state.get("outlook_password", ""),
            type="password",
        )
        poll_interval = st.slider("Poll interval (seconds)", 30, 600,
                                   st.session_state.get("poll_interval", 60), 30)
        if st.button("Save Outlook settings"):
            st.session_state["outlook_email"]    = outlook_email
            st.session_state["outlook_password"] = outlook_password
            st.session_state["poll_interval"]    = poll_interval
            st.success("Outlook settings saved.")
