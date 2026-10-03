"""Summarize actual filled trades, including user-supplied total costs."""

import argparse
import csv
import json
from datetime import datetime
from math import isfinite


def evaluate(records):
    trades = []
    for line, record in enumerate(records, 2):
        try:
            entry, exit_price, quantity, stop, fees = (float(record[key]) for key in
                ("entry_price", "exit_price", "quantity", "stop_price", "fees_inr"))
            side = {"long": 1, "short": -1}[record["side"].lower()]
            entered, exited = (datetime.fromisoformat(record[key]) for key in ("entry_time", "exit_time"))
            if not all(isfinite(value) for value in (entry, exit_price, quantity, stop, fees)):
                raise ValueError("non-finite values")
            if min(entry, exit_price, quantity, stop) <= 0 or fees < 0 or (entry - stop) * side <= 0 or exited < entered:
                raise ValueError("invalid prices, costs, times or stop direction")
            risk = abs(entry - stop) * quantity
            net = (exit_price - entry) * side * quantity - fees
            trades.append({"signal_id": record["signal_id"], "exit": exited, "net": net, "r": net / risk})
        except (KeyError, ValueError, TypeError) as error:
            raise ValueError(f"Invalid filled trade on CSV line {line}: {error}") from error
    trades.sort(key=lambda row: row["exit"])
    balance, peak, drawdown = 0.0, 0.0, 0.0
    for trade in trades:
        balance += trade["net"]
        peak = max(peak, balance)
        drawdown = max(drawdown, peak - balance)
    count = len(trades)
    return {"filled_trades": count, "net_winners": sum(row["net"] > 0 for row in trades),
            "net_win_rate": sum(row["net"] > 0 for row in trades) / count if count else None,
            "net_pnl_inr": round(balance, 2), "average_net_r": sum(row["r"] for row in trades) / count if count else None,
            "max_realized_drawdown_inr": round(drawdown, 2),
            "note": "Actual fills and supplied total costs only; not a strategy backtest or accuracy guarantee."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="Actual fills CSV, using the trades-template.csv columns")
    args = parser.parse_args()
    with open(args.csv, newline="", encoding="utf-8") as handle:
        result = evaluate(csv.DictReader(handle))
    print(json.dumps(result, indent=2, allow_nan=False))
