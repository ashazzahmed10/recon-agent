import io

import pandas as pd
import streamlit as st

import reconcile as rc
from agent import Agent, dispute_email, get_config

st.set_page_config(page_title="ReconAgent – Payments Reconciliation", page_icon="🧾", layout="wide")

MAX_CHAT = 10
MAX_RUNS = 5
MAX_MB = 5
TYPE_LABEL = {"MISSING_SETTLEMENT": "Missing settlement", "AMOUNT_MISMATCH": "Amount mismatch",
              "EXCESS_FEE": "Excess gateway fee", "DUPLICATE_REFUND": "Duplicate refund",
              "UNKNOWN_ORDER_PAYMENT": "Payment for unknown order",
              "SETTLEMENT_NOT_IN_BANK": "Settlement not in bank", "SHORT_PAYOUT": "Short payout"}
SEV = {"high": "🔴", "medium": "🟠", "low": "🟡"}

ss = st.session_state
ss.setdefault("agent", None)
ss.setdefault("chat", [])
ss.setdefault("runs", 0)
ss.setdefault("held", [])

# ---------------- sidebar: inputs ----------------
with st.sidebar:
    st.header("1. Your data")
    use_sample = st.toggle("Use sample data", value=True,
                           help="Demo files with planted problems. Turn off to upload your own.")
    files = {}
    if not use_sample:
        for kind, hint in [("orders", "order_id, order_date, customer_id, amount, payment_method, status"),
                           ("settlements", "payment_id, order_id, type, amount, fee, settlement_id"),
                           ("bank", "reference, credit")]:
            files[kind] = st.file_uploader(f"{kind.title()} CSV", type=["csv"], help=f"Columns: {hint}")
    st.header("2. Settings")
    fee_rate = st.number_input("Agreed gateway fee (%)", 0.0, 10.0, 2.0, 0.1) / 100
    merchant = st.text_input("Your store name", "Your Store", max_chars=40)
    cfg = get_config(st.secrets if hasattr(st, "secrets") else None)
    st.caption("🤖 AI: " + ("connected" if cfg["key"] else "not configured. Rule-based mode."))
    st.caption("🛡️ Demo mode: no emails are sent and no real orders are changed.")
    run = st.button("Run reconciliation", type="primary", use_container_width=True)

st.title("🧾 ReconAgent")
st.write("Matches your **store orders**, **payment-gateway settlements** and **bank statement**, "
         "finds missing money, explains it, flags risky orders, and drafts the fix for you to approve.")


def read_inputs():
    if use_sample:
        return {k: rc.load(k, f"sample_data/{k}.csv") for k in ("orders", "settlements", "bank")}
    missing = [k for k, f in files.items() if f is None]
    if missing:
        raise rc.DataError(f"Please upload: {', '.join(missing)}.")
    for k, f in files.items():
        if f.size > MAX_MB * 1024 * 1024:
            raise rc.DataError(f"{k} file is larger than {MAX_MB} MB.")
    return {k: rc.load(k, io.BytesIO(f.getvalue())) for k, f in files.items()}


if run:
    if ss.runs >= MAX_RUNS:
        st.warning("You've reached the run limit for this session. Refresh the page to start over.")
    else:
        try:
            data = read_inputs()
            with st.status("Agent working…", expanded=True) as status:
                agent = Agent(data["orders"], data["settlements"], data["bank"], fee_rate, cfg)
                agent.run(log=st.write)
                status.update(label="Done", state="complete", expanded=False)
            ss.agent, ss.chat, ss.held = agent, [], []
            ss.runs += 1
        except rc.DataError as e:
            st.error(f"📄 {e}")
        except Exception as e:
            st.error("Something went wrong while reading your files. Check they're valid CSVs and try again.")
            st.caption(f"Details: {type(e).__name__}")

agent = ss.agent
if not agent:
    st.info("👈 Click **Run reconciliation** to try it with the sample data.")
    st.stop()

# ---------------- results ----------------
issues = agent.issues
at_risk = sum(i["impact"] for i in issues if i["impact"] > 0)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Money at risk", rc._inr(at_risk))
c2.metric("Issues found", len(issues))
c3.metric("Risky orders", len(agent.risks))
c4.metric("Orders checked", len(agent.orders))
if agent.report.get("summary"):
    st.success(agent.report["summary"])
if not issues and not agent.risks:
    st.balloons()
    st.write("✅ Everything matches. Nothing to do today.")

tab1, tab2, tab3, tab4 = st.tabs(["Issues", "Risky orders", "Approvals", "Ask the agent"])

with tab1:
    for i in issues:
        label = f"{SEV[i['severity']]} {i['id']} · {TYPE_LABEL[i['type']]} · {i['order_id'] or i['settlement_id']}"
        if i["impact"]:
            label += f" · {rc._inr(i['impact'])}"
        with st.expander(label):
            st.write(i["explanation"])
            st.caption("✍️ AI explanation (amounts checked against data)" if i.get("ai_written")
                       else "📐 Rule-based explanation")
            st.json(i["facts"], expanded=False)

with tab2:
    if not agent.risks:
        st.write("No risky orders flagged.")
    for r in agent.risks:
        st.markdown(f"**{r['order_id']}** · {r['customer_id']} · {r['amount']} · risk score **{r['score']}/100**")
        for reason in r["reasons"]:
            st.markdown(f"- {reason}")

with tab3:
    st.caption("The agent only proposes. Nothing happens until you approve.")
    by_id = {i["id"]: i for i in issues}
    for n, p in enumerate(agent.proposals):
        issue = by_id.get(p["target_id"])
        title = {"dispute_email": "📧 Send dispute email", "hold_order": "⏸️ Hold order",
                 "review": "🔍 Mark for manual review"}[p["action"]]
        with st.container(border=True):
            st.markdown(f"**{title}** → `{p['target_id']}`" +
                        (f" · {TYPE_LABEL[issue['type']]}" if issue else ""))
            st.caption(p["reason"])
            if p["status"] == "pending":
                a, b, _ = st.columns([1, 1, 4])
                if a.button("Approve", key=f"ap{n}", type="primary"):
                    p["status"] = "approved"
                    if p["action"] == "hold_order":
                        ss.held.append(p["target_id"])
                    st.rerun()
                if b.button("Reject", key=f"rj{n}"):
                    p["status"] = "rejected"
                    st.rerun()
            elif p["status"] == "rejected":
                st.write("❌ Rejected")
            else:
                st.write("✅ Approved")
                if p["action"] == "dispute_email" and issue:
                    body = st.text_area("Edit before sending", dispute_email(issue, merchant),
                                        height=260, key=f"em{n}")
                    st.download_button("Download email", body, f"dispute_{issue['id']}.txt", key=f"dl{n}")
                    st.caption("Demo mode: email not actually sent.")
                elif p["action"] == "hold_order":
                    st.write(f"Order {p['target_id']} marked ON HOLD (simulated).")
    if ss.held:
        st.download_button("Download held orders (CSV)",
                           pd.DataFrame({"order_id": ss.held, "status": "on_hold"}).to_csv(index=False),
                           "held_orders.csv")

with tab4:
    st.caption(f"Ask about the results, e.g. “Why is ORD-1019 flagged?” ({MAX_CHAT} questions per session)")
    for m in ss.chat:
        st.chat_message(m["role"]).write(m["content"])
    asked = sum(1 for m in ss.chat if m["role"] == "user")
    q = st.chat_input("Ask the agent…", disabled=asked >= MAX_CHAT, max_chars=500)
    if q:
        st.chat_message("user").write(q)
        with st.spinner("Thinking…"):
            ans = agent.chat(ss.chat, q)
        ss.chat += [{"role": "user", "content": q}, {"role": "assistant", "content": ans}]
        st.chat_message("assistant").write(ans)

with st.expander("🔍 Agent trace (what tools it called)"):
    st.json(agent.trace or [{"mode": "rule-based"}])
