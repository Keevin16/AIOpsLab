"""Interface to the OpenTelemetry Astronomy Shop application"""

import time

from aiopslab.service.helm import Helm
from aiopslab.service.kubectl import KubeCtl
from aiopslab.service.apps.base import Application
from aiopslab.paths import ASTRONOMY_SHOP_METADATA


class AstronomyShop(Application):
    def __init__(self):
        super().__init__(ASTRONOMY_SHOP_METADATA)
        self.load_app_json()
        self.kubectl = KubeCtl()
        self.create_namespace()

    def load_app_json(self):
        super().load_app_json()
        metadata = self.get_app_json()
        self.frontend_service = "frontend-proxy"
        self.frontend_port = 8080

    def deploy(self):
        """Deploy the Helm configurations."""
        self.kubectl.create_namespace_if_not_exist(self.namespace)
        Helm.add_repo(
            "open-telemetry",
            "https://open-telemetry.github.io/opentelemetry-helm-charts",
        )
        # Readiness probes (the chart has none); its schema allows only httpGet, useless on the gRPC services
        values = ASTRONOMY_SHOP_METADATA.parent / "astronomy-shop-values.yaml"
        extra_args = [*self.helm_configs.get("extra_args", []), f"--values {values}", "--skip-schema-validation"]
        Helm.install(**{**self.helm_configs, "extra_args": extra_args})
        # The OpenTelemetry Demo chart deploys ~29 pods (kafka, opensearch, grafana,
        # jaeger, ...) and can take longer than assert_if_deployed's default 300s
        # readiness timeout, especially on a cold image cache.
        self.kubectl.wait_for_ready(self.namespace, max_wait=900, stable_for=30)
        self.kubectl.wait_for_endpoints(self.namespace)

    def delete(self):
        """Delete the Helm configurations."""
        Helm.uninstall(**self.helm_configs)
        self.kubectl.delete_namespace(self.helm_configs["namespace"])
        time.sleep(30)

    def cleanup(self):
        Helm.uninstall(**self.helm_configs)
        self.kubectl.delete_namespace(self.helm_configs["namespace"])


# Run this code to test installation/deletion
# if __name__ == "__main__":
#     shop = AstronomyShop()
#     shop.deploy()
#     shop.delete()
