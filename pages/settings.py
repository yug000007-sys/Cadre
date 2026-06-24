"""
Settings page — configure Groq API, Outlook credentials, output path.
"""

import streamlit as st
import os

from utils.state import init_state
from utils.excel_io import XLSX_PATH


GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
]


def render():
    init_state()
    st.title("⚙️ Settings")
    st.caption("Configure API keys, Outlook connection, and output options.")

    # ── Load from env if not already in session ───────────────────────────────
    if not st.session_state.get("groq_api_key"):
        st.session_state["groq_api_key"] = os.environ.get("GROQ_API_KEY", "")
    if not st.session_state.get("outlook_email"):
        st.session_state["outlook_email"] = os.environ.get("CADRE_EMAIL", "")
    if not st.session_state.get("outlook_password"):
        st.session_state["outlook_password"] = os.environ.get("CADRE_PASSWORD", "")

    # ── Groq API settings ─────────────────────────────────────────────────────
    st.subheader("🤖 Groq API")
    st.markdown("Get your free API key at [console.groq.com](https://console.groq.com)")

    api_key = st.text_input(
        "Groq API key",
        value=st.session_state.get("groq_api_key", ""),
        type="password",
        placeholder="gsk_...",
    )
    model = st.selectbox(
        "Model",
        options=GROQ_MODELS,
        index=GROQ_MODELS.index(st.session_state.get("groq_model", GROQ_MODELS[0])),
        help="llama-3.3-70b-versatile recommended for best extraction accuracy",
    )

    if st.button("Test Groq connection"):
        if not api_key:
            st.error("Enter an API key first.")
        else:
            try:
                from groq import Groq
                client = Groq(api_key=api_key)
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "Reply with the single word: connected"}],
                    max_tokens=10,
                )
                answer = resp.choices[0].message.content.strip().lower()
                if "connected" in answer:
                    st.success(f"✅ Connected to Groq ({model})")
                else:
                    st.success(f"✅ Groq responded: {answer}")
            except Exception as e:
                st.error(f"❌ Connection failed: {e}")

    st.markdown("---")

    # ── Outlook settings ──────────────────────────────────────────────────────
    st.subheader("📬 Outlook / Exchange")
    st.markdown(
        "For Microsoft 365 with MFA, use an **App Password** instead of your regular password. "
        "[How to create an App Password](https://support.microsoft.com/en-us/account-billing/manage-app-passwords-for-two-step-verification-d6dc8c6d-4bf7-4851-ad95-6d07799387e9)"
    )

    outlook_email = st.text_input(
        "Outlook email (inbox to monitor)",
        value=st.session_state.get("outlook_email", ""),
        placeholder="cadre.quote@distributor-systems.com",
    )
    outlook_password = st.text_input(
        "Password / App Password",
        value=st.session_state.get("outlook_password", ""),
        type="password",
    )
    poll_interval = st.slider(
        "Poll interval (seconds)",
        min_value=30,
        max_value=600,
        value=st.session_state.get("poll_interval", 60),
        step=30,
        help="How often the agent checks for new emails",
    )

    if st.button("Test Outlook connection"):
        if not outlook_email or not outlook_password:
            st.error("Enter email and password.")
        else:
            try:
                from exchangelib import Credentials, Account, DELEGATE, Configuration
                credentials = Credentials(outlook_email, outlook_password)
                config = Configuration(server="outlook.office365.com", credentials=credentials)
                account = Account(
                    primary_smtp_address=outlook_email,
                    config=config,
                    autodiscover=False,
                    access_type=DELEGATE,
                )
                count = account.inbox.total_count
                st.success(f"✅ Connected! Inbox has {count} total messages.")
            except Exception as e:
                st.error(f"❌ Connection failed: {e}")

    st.markdown("---")

    # ── Output settings ────────────────────────────────────────────────────────
    st.subheader("📁 Output")
    output_xlsx = st.text_input(
        "Excel output path",
        value=st.session_state.get("output_xlsx", XLSX_PATH),
        help="Relative or absolute path for the output .xlsx file",
    )

    st.markdown("---")

    # ── Save ──────────────────────────────────────────────────────────────────
    if st.button("💾 Save settings", type="primary"):
        st.session_state["groq_api_key"]     = api_key
        st.session_state["groq_model"]       = model
        st.session_state["outlook_email"]    = outlook_email
        st.session_state["outlook_password"] = outlook_password
        st.session_state["poll_interval"]    = poll_interval
        st.session_state["output_xlsx"]      = output_xlsx
        st.success("✅ Settings saved for this session.")
        st.info("💡 To persist settings across restarts, add them to `.streamlit/secrets.toml` or as environment variables.")

    # ── secrets.toml helper ───────────────────────────────────────────────────
    with st.expander("📋 secrets.toml template (copy to .streamlit/secrets.toml)"):
        st.code(f"""
[groq]
api_key = "{api_key or 'gsk_...'}"
model   = "{model}"

[outlook]
email    = "{outlook_email or 'cadre.quote@distributor-systems.com'}"
password = "your-app-password"

[output]
xlsx_path = "{output_xlsx}"
poll_interval = {poll_interval}
""", language="toml")
