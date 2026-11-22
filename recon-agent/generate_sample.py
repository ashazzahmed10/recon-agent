"""Generates messy demo CSVs with planted problems. Run: python generate_sample.py"""
import csv
import random
from datetime import datetime, timedelta

random.seed(7)
FEE_RATE, GST = 0.02, 0.18
start = datetime(2026, 11, 15, 9, 0)

orders, settlements, bank = [], [], []
prices = [499, 799, 1299, 1499, 2499, 3499, 5999]

for i in range(1, 31):
    oid = f"ORD-{1000 + i}"
    method = random.choice(["UPI", "UPI", "Card", "COD"])
    amt = random.choice(prices)
    status = "paid"
    if oid == "ORD-1019":
        method, amt, status = "Card", 1299, "refunded"
    if oid == "ORD-1007":
        method, amt = "UPI", 2499
    if oid == "ORD-1013":
        method, amt = "Card", 5999
    if oid == "ORD-1024":
        method, amt = "UPI", 3499
    orders.append({
        "Order ID": oid,
        "Order Date": (start + timedelta(hours=5 * i)).strftime("%Y-%m-%d %H:%M"),
        "Customer ID": f"CUST-{random.randint(10, 60):03d}",
        "Amount": amt, "Payment Method": method, "Status": status,
    })

# Risk pattern: one customer, 4 high-value COD orders in under an hour
for j in range(4):
    orders.append({
        "Order ID": f"ORD-{1031 + j}",
        "Order Date": (start + timedelta(days=6, minutes=12 * j)).strftime("%Y-%m-%d %H:%M"),
        "Customer ID": "CUST-077", "Amount": 18999 if j == 0 else 5999,
        "Payment Method": "COD", "Status": "paid",
    })

pay_n = 0
for o in orders:
    if o["Payment Method"] == "COD":
        continue
    oid, amt = o["Order ID"], o["Amount"]
    if oid == "ORD-1007":          # PLANTED: paid order never settled
        continue
    batch = f"SETL-0{(int(oid[-2:]) - 1) // 10 + 1}"
    captured = 3149 if oid == "ORD-1024" else amt         # PLANTED: under-capture
    rate = 0.035 if oid == "ORD-1013" else FEE_RATE      # PLANTED: excess fee
    pay_n += 1
    settlements.append({"payment_id": f"pay_{pay_n:04d}", "order_id": oid, "type": "payment",
                        "amount": captured, "fee": round(captured * rate * (1 + GST), 2),
                        "settlement_id": batch, "settled_on": o["Order Date"][:10]})
    if o["Status"] == "refunded":
        for _ in range(2):         # PLANTED: duplicate refund
            pay_n += 1
            settlements.append({"payment_id": f"rfnd_{pay_n:04d}", "order_id": oid, "type": "refund",
                                "amount": amt, "fee": 0, "settlement_id": batch,
                                "settled_on": o["Order Date"][:10]})

# PLANTED: payment for an order that does not exist
settlements.append({"payment_id": "pay_9001", "order_id": "ORD-9999", "type": "payment",
                    "amount": 7999, "fee": round(7999 * FEE_RATE * (1 + GST), 2),
                    "settlement_id": "SETL-03", "settled_on": "2026-11-20"})

nets = {}
for s in settlements:
    sign = 1 if s["type"] == "payment" else -1
    nets[s["settlement_id"]] = nets.get(s["settlement_id"], 0) + sign * s["amount"] - s["fee"]

for sid, net in sorted(nets.items()):
    if sid == "SETL-03":
        net -= 500                 # PLANTED: short payout in bank
    bank.append({"Txn Date": "2026-11-2" + sid[-1], "Description": f"NEFT RAZORPAY SETL {sid}",
                 "Reference": sid, "Credit": f"₹{net:,.2f}"})

for name, rows in [("orders", orders), ("settlements", settlements), ("bank", bank)]:
    with open(f"sample_data/{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
print("sample_data/ written:", len(orders), "orders,", len(settlements), "gateway rows,", len(bank), "bank rows")
