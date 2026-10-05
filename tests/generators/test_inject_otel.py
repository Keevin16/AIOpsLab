import json
import unittest
from unittest.mock import MagicMock

from aiopslab.generators.fault.inject_otel import OtelFaultInjector

# the two flags as rendered by opentelemetry-demo chart 0.42.1
FLAGS = {
    "cartFailure": {
        "defaultVariant": "off",
        "variants": {"10%": 0.1, "100%": 1, "off": 0},
    },
    "productCatalogFailure": {
        "defaultVariant": "off",
        "targeting": {"if": [{"==": [{"var": "product_id"}, "OLJCESPC7Z"]}, "off", "off"]},
        "variants": {"off": False, "on": True},
    },
    "adFailure": {"defaultVariant": "off", "variants": {"on": True, "off": False}},
}


def _injector():
    inj = OtelFaultInjector.__new__(OtelFaultInjector)  # skip KubeCtl(), no cluster here
    inj.namespace, inj.configmap_name = "astronomy-shop", "flagd-config"
    inj.kubectl = MagicMock()
    inj.kubectl.exec_command.side_effect = lambda cmd: (
        json.dumps({"data": {"demo.flagd.json": json.dumps({"flags": FLAGS})}}) if "get configmap" in cmd else ""
    )
    return inj


def _written(inj):
    data = inj.kubectl.create_or_update_configmap.call_args[0][2]
    return json.loads(data["demo.flagd.json"])["flags"]


class OtelFaultInjectorTest(unittest.TestCase):

    def test_cart_failure_uses_a_valid_variant(self):
        inj = _injector()
        inj.inject_fault("cartFailure")
        self.assertEqual(_written(inj)["cartFailure"]["defaultVariant"], "100%")

    def test_product_catalog_failure_flips_the_targeting(self):
        inj = _injector()
        inj.inject_fault("productCatalogFailure")
        self.assertEqual(_written(inj)["productCatalogFailure"]["targeting"]["if"][1], "on")
        inj.recover_fault("productCatalogFailure")
        flag = _written(inj)["productCatalogFailure"]
        self.assertEqual((flag["defaultVariant"], flag["targeting"]["if"][1]), ("off", "off"))

    def test_other_flags_keep_the_on_variant(self):
        inj = _injector()
        inj.inject_fault("adFailure")
        self.assertEqual(_written(inj)["adFailure"]["defaultVariant"], "on")

    def test_invalid_variant_is_refused_before_touching_the_configmap(self):
        inj = _injector()
        FLAGS["cartFailure"]["variants"].pop("100%")
        try:
            with self.assertRaises(ValueError):
                inj.inject_fault("cartFailure")
        finally:
            FLAGS["cartFailure"]["variants"]["100%"] = 1
        inj.kubectl.create_or_update_configmap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
