import unittest
from unittest.mock import patch

from aiopslab.generators.fault.inject_symp import SymptomFaultInjector

# aiopslab.observer loads the kube config at import time
with patch("kubernetes.config.kube_config.load_kube_config"):
    from aiopslab.orchestrator.problems.dev_chaos import dev_chaos_manifest
    from aiopslab.orchestrator.problems.dev_chaos.dev_chaos import LABEL_KEYS


class DevChaosManifestTest(unittest.TestCase):

    def test_stress_chaos_on_hotel(self):
        spec = {"service": "search", "kind": "StressChaos",
                "params": {"stressors": {"cpu": {"workers": 2, "load": 80}}}}
        manifest = dev_chaos_manifest(spec, "test-hotel-reservation", LABEL_KEYS["hotel"])
        self.assertEqual(manifest["kind"], "StressChaos")
        self.assertEqual(manifest["metadata"]["namespace"], "test-hotel-reservation")
        self.assertEqual(manifest["spec"]["stressors"], spec["params"]["stressors"])
        self.assertEqual(manifest["spec"]["selector"], {
            "namespaces": ["test-hotel-reservation"],
            "labelSelectors": {"io.kompose.service": "search"},
        })

    def test_network_chaos_on_social_uses_its_label(self):
        spec = {"service": "user-timeline-mongodb", "kind": "NetworkChaos",
                "params": {"action": "corrupt", "corrupt": {"corrupt": "40", "correlation": "25"}}}
        manifest = dev_chaos_manifest(spec, "test-social-network", LABEL_KEYS["social"])
        self.assertEqual(manifest["spec"]["action"], "corrupt")
        self.assertEqual(manifest["spec"]["selector"]["labelSelectors"], {"service": "user-timeline-mongodb"})


class ChaosMeshInstallTest(unittest.TestCase):

    def _injector(self, already_installed):
        with patch("aiopslab.generators.fault.inject_symp.Helm") as helm, \
                patch("aiopslab.generators.fault.inject_symp.KubeCtl") as kubectl:
            helm.exists_release.return_value = already_installed
            kubectl.return_value.get_container_runtime.return_value = "containerd://1.7"
            SymptomFaultInjector(namespace="ns")
        return helm

    def test_installs_when_missing(self):
        self.assertEqual(self._injector(already_installed=False).install.call_count, 1)

    def test_second_injector_does_not_install_again(self):
        self.assertEqual(self._injector(already_installed=True).install.call_count, 0)


if __name__ == "__main__":
    unittest.main()
