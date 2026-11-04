"""Parametric Chaos Mesh fault, used to tune an anomaly detector on faults that are not test scenarios.

AIOPSLAB_DEV_CHAOS holds the target service, the Chaos Mesh kind and its spec, for example:
{"service": "search", "kind": "StressChaos", "params": {"stressors": {"cpu": {"workers": 2, "load": 80}}}}
"""

import json
import os

from aiopslab.generators.fault.inject_symp import SymptomFaultInjector
from aiopslab.orchestrator.problems.no_op import NoOpDetection

LABEL_KEYS = {"hotel": "io.kompose.service", "social": "service"}


def dev_chaos_manifest(spec: dict, namespace: str, label_key: str) -> dict:
    return {
        "apiVersion": "chaos-mesh.org/v1alpha1",
        "kind": spec["kind"],
        "metadata": {"name": "dev-chaos", "namespace": namespace},
        "spec": {
            **spec["params"],
            "mode": "one",
            "duration": "200s",
            "selector": {
                "namespaces": [namespace],
                "labelSelectors": {label_key: spec["service"]},
            },
        },
    }


# ponytail: eval() stays the noop's (expects "No"); dev runs have no agent, override it if one ever runs here
class DevChaosDetection(NoOpDetection):
    def __init__(self, app_name: str):
        super().__init__(app_name)
        self.spec = json.loads(os.environ["AIOPSLAB_DEV_CHAOS"])
        self.faulty_service = self.spec["service"]
        self.injector = SymptomFaultInjector(namespace=self.namespace)

    def inject_fault(self):
        print("== Fault Injection ==")
        manifest = dev_chaos_manifest(self.spec, self.namespace, LABEL_KEYS[self.app_name])
        self.injector.create_chaos_experiment(manifest, "dev-chaos")
        print(f"Service: {self.faulty_service} | Namespace: {self.namespace}\n")

    def recover_fault(self):
        print("== Fault Recovery ==")
        self.injector.delete_chaos_experiment("dev-chaos")
