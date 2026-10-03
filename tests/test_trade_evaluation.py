import unittest

from evaluate_trades import evaluate


def trade(**overrides):
    row = {"signal_id": "test", "entry_time": "2026-09-28T10:00:00+05:30",
           "exit_time": "2026-09-28T10:15:00+05:30", "side": "long",
           "entry_price": 100, "exit_price": 102, "quantity": 10,
           "stop_price": 99, "fees_inr": 5}
    row.update(overrides)
    return row


class TradeEvaluationTests(unittest.TestCase):
    def test_long_net_r_includes_costs(self):
        result = evaluate([trade()])
        self.assertEqual(result["net_pnl_inr"], 15)
        self.assertEqual(result["average_net_r"], 1.5)

    def test_short_fill_direction(self):
        result = evaluate([trade(side="short", exit_price=98, stop_price=101)])
        self.assertEqual(result["net_pnl_inr"], 15)

    def test_fees_can_turn_gross_winner_into_net_loser(self):
        result = evaluate([trade(exit_price=100.1)])
        self.assertEqual(result["net_win_rate"], 0)

    def test_empty_input_has_no_invented_accuracy(self):
        self.assertIsNone(evaluate([])["net_win_rate"])

    def test_invalid_stop_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate([trade(stop_price=101)])

    def test_realized_drawdown_after_losses(self):
        result = evaluate([trade(), trade(exit_price=98, exit_time="2026-09-28T10:30:00+05:30")])
        self.assertEqual(result["max_realized_drawdown_inr"], 25)
