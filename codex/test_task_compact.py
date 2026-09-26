"""Offline integration checks for Codex hook events, rollouts, Jev and Herdr."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

spec = importlib.util.spec_from_file_location("tc", Path(__file__).with_name("task-compact.py"))
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)


def event(kind, **fields):
    return {"type": "event_msg", "payload": {"type": kind, **fields}}


class CompactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"HERDR_PANE_ID": "pane-1", "TASK_COMPACT_DRY": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.patches = [patch.object(tc, "STATE", self.root / "state"),
                        patch.object(tc, "LOG", str(self.root / "decisions.jsonl")),
                        patch.object(tc, "MIN_TOKENS", 630000),
                        patch.object(tc.time, "sleep")]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.transcript = self.root / "rollout.jsonl"
        self.data = {"session_id": "s1", "turn_id": "t1", "hook_event_name": "Stop",
                     "last_assistant_message": "Done.", "transcript_path": str(self.transcript)}
        tc.handle({**self.data, "hook_event_name": "UserPromptSubmit", "prompt": "Fix the bug"})
        self.data["_request_state"] = tc.read_state(self.data)
        self.rows = [event("task_started", turn_id="t1"),
                     event("token_count", info={"last_token_usage": {"input_tokens": 630000,
                           "cached_input_tokens": 100000}, "total_token_usage": {"input_tokens": 900000}}),
                     {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                       "phase": "final_answer", "content": [{"type": "output_text", "text": "Done."}]}},
                     event("task_complete", turn_id="t1", last_agent_message="Done.")]
        self.write()

    def write(self):
        self.transcript.write_text("".join(json.dumps(r) + "\n" for r in self.rows))

    def run_worker(self, judge=None):
        with patch.object(tc, "jev", side_effect=judge, return_value=(0.9, 0.05, 1)) as jev, \
             patch.object(tc.subprocess, "run") as run:
            tc.background(self.data, settle=0)
        return jev, run, json.loads(Path(tc.LOG).read_text().splitlines()[-1])

    def test_finished_injects_exact_commands_and_request(self):
        jev, run, result = self.run_worker()
        jev.assert_called_once_with("Fix the bug", "Done.")
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [["herdr", "pane", "send-text", "pane-1", "/compact"],
                          ["herdr", "pane", "send-keys", "pane-1", "enter"]])
        self.assertTrue(result["injected"])
        self.assertEqual(result["tokens"], 630000)
        jev, run, result = self.run_worker()
        jev.assert_not_called()
        run.assert_not_called()
        self.assertEqual(result["skip"], "duplicate")

    def test_small_uses_input_only_not_cached_or_cumulative(self):
        self.rows[1]["payload"]["info"]["last_token_usage"]["input_tokens"] = 629999
        self.write()
        jev, run, result = self.run_worker()
        jev.assert_not_called()
        run.assert_not_called()
        self.assertEqual(result["skip"], "small")

    def test_dry_run(self):
        os.environ["TASK_COMPACT_DRY"] = "1"
        _, run, result = self.run_worker()
        run.assert_not_called()
        self.assertTrue(result["fire"])

    def test_unfinished_or_waiting_never_injects(self):
        for scores in ((0.2, 0.1, 1), (0.95, 0.8, 1), (float('nan'), 0.1, 1)):
            with self.subTest(scores=scores):
                tc.state_path(self.data).with_suffix('.claimed').unlink(missing_ok=True)
                _, run, result = self.run_worker(lambda *args: scores)
                run.assert_not_called()

    def test_new_prompt_during_judge_cancels(self):
        def judge(*args):
            tc.handle({**self.data, "hook_event_name": "UserPromptSubmit", "prompt": "Next task"})
            return 0.9, 0.05, 1
        _, run, result = self.run_worker(judge)
        run.assert_not_called()
        self.assertEqual(result["skip"], "new-turn")

    def test_new_prompt_between_text_and_enter(self):
        def typed(*args, **kwargs):
            tc.handle({**self.data, "hook_event_name": "UserPromptSubmit", "prompt": "Next"})
        with patch.object(tc, "jev", return_value=(0.9, 0.05, 1)), \
             patch.object(tc.subprocess, "run", side_effect=typed) as run:
            tc.background(self.data, settle=0)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(json.loads(Path(tc.LOG).read_text())["skip"], "new-turn-typed")

    def test_lifecycle_events_cancel(self):
        for kind in ("Interrupt", "PreCompact", "SessionEnd", "SessionStart"):
            with self.subTest(kind=kind):
                tc.write_state(self.data, self.data["_request_state"])
                tc.handle({**self.data, "hook_event_name": kind})
                jev, run, result = self.run_worker()
                jev.assert_not_called()
                run.assert_not_called()
                self.assertEqual(result["skip"], "new-turn")

    def test_replacement_session_cancels_previous_worker(self):
        replacement = {**self.data, "session_id": "s2", "hook_event_name": "SessionStart"}
        tc.handle(replacement)
        jev, run, result = self.run_worker()
        jev.assert_not_called()
        run.assert_not_called()
        self.assertEqual(result["skip"], "new-turn")
        tc.handle({**replacement, "hook_event_name": "UserPromptSubmit", "prompt": "New session"})
        with patch.object(tc.subprocess, "Popen") as spawn:
            tc.handle(self.data.copy())
            spawn.assert_not_called()
        jev, run, result = self.run_worker()
        jev.assert_not_called()
        run.assert_not_called()
        self.assertEqual(result["skip"], "new-turn")

    def test_bad_or_newer_transcript_fails_closed(self):
        original = list(self.rows)
        for extra in (event("task_started", turn_id="t2"), event("turn_aborted"),
                      {"type": "compacted", "payload": {}}, event("user_message", message="Next")):
            with self.subTest(extra=extra):
                self.rows = original + [extra]
                self.write()
                jev, run, result = self.run_worker()
                jev.assert_not_called()
                run.assert_not_called()
                self.assertEqual(result["skip"], "failed-closed")
        for raw in ('{"type":', '[]\n', '{}\n'):
            self.transcript.write_text(raw)
            jev, run, _ = self.run_worker()
            jev.assert_not_called()
            run.assert_not_called()

    def test_missing_completion_or_wrong_reply(self):
        self.rows.pop()
        self.write()
        jev, run, _ = self.run_worker()
        jev.assert_not_called()
        self.rows.append(event("task_complete", turn_id="t1", last_agent_message="Different"))
        self.write()
        jev, run, _ = self.run_worker()
        jev.assert_not_called()

    def test_transcript_growth_during_judge(self):
        def judge(*args):
            with self.transcript.open("a") as f:
                f.write(json.dumps(event("task_started", turn_id="t2")) + "\n")
            return 0.9, 0.05, 1
        _, run, result = self.run_worker(judge)
        run.assert_not_called()
        self.assertEqual(result["skip"], "new-turn")

    def test_errors_fail_closed_without_leaking_text(self):
        _, run, result = self.run_worker(lambda *args: (_ for _ in ()).throw(OSError("secret body")))
        run.assert_not_called()
        self.assertNotIn("secret", Path(tc.LOG).read_text())
        self.assertEqual(result["error"], "OSError")

    def test_stop_detaches_and_ignores_wrong_events_or_turns(self):
        class Pipe(io.BytesIO):
            def close(inner):
                inner.value = inner.getvalue()
                super().close()
        pipe = Pipe()
        with patch.object(tc.subprocess, "Popen") as spawn:
            spawn.return_value.stdin = pipe
            tc.handle(self.data.copy())
            self.assertIn("--worker", spawn.call_args.args[0])
            self.assertTrue(spawn.call_args.kwargs["start_new_session"])
            self.assertEqual(json.loads(pipe.value)["_request_state"], self.data["_request_state"])
            spawn.reset_mock()
            for overrides in ({"turn_id": "other"}, {"session_id": "other"}, {"stop_hook_active": True},
                              {"hook_event_name": "SubagentStop"}):
                tc.handle({**self.data, **overrides})
            os.environ.pop("HERDR_PANE_ID")
            tc.handle(self.data)
            spawn.assert_not_called()

    def test_jev_retry_and_payload(self):
        error = urllib.error.HTTPError("https://api.typesafe.ai", 529, "busy", None, None)
        response = io.BytesIO(json.dumps({"answers": {"done": {"noul": .9}, "waiting": {"noul": .1}}}).encode())
        with patch.object(tc, "api_key", return_value="fake"), \
             patch.object(tc.urllib.request, "urlopen", side_effect=[error, response]) as send:
            self.assertEqual(tc.jev("x" * 5000, "y" * 9000), (.9, .1, 2))
            body = json.loads(send.call_args.args[0].data)
            self.assertEqual(len(body["state"]["user_request"]), 4000)
            self.assertEqual(len(body["state"]["assistant_final_reply"]), 8000)

    def test_tail_cut_and_unfinished_line(self):
        rows = tc.read_tail(str(self.transcript), limit=self.transcript.stat().st_size - 10)
        self.assertEqual(rows, self.rows[1:])
        with self.transcript.open("a") as f:
            f.write('{"payload":{}}')
        with self.assertRaises(ValueError):
            tc.read_tail(str(self.transcript))

    def test_missing_herdr_is_logged(self):
        with patch.object(tc, "jev", return_value=(.9, .1, 1)), \
             patch.object(tc.subprocess, "run", side_effect=FileNotFoundError()):
            tc.background(self.data, settle=0)
        self.assertEqual(json.loads(Path(tc.LOG).read_text())["error"], "FileNotFoundError")


if __name__ == "__main__":
    unittest.main()
