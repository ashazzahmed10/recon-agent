"""Deterministic core: loading, validation, reconciliation and risk scoring.
All money maths lives here. The LLM never calculates numbers."""
import re
import pandas as pd

MAX_ROWS = 5000
GST = 0.18

REQUIRED = {
    "orders": ["order_id", "order_date", "customer_id", "amount", "payment_method", "status"],
    "settlements": ["payment_id", "order_id", "type", "amount", "fee", "settlement_id"],
    "bank": ["reference", "credit"],
}
ALIASES = {
    "order_id": ["orderid", "order_no", "order_number", "order"],
    "order_date": ["date", "created_at", "order_time"],
    "customer_id": ["customer", "cust_id", "buyer_id"],
    "amount": ["order_amount", "total", "value"],
    "payment_method": ["method", "payment_mode", "mode"],
    "settlement_id": ["settlement", "setl_id", "batch_id"],
    "reference": ["ref", "utr", "ref_no"],
    "credit": ["credit_amount", "deposit", "amount"],
}


class DataError(ValueError):
    """Raised with a message that is safe and friendly to show the user."""


def _clean_cols(df):
    df.columns = [re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_") for c in df.columns]
    return df


def _money(series):
    return pd.to_numeric(series.astype(str).str.replace(r"[₹,\s]|INR|Rs\.?", "", regex=True),
                         errors="coerce")


def load(kind, file):
    try:
        df = pd.read_csv(file, dtype=str)
    except Exception:
        raise DataError(f"Couldn't read the {kind} file. Please upload a valid .csv file.")
    if df.empty:
        raise DataError(f"The {kind} file has no rows.")
    if len(df) > MAX_ROWS:
        raise DataError(f"The {kind} file has {len(df):,} rows. The limit is {MAX_ROWS:,}.")
    df = _clean_cols(df)
    for col in REQUIRED[kind]:
        if col not in df.columns:
            for alt in ALIASES.get(col, []):
                if alt in df.columns:
                    df = df.rename(columns={alt: col})
                    break
    missing = [c for c in REQUIRED[kind] if c not in df.columns]
    if missing:
        raise DataError(f"The {kind} file is missing column(s): {', '.join(missing)}. "
                        f"Found: {', '.join(df.columns)}.")
    for c in ("amount", "fee", "credit"):
        if c in df.columns:
            df[c] = _money(df[c])
            if df[c].isna().any():
                bad = df[df[c].isna()].index[:3].tolist()
                raise DataError(f"Non-numeric values in '{c}' of the {kind} file (rows {bad}).")
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].str.strip().str.slice(0, 80)  # cap free text (prompt-injection surface)
    if "status" in df:
        df["status"] = df["status"].str.lower()
    if "type" in df:
        df["type"] = df["type"].str.lower()
    if "payment_method" in df:
        df["payment_method"] = df["payment_method"].str.upper()
    return df


def _inr(x):
    return f"₹{x:,.2f}"


def reconcile(orders, stl, bank, fee_rate=0.02):
    issues = []

    def add(kind, severity, impact, action, facts, order_id=None, settlement_id=None):
        issues.append({"id": f"ISS-{len(issues) + 1:02d}", "type": kind, "severity": severity,
                       "order_id": order_id, "settlement_id": settlement_id,
                       "impact": round(float(impact), 2), "action": action, "facts": facts})

    pays = stl[stl["type"] == "payment"]
    refunds = stl[stl["type"] == "refund"]
    order_ids = set(orders["order_id"])

    # 1. Prepaid order with no settlement
    prepaid = orders[(orders["payment_method"] != "COD") & orders["status"].isin(["paid", "refunded"])]
    for _, o in prepaid[~prepaid["order_id"].isin(pays["order_id"])].iterrows():
        add("MISSING_SETTLEMENT", "high", o["amount"], "dispute_email",
            {"order_amount": _inr(o["amount"]), "method": o["payment_method"]}, o["order_id"])

    # 2. Captured amount differs from order amount
    m = pays.merge(orders[["order_id", "amount"]], on="order_id", suffixes=("_captured", "_order"))
    for _, r in m[(m["amount_captured"] - m["amount_order"]).abs() > 0.5].iterrows():
        diff = r["amount_order"] - r["amount_captured"]
        add("AMOUNT_MISMATCH", "medium", diff, "dispute_email",
            {"order_amount": _inr(r["amount_order"]), "captured": _inr(r["amount_captured"]),
             "difference": _inr(diff)}, r["order_id"], r["settlement_id"])

    # 3. Fee above agreed rate (+18% GST on fee)
    for _, p in pays.iterrows():
        expected = p["amount"] * fee_rate * (1 + GST)
        if p["fee"] - expected > 1:
            add("EXCESS_FEE", "medium", p["fee"] - expected, "dispute_email",
                {"charged_fee": _inr(p["fee"]), "expected_fee": _inr(expected),
                 "charged_rate": f"{p['fee'] / (1 + GST) / p['amount']:.2%}",
                 "agreed_rate": f"{fee_rate:.2%}", "overcharge": _inr(p["fee"] - expected)},
                p["order_id"], p["settlement_id"])

    # 4. Duplicate / excess refunds
    for oid, g in refunds.groupby("order_id"):
        order_amt = orders.loc[orders["order_id"] == oid, "amount"]
        cap = float(order_amt.iloc[0]) if len(order_amt) else 0
        if len(g) > 1 or g["amount"].sum() > cap + 0.5:
            extra = g["amount"].sum() - cap
            add("DUPLICATE_REFUND", "high", extra, "dispute_email",
                {"refund_count": len(g), "total_refunded": _inr(g["amount"].sum()),
                 "order_amount": _inr(cap), "excess": _inr(extra),
                 "refund_ids": ", ".join(g["payment_id"])}, oid, g["settlement_id"].iloc[0])

    # 5. Payment for an order we don't have
    for _, p in pays[~pays["order_id"].isin(order_ids)].iterrows():
        add("UNKNOWN_ORDER_PAYMENT", "high", 0, "review",
            {"payment_id": p["payment_id"], "amount": _inr(p["amount"])},
            p["order_id"], p["settlement_id"])

    # 6. Settlement batch vs bank credit
    sign = stl["type"].map({"payment": 1, "refund": -1}).fillna(0)
    expected_net = (stl["amount"] * sign - stl["fee"]).groupby(stl["settlement_id"]).sum()
    credits = bank.groupby("reference")["credit"].sum()
    for sid, net in expected_net.items():
        got = credits.get(sid)
        if got is None:
            add("SETTLEMENT_NOT_IN_BANK", "high", net, "dispute_email",
                {"expected_credit": _inr(net)}, settlement_id=sid)
        elif net - got > 1:
            add("SHORT_PAYOUT", "high", net - got, "dispute_email",
                {"expected_credit": _inr(net), "bank_credit": _inr(got), "shortfall": _inr(net - got)},
                settlement_id=sid)
    return issues


def score_risk(orders, stl, issues):
    o = orders.copy()
    o["ts"] = pd.to_datetime(o["order_date"], errors="coerce")
    median = o["amount"].median()
    flagged = []
    for _, r in o.iterrows():
        score, reasons = 0, []
        cust = o[o["customer_id"] == r["customer_id"]]
        if r["payment_method"] == "COD" and r["amount"] > 3 * median:
            score += 40
            reasons.append(f"COD order of {_inr(r['amount'])} is over 3x the typical order ({_inr(median)})")
        if pd.notna(r["ts"]):
            near = cust[(cust["ts"] - r["ts"]).abs() <= pd.Timedelta(hours=1)]
            if len(near) >= 3:
                score += 35
                reasons.append(f"{len(near)} orders from {r['customer_id']} within 1 hour")
        for i in issues:
            if i["order_id"] == r["order_id"] and i["type"] == "DUPLICATE_REFUND":
                score += 50
                reasons.append("refunded more than once")
        if score >= 50:
            flagged.append({"order_id": r["order_id"], "customer_id": r["customer_id"],
                            "amount": _inr(r["amount"]), "score": min(score, 100),
                            "reasons": reasons, "action": "hold_order"})
    return sorted(flagged, key=lambda x: -x["score"])


TEMPLATES = {
    "MISSING_SETTLEMENT": "Order {order_id} was paid by {method} ({order_amount}) but the gateway never settled it.",
    "AMOUNT_MISMATCH": "Order {order_id} was for {order_amount} but only {captured} was captured, {difference} short.",
    "EXCESS_FEE": "On order {order_id} the gateway charged {charged_fee} in fees ({charged_rate}) instead of {expected_fee} at the agreed {agreed_rate}. Overcharge: {overcharge}.",
    "DUPLICATE_REFUND": "Order {order_id} ({order_amount}) was refunded {refund_count} times, {total_refunded} in total. {excess} went out extra.",
    "UNKNOWN_ORDER_PAYMENT": "The gateway shows payment {payment_id} of {amount} for {order_id}, which doesn't exist in your store. Check for a test or fraudulent order.",
    "SETTLEMENT_NOT_IN_BANK": "Settlement {settlement_id} ({expected_credit}) never arrived in your bank account.",
    "SHORT_PAYOUT": "Settlement {settlement_id} should have paid {expected_credit} but your bank received {bank_credit}. Shortfall: {shortfall}.",
}


def template_explanation(issue):
    return TEMPLATES[issue["type"]].format(order_id=issue["order_id"],
                                           settlement_id=issue["settlement_id"], **issue["facts"])


def numbers_are_grounded(text, issue):
    """Guardrail: every ₹ amount the LLM writes must appear in the code-computed facts."""
    allowed = {re.sub(r"[^\d.]", "", str(v)) for v in issue["facts"].values()}
    allowed |= {a.split(".")[0] for a in allowed}
    for amt in re.findall(r"₹\s?([\d,]+(?:\.\d+)?)", text):
        clean = amt.replace(",", "")
        if clean not in allowed and clean.split(".")[0] not in allowed:
            return False
    return True
