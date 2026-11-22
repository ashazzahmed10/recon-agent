# 🧾 ReconAgent: payments reconciliation and risk agent

An AI agent for small online merchants. It matches **store orders ↔ payment-gateway settlements ↔ bank statement**, finds missing money, explains each problem in plain English, flags risky orders, and drafts the fix (dispute email or order hold) **for a human to approve**.

## What it catches
| Check | Example |
|---|---|
| Missing settlement | UPI order paid, gateway never settled it |
| Amount mismatch | ₹3,499 order, only ₹3,149 captured |
| Excess gateway fee | Charged 3.5% instead of the agreed 2% (+18% GST) |
| Duplicate refund | Same order refunded twice |
| Payment for unknown order | Gateway payment with no matching store order |
| Short payout / missing credit | Settlement batch ≠ bank credit |
| Risky orders | High-value COD, 3+ orders in 1 hour from one customer, repeat refunds |

## How the agent works
```
User → Agent (LLM with tool calling)
         ├─ run_reconciliation   (deterministic Python: all ₹ maths)
         ├─ score_risk           (rule-based risk signals)
         ├─ get_order            (look up evidence)
         ├─ propose_action       (queued, needs human approval)
         └─ submit_report        (plain-English explanations)
       → Guardrail layer → UI (Approve / Reject → email draft / hold list)
```

## Guardrails
- **The AI never does maths.** Amounts come from code. Every ₹ figure the AI writes is checked against computed facts, and if it doesn't match, the explanation falls back to a verified template.
- **Human-in-the-loop.** The agent can only *propose* actions. Nothing happens without Approve.
- **Demo-safe actions.** Emails are drafted (editable and downloadable), not sent. Holds are simulated.
- **Hallucination guard.** Proposals or explanations for unknown issue IDs are rejected.
- **Prompt-injection guard.** File contents are treated as data, free-text fields are capped at 80 chars, and the system prompt tells the AI to ignore instructions inside the data.
- **Always works.** If there's no API key, or on timeout, rate limit or a bad response, it switches to rule-based mode. Step limit is 8 and timeout is 30s.
- **Input validation.** CSV only, 5 MB / 5,000 rows max, column-name aliases, friendly error messages.
- **Abuse limits.** 5 runs and 10 chat questions per session, input length caps, chat restricted to the reconciliation topic.
- **Transparency.** An agent trace shows every tool call, and each explanation is labelled "AI" or "rule-based".

## Run locally
```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add your key (optional)
streamlit run app.py
```

## Deploy (live link)
1. Push this folder to a **public GitHub repo**.
2. Go to **share.streamlit.io** → *Create app* → pick the repo, branch `main`, file `app.py`.
3. *Advanced settings → Secrets*: paste the contents of `secrets.toml.example` with your key.
4. Deploy and you get a `https://<name>.streamlit.app` link.

Any OpenAI-compatible provider works:
| Provider | LLM_BASE_URL | LLM_MODEL |
|---|---|---|
| Groq (free) | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.0-flash` |

## Sample data
`sample_data/` has planted problems. Regenerate with `python generate_sample.py`.
Your own CSVs need these columns:
- **orders**: order_id, order_date, customer_id, amount, payment_method (UPI/CARD/COD), status (paid/refunded)
- **settlements**: payment_id, order_id, type (payment/refund), amount, fee, settlement_id
- **bank**: reference (settlement id), credit
