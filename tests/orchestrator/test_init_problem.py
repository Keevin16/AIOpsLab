import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# aiopslab.observer loads the kube config at import time
with patch("kubernetes.config.kube_config.load_kube_config"):
    from aiopslab.orchestrator.orchestrator import Orchestrator

MODULE = "aiopslab.orchestrator.orchestrator"
VARIABLES = ("AIOPSLAB_SETTLE_S", "AIOPSLAB_RUN_META", "AIOPSLAB_CHAOS_MESH_ALWAYS")


class InitProblemTest(unittest.TestCase):

    def _init_problem(self, chaos_installed=False, **env):
        """Run init_problem on a fake problem and return what happened, in order."""
        events = []
        prob = MagicMock()
        prob.app.namespace = "ns"
        prob.app.deploy.side_effect = lambda: events.append("deploy")
        prob.inject_fault.side_effect = lambda: events.append("inject")
        prob.start_workload = lambda: events.append("workload")
        with patch(f"{MODULE}.Helm.exists_release", return_value=chaos_installed), \
                patch(f"{MODULE}.KubeCtl"), patch(f"{MODULE}.ProblemRegistry"), patch(f"{MODULE}.Session"), \
                patch(f"{MODULE}.Prometheus"), patch(f"{MODULE}.atexit"), \
                patch(f"{MODULE}.SymptomFaultInjector",
                      side_effect=lambda namespace: events.append(f"chaos-mesh:{namespace}")), \
                patch(f"{MODULE}.time.sleep", side_effect=lambda s: events.append(f"sleep:{s}")), \
                patch.dict(os.environ):
            for name in VARIABLES:
                os.environ.pop(name, None)
            os.environ.update(env)
            orchestrator = Orchestrator()
            orchestrator.probs.get_problem_instance.return_value = prob
            orchestrator.probs.get_problem_deployment.return_value = "k8s"
            orchestrator.register_agent(MagicMock(), "agent")
            orchestrator.init_problem("problem-detection-1")
        return events

    def test_default_order_is_unchanged(self):
        self.assertEqual(self._init_problem(), ["deploy", "inject", "workload"])

    def test_settle_runs_the_workload_before_the_fault_and_never_after(self):
        self.assertEqual(self._init_problem(AIOPSLAB_SETTLE_S="120"),
                         ["deploy", "workload", "sleep:120", "inject"])

    def test_chaos_mesh_is_installed_before_the_deploy_only_on_request(self):
        self.assertEqual(self._init_problem(AIOPSLAB_CHAOS_MESH_ALWAYS="1")[0], "chaos-mesh:ns")
        self.assertNotIn("chaos-mesh:ns", self._init_problem())

    def test_chaos_mesh_installed_by_the_problem_is_not_installed_again(self):
        self.assertNotIn("chaos-mesh:ns", self._init_problem(chaos_installed=True, AIOPSLAB_CHAOS_MESH_ALWAYS="1"))

    def test_run_meta_records_the_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "run.json")
            self._init_problem(AIOPSLAB_SETTLE_S="120", AIOPSLAB_RUN_META=path)
            with open(path) as f:
                meta = json.load(f)
        self.assertEqual(meta["settle_s"], 120)
        self.assertLessEqual(meta["t_deployed"], meta["t_workload"])
        self.assertLessEqual(meta["t_workload"], meta["t_inject"])
        self.assertLessEqual(meta["t_inject"], meta["t_injected"])

    def test_without_run_meta_no_file_is_written(self):
        with patch("builtins.open") as mocked_open:
            self._init_problem()
        mocked_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
