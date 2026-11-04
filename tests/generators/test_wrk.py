import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kubernetes import client

from aiopslab.generators.workload.wrk import Wrk, parse_schedule


def _wrk(**kwargs):
    with patch("aiopslab.generators.workload.wrk.config"):
        return Wrk(rate=10, dist="exp", connections=2, duration=10, threads=2, **kwargs)


def _created_container(wrk):
    """Run create_wrk_job against a fake API and return the container of the submitted Job."""
    with patch("aiopslab.generators.workload.wrk.client.BatchV1Api") as api, \
            patch("aiopslab.generators.workload.wrk.time.sleep"):
        api.return_value.read_namespaced_job.side_effect = client.exceptions.ApiException(status=404)
        wrk.create_wrk_job("wrk2-job", "default", "script.lua", "http://10.0.0.1:5000")
        body = api.return_value.create_namespaced_job.call_args.kwargs["body"]
    return body["spec"]["template"]["spec"]["containers"][0]


class ParseScheduleTest(unittest.TestCase):

    def test_segments(self):
        self.assertEqual(parse_schedule("300:20,120:50"), [(300, 20), (120, 50)])

    def test_invalid_segments_are_rejected(self):
        for schedule in ("300", "300:20:1", "a:20", "0:20", "300:0", "300:-5"):
            with self.subTest(schedule), self.assertRaises(ValueError):
                parse_schedule(schedule)


class WrkJobTest(unittest.TestCase):

    def test_without_schedule_the_job_is_the_usual_burst(self):
        with patch.dict(os.environ, clear=False):
            os.environ.pop("AIOPSLAB_WORKLOAD_SCHEDULE", None)
            container = _created_container(_wrk())
        self.assertNotIn("command", container)
        self.assertEqual(container["args"], [
            "wrk", "-D", "exp", "-t", "2", "-c", "2", "-d", "10s", "-L", "-s", "/scripts/script.lua",
            "http://10.0.0.1:5000", "-R", "10", "--latency",
        ])

    def test_schedule_runs_a_shell_loop_with_its_rates(self):
        env = {"AIOPSLAB_WORKLOAD_SCHEDULE": "300:20,120:50", "AIOPSLAB_WORKLOAD_CONNECTIONS": "8"}
        with patch.dict(os.environ, env):
            container = _created_container(_wrk())
        self.assertEqual(container["command"], ["/bin/sh", "-c"])
        script = container["args"][0]
        self.assertIn("run 300 20; run 120 50;", script)
        self.assertIn("-c 8", script)
        self.assertIn("http://10.0.0.1:5000", script)
        self.assertNotIn("-d 10s", script)


class ExistingJobTest(unittest.TestCase):

    def test_new_job_is_created_only_after_the_old_one_is_gone(self):
        events = []
        gone = client.exceptions.ApiException(status=404)
        with patch("aiopslab.generators.workload.wrk.client.BatchV1Api") as api, \
                patch("aiopslab.generators.workload.wrk.time.sleep"):
            jobs = api.return_value
            # Found, then still terminating twice, then deleted
            jobs.read_namespaced_job.side_effect = lambda **_: events.append("read") or (
                (_ for _ in ()).throw(gone) if events.count("read") == 4 else True)
            jobs.delete_namespaced_job.side_effect = lambda **_: events.append("delete")
            jobs.create_namespaced_job.side_effect = lambda **_: events.append("create") or jobs.create_result
            _wrk().create_wrk_job("wrk2-job", "default", "script.lua", "http://10.0.0.1:5000")
        self.assertEqual(events, ["read", "delete", "read", "read", "read", "create"])


class ContinuousCommandTest(unittest.TestCase):
    """Run the generated script in a real shell, with fake wrk and sleep on the PATH."""

    def _run(self, wrk_body):
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp) / "calls"
            for name, body in (("wrk", f'echo "$*" >> {calls}\n{wrk_body}\n'), ("sleep", "exit 0\n")):
                path = Path(tmp) / name
                path.write_text(f"#!/bin/sh\n{body}")
                path.chmod(path.stat().st_mode | stat.S_IEXEC)
            script = _wrk().continuous_command([(1, 5), (1, 7)], "script.lua", "http://10.0.0.1:5000")
            env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"}
            result = subprocess.run(["timeout", "2.5", "/bin/sh", "-c", script], env=env,
                                    capture_output=True, text=True)
            return result.stdout, calls.read_text().splitlines()

    def test_failing_wrk_retries_inside_its_segment_and_keeps_the_schedule(self):
        stdout, calls = self._run("exit 1")
        rates = [call.split("-R ")[1].split()[0] for call in calls]
        self.assertGreater(len(rates), 2)
        # Every retry of a segment stays on its rate, and the segments still alternate in time
        self.assertEqual(rates[0], "5")
        self.assertIn("7", rates)
        changes = [rate for i, rate in enumerate(rates) if i == 0 or rates[i - 1] != rate]
        self.assertEqual(changes[:2], ["5", "7"])
        self.assertLessEqual(len(changes), 4)
        self.assertIn(" 1 5", stdout)


if __name__ == "__main__":
    unittest.main()
