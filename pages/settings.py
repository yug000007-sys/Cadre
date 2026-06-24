"""
Settings page — configure Groq API, Outlook credentials, output path.
Auto-loads from st.secrets or environment variables on first run.
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

def _load_defaults():
    """Pull from st.secrets or env vars once, into session state."""
    init_state()

    def _get(session_key, *fallbacks):
        if st.session_state.get(session_key):
            return  # already set
        for fb in fallbacks:
            val = fb() if callable(fb) else fb
            if val:
                st.session_state[session_key] = val
                return

    _get("groq_api_key",
         lambda: st.secrets.get("groq", {}).get("api_key", ""),
         lambda: os.environ.get("GROQ_API_KEY", ""))

    _get("groq_model",
         lambda: st.secrets.get("groq", {}).get("model", ""),
         "llama-3.3-70b-versatile")

    _get("outlook_email",
         lambda: st.secrets.get("outlook", {}).get("email", ""),
         lambda: os.environ.get("CADRE_EMAIL", ""))

    _get("outlook_password",
         lambda: st.secrets.get("outlook", {}).get("password", ""),
         lambda: os.environ.get("CADRE_PASSWORD", ""))

    _get("output_xlsx",
         lambda: st.secrets.get("output", {}).get("xlsx_path", ""),
         "data/cadre_quotes.xlsx")

    if not st.session_state.get("poll_interval"):
        try:
            st.session_state["poll_interval"] = int(
                st.secrets.get("output", {}).get("poll_interval", 60)
            )
        except Exception:
            st.session_state["poll_interval"] = 60


def render():
    _load_defaults()
    st.title("⚙️ Settings")
    st.caption("Configure API keys, Outlook connection, and output options. Settings are saved for this session.")

    # ── Groq API ──────────────────────────────────────────────────────────────
    st.subheader("🤖 Groq API")
    st.markdown(
        "Get your **free** API key at [console.groq.com](https://console.groq.com) — "
        "takes 2 minutes, generous free tier."
    )

    api_key = st.text_input(
        "Groq API key *",
        value=st.session_state.get("groq_api_key", ""),
        type="password",
        placeholder="gsk_...",
        help="Required for AI extraction",
    )
    model = st.selectbox(
        "Model",
        options=GROQ_MODELS,
        index=GROQ_MODELS.index(st.session_state.get("groq_model", GROQ_MODELS[0]))
              if st.session_state.get("groq_model") in GROQ_MODELS else 0,
        help="llama-3.3-70b-versatile is recommended",
    )

    if st.button("🔌 Test Groq connection", key="test_groq"):
        if not api_key:
            st.error("Enter an API key first.")
        else:
            with st.spinner("Testing…"):
                try:
                    from groq import Groq
                    client = Groq(api_key=api_key)
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": "Reply with only the word: connected"}],
                        max_tokens=10,
                        temperature=0,
                    )
                    answer = resp.choices[0].message.content.strip().lower()
                    st.success(f"✅ Groq connected ({model}) — response: {answer}")
                except Exception as e:
                    st.error(f"❌ Failed: {e}")

    st.markdown("---")

    # ── Outlook ───────────────────────────────────────────────────────────────
    st.subheader("📬 Outlook / Exchange")
    st.markdown(
        "For **Microsoft 365 with MFA**, use an "
        "[App Password](https://support.microsoft.com/en-us/account-billing/manage-app-passwords-for-two-step-verification-d6dc8c6d-4bf7-4851-ad95-6d07799387e9) "
        "instead of your regular password."
    )

    outlook_email = st.text_input(
        "Outlook email",
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
        min_value=30, max_value=600,
        value=st.session_state.get("poll_interval", 60),
        step=30,
    )

    if st.button("🔌 Test Outlook connection", key="test_outlook"):
        if not outlook_email or not outlook_password:
            st.error("Enter email and password first.")
        else:
            with st.spinner("Connecting…"):
                try:
                    from exchangelib import Credentials, Account, DELEGATE, Configuration
                    creds   = Credentials(outlook_email, outlook_password)
                    config  = Configuration(server="outlook.office365.com", credentials=creds)
                    account = Account(primary_smtp_address=outlook_email, config=config,
                                      autodiscover=False, access_type=DELEGATE)
                    st.success(f"✅ Connected! Inbox has {account.inbox.total_count} messages.")
                except Exception as e:
                    st.error(f"❌ Failed: {e}")

    st.markdown("---")

    # ── Output ────────────────────────────────────────────────────────────────
    st.subheader("📁 Output")
    output_xlsx = st.text_input(
        "Excel output path",
        value=st.session_state.get("output_xlsx", "data/cadre_quotes.xlsx"),
    )

    st.markdown("---")

    # ── Save button ───────────────────────────────────────────────────────────
    if st.button("💾 Save settings", type="primary"):
        st.session_state["groq_api_key"]     = api_key
        st.session_state["groq_model"]       = model
        st.session_state["outlook_email"]    = outlook_email
        st.session_state["outlook_password"] = outlook_password
        st.session_state["poll_interval"]    = poll_interval
        st.session_state["output_xlsx"]      = output_xlsx
        st.success("✅ Settings saved! Switch to **Upload Quotes** to process files.")

    # ── secrets.toml helper ───────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("📋 Persist settings — copy to `.streamlit/secrets.toml`"):
        st.info("Add this file to your repo (it's gitignored) or paste into Streamlit Cloud → Settings → Secrets.")
        st.code(f"""[groq]
api_key = "{api_key or 'gsk_your_key_here'}"
model   = "{model}"

[outlook]
email    = "{outlook_email or 'cadre.quote@distributor-systems.com'}"
password = "your-app-password-here"

[output]
xlsx_path     = "{output_xlsx}"
poll_interval = {poll_interval}
""", language="toml")
