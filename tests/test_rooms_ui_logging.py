"""Tests for service/rooms.py's _log_ui_ops — the ui.update log line
Room._emit_ui/broadcast_ui_now emit for every outgoing op, independent
of AGENT_VERBOSE (that flag only gates llm/callbacks.py's raw-I/O print).
"""

import unittest

from models.ui import Node, UIOp
from service.rooms import _log_ui_ops


class TestLogUiOps(unittest.TestCase):
    def test_logs_one_line_per_op(self):
        ops = [
            UIOp(
                op="replace", target="header", node=Node(type="container", id="header")
            ),
            UIOp(op="remove", target="modal"),
        ]
        with self.assertLogs("service.rooms", level="INFO") as cm:
            _log_ui_ops(ops)
        self.assertEqual(len(cm.output), 2)
        self.assertIn("replace", cm.output[0])
        self.assertIn("header", cm.output[0])
        self.assertIn("type=container", cm.output[0])
        self.assertIn("remove", cm.output[1])
        self.assertIn("modal", cm.output[1])
        self.assertIn("no node", cm.output[1])

    def test_empty_ops_logs_nothing(self):
        with self.assertRaises(AssertionError):
            # assertLogs itself raises if nothing was logged — confirms
            # an empty batch doesn't emit a spurious line.
            with self.assertLogs("service.rooms", level="INFO"):
                _log_ui_ops([])


if __name__ == "__main__":
    unittest.main()
