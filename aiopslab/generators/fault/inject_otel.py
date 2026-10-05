import json
import subprocess
from aiopslab.generators.fault.base import FaultInjector
from aiopslab.service.kubectl import KubeCtl


class OtelFaultInjector(FaultInjector):
    def __init__(self, namespace: str):
        self.namespace = namespace
        self.kubectl = KubeCtl()
        self.configmap_name = "flagd-config"

    def inject_fault(self, feature_flag: str):
        command = (
            f"kubectl get configmap {self.configmap_name} -n {self.namespace} -o json"
        )
        try:
            output = self.kubectl.exec_command(command)
            configmap = json.loads(output)
        except subprocess.CalledProcessError:
            raise ValueError(
                f"ConfigMap '{self.configmap_name}' not found in namespace '{self.namespace}'."
            )
        except json.JSONDecodeError:
            raise ValueError(
                f"Error decoding JSON for ConfigMap '{self.configmap_name}'."
            )

        flagd_data = json.loads(configmap["data"]["demo.flagd.json"])

        if feature_flag in flagd_data["flags"]:
            flag = flagd_data["flags"][feature_flag]
            if feature_flag == "productCatalogFailure":
                # the targeting wins over defaultVariant and returns "off" in both branches
                flag["targeting"]["if"][1] = "on"
            variant = {"paymentFailure": "100%", "cartFailure": "100%", "imageSlowLoad": "10sec"}.get(
                feature_flag, "on"
            )
            # flagd rejects the WHOLE config (every flag, every service) on an invalid default variant
            if variant not in flag["variants"]:
                raise ValueError(
                    f"Variant '{variant}' is not valid for feature flag '{feature_flag}': {list(flag['variants'])}"
                )
            flag["defaultVariant"] = variant
        else:
            raise ValueError(
                f"Feature flag '{feature_flag}' not found in ConfigMap '{self.configmap_name}'."
            )

        updated_data = {"demo.flagd.json": json.dumps(flagd_data, indent=2)}
        self.kubectl.create_or_update_configmap(
            self.configmap_name, self.namespace, updated_data
        )

        self.kubectl.exec_command(
            f"kubectl rollout restart deployment flagd -n {self.namespace}"
        )
        
        print(f"Fault injected: Feature flag '{feature_flag}' set to '{variant}'.")

    def recover_fault(self, feature_flag: str):
        command = (
            f"kubectl get configmap {self.configmap_name} -n {self.namespace} -o json"
        )
        try:
            output = self.kubectl.exec_command(command)
            configmap = json.loads(output)
        except subprocess.CalledProcessError:
            raise ValueError(
                f"ConfigMap '{self.configmap_name}' not found in namespace '{self.namespace}'."
            )
        except json.JSONDecodeError:
            raise ValueError(
                f"Error decoding JSON for ConfigMap '{self.configmap_name}'."
            )

        flagd_data = json.loads(configmap["data"]["demo.flagd.json"])

        if feature_flag in flagd_data["flags"]:
            flagd_data["flags"][feature_flag]["defaultVariant"] = "off"
            if feature_flag == "productCatalogFailure":
                flagd_data["flags"][feature_flag]["targeting"]["if"][1] = "off"
        else:
            raise ValueError(
                f"Feature flag '{feature_flag}' not found in ConfigMap '{self.configmap_name}'."
            )

        updated_data = {"demo.flagd.json": json.dumps(flagd_data, indent=2)}
        self.kubectl.create_or_update_configmap(
            self.configmap_name, self.namespace, updated_data
        )

        self.kubectl.exec_command(
            f"kubectl rollout restart deployment flagd -n {self.namespace}"
        )
        print(f"Fault recovered: Feature flag '{feature_flag}' set to 'off'.")


# Example usage:
# if __name__ == "__main__":
#     namespace = "astronomy-shop"
#     feature_flag = "adServiceFailure"

#     injector = OtelFaultInjector(namespace)

#     injector.inject_fault(feature_flag)
#     injector.recover_fault(feature_flag)
