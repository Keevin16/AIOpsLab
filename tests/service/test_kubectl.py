from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiopslab.service.kubectl import KubeCtl


def _pod(phase, ready_values=None):
    container_statuses = None
    if ready_values is not None:
        container_statuses = [
            SimpleNamespace(ready=ready) for ready in ready_values
        ]

    return SimpleNamespace(
        status=SimpleNamespace(
            phase=phase,
            container_statuses=container_statuses,
        )
    )


class PodReadinessTest(unittest.TestCase):

    def test_running_pod_with_ready_containers_satisfies_readiness(self):
        pod = _pod("Running", [True, True])

        self.assertTrue(KubeCtl._pod_is_ready_or_succeeded(pod))

    def test_succeeded_cleanup_pod_satisfies_readiness(self):
        pod = _pod("Succeeded", [False])

        self.assertTrue(KubeCtl._pod_is_ready_or_succeeded(pod))

    def test_running_pod_with_unready_container_blocks_readiness(self):
        pod = _pod("Running", [True, False])

        self.assertFalse(KubeCtl._pod_is_ready_or_succeeded(pod))

    def test_pending_pod_without_container_statuses_blocks_readiness(self):
        pod = _pod("Pending")

        self.assertFalse(KubeCtl._pod_is_ready_or_succeeded(pod))

    def test_failed_pod_with_unready_container_blocks_readiness(self):
        pod = _pod("Failed", [False])

        self.assertFalse(KubeCtl._pod_is_ready_or_succeeded(pod))


def _snapshot(*pods):
    """One list_pods answer; each pod is (ready, restart_count) of its single container."""
    return SimpleNamespace(items=[
        SimpleNamespace(status=SimpleNamespace(
            phase="Running", container_statuses=[SimpleNamespace(ready=ready, restart_count=restarts)]))
        for ready, restarts in pods
    ])


def _polls(snapshots, **kwargs):
    """Run wait_for_ready on successive snapshots (the last one repeats) and return how many polls it took."""
    kubectl = KubeCtl.__new__(KubeCtl)  # skip load_kube_config, no cluster here
    polls = []

    def list_pods(_namespace):
        polls.append(None)
        return snapshots[min(len(polls), len(snapshots)) - 1]

    kubectl.list_pods = list_pods
    with patch("aiopslab.service.kubectl.time.sleep"):
        kubectl.wait_for_ready("test-hotel-reservation", sleep=2, **kwargs)
    return len(polls)


# Hotel Reservation startup: geo is Ready for ~16 s, crashes on MongoDB (not ready, restart 1), then comes back
GEO_RACE = [_snapshot((True, 0), (True, 0))] * 8 + [_snapshot((True, 0), (False, 1))] * 3 + [_snapshot((True, 0), (True, 1))]


class WaitForReadyStabilityTest(unittest.TestCase):

    def test_without_stable_for_returns_at_first_all_ready_poll(self):
        self.assertEqual(_polls(GEO_RACE), 1)  # the old behaviour: the workload would start on a doomed geo

    def test_stable_for_waits_past_the_crash(self):
        # all ready again at poll 12 (22 s), then 30 s of stability: poll 27 (52 s)
        self.assertEqual(_polls(GEO_RACE, stable_for=30), 27)

    def test_restart_between_polls_resets_the_window(self):
        # never seen not ready, but the restart count moves at poll 6 (10 s): stable until 40 s, poll 21
        snapshots = [_snapshot((True, 0))] * 5 + [_snapshot((True, 1))]
        self.assertEqual(_polls(snapshots, stable_for=30), 21)

    def test_never_stable_times_out(self):
        flapping = [_snapshot((True, 0)), _snapshot((False, 1))] * 20
        with self.assertRaises(Exception):
            _polls(flapping, stable_for=30, max_wait=60)
