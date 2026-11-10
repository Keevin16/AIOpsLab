# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Interface to the social network application from DeathStarBench"""

import os
import subprocess
import sys
import time

from aiopslab.service.helm import Helm
from aiopslab.service.kubectl import KubeCtl
from aiopslab.service.apps.base import Application
from aiopslab.paths import TARGET_MICROSERVICES
from aiopslab.paths import SOCIAL_NETWORK_METADATA


class SocialNetwork(Application):
    def __init__(self):
        super().__init__(SOCIAL_NETWORK_METADATA)
        self.load_app_json()
        self.kubectl = KubeCtl()
        self.local_tls_path = (
            TARGET_MICROSERVICES / "socialNetwork/helm-chart/socialnetwork"
        )
        self.create_namespace()
        self.create_tls_secret()

    def load_app_json(self):
        super().load_app_json()
        metadata = self.get_app_json()
        self.frontend_service = metadata.get("frontend_service", "nginx-thrift")
        self.frontend_port = metadata.get("frontend_port", 8080)

    def create_tls_secret(self):
        check_sec = f"kubectl get secret mongodb-tls -n {self.namespace}"
        result = self.kubectl.exec_command(check_sec)
        if "notfound" in result.lower():
            create_sec_command = (
                f"kubectl create secret generic mongodb-tls "
                f"--from-file=tls.pem={self.local_tls_path}/tls.pem "
                f"--from-file=ca.crt={self.local_tls_path}/ca.crt "
                f"-n {self.namespace}"
            )
            create_result = self.kubectl.exec_command(create_sec_command)
            print(f"TLS secret created: {create_result.strip()}")
        else:
            print("TLS secret already exists. Skipping creation.")

    def deploy(self):
        """Deploy the Helm configurations with architecture-aware image selection."""
        node_architectures = self.kubectl.get_node_architectures()
        is_arm = any(arch in ["arm64", "aarch64"] for arch in node_architectures)

        if is_arm:
            # Use the ARM-compatible image for media-frontend
            if "extra_args" not in self.helm_configs:
                self.helm_configs["extra_args"] = []

            self.helm_configs["extra_args"].append(
                "--set media-frontend.container.image=jacksonarthurclark/media-frontend"
            )
            self.helm_configs["extra_args"].append(
                "--set media-frontend.container.imageVersion=latest"
            )

        configs = dict(self.helm_configs)
        if os.getenv("AIOPSLAB_SOCIAL_SAMPLING"):  # share of requests traced; the chart's default is 0.01
            configs["extra_args"] = [*configs.get("extra_args", []),
                                     f"--set global.jaeger.samplerParam={float(os.environ['AIOPSLAB_SOCIAL_SAMPLING'])}"]
        Helm.install(**configs)
        # Thrift calls go through the ClusterIPs, which refuse connections until the endpoints turn ready.
        # 900 s: on a slow disk the images take minutes to unpack; a slow deploy is not a failed one
        self.kubectl.wait_for_ready(self.namespace, stable_for=30, max_wait=900)
        self.kubectl.wait_for_endpoints(self.namespace)
        if os.getenv("AIOPSLAB_SOCIAL_GRAPH"):
            self.init_social_graph(os.environ["AIOPSLAB_SOCIAL_GRAPH"])

    def init_social_graph(self, graph, port=18080):
        """Register the users and follows of `graph` (e.g. socfb-Reed98), as the DeathStarBench README prescribes.

        Without them every compose-post finds no follower: no home timeline is written, and reading one returns nothing.
        """
        forward = subprocess.Popen(
            ["kubectl", "port-forward", f"svc/{self.frontend_service}", f"{port}:{self.frontend_port}", "-n", self.namespace],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(5)
            started = time.time()
            out = subprocess.run(
                [sys.executable, "scripts/init_social_graph.py", f"--graph={graph}", "--ip=127.0.0.1", f"--port={port}"],
                cwd=TARGET_MICROSERVICES / "socialNetwork", capture_output=True, text=True, check=True).stdout
        finally:
            forward.terminate()
        summary = [line for line in out.splitlines() if line.startswith(("Succeeded", "Failed"))]
        print(f"Social graph {graph} loaded in {time.time() - started:.0f} s: {summary}")
        if any(line.startswith("Failed") for line in summary):
            raise RuntimeError(f"Social graph {graph} only partly loaded: {summary}")

    def delete(self):
        """Delete the Helm configurations."""
        Helm.uninstall(**self.helm_configs)

    def cleanup(self):
        """Delete the entire namespace for the social network application."""
        Helm.uninstall(**self.helm_configs)
        # self.kubectl.delete_namespace(self.namespace)
        # time.sleep(15)
