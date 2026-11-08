import os
import unittest
from unittest.mock import MagicMock, patch

from aiopslab.service.apps.socialnet import SocialNetwork

MODULE = "aiopslab.service.apps.socialnet"


def _app():
    with patch.object(SocialNetwork, "__init__", lambda self: None):
        app = SocialNetwork()
    app.frontend_service, app.frontend_port, app.namespace = "nginx-thrift", 8080, "ns"
    app.helm_configs, app.kubectl = {}, MagicMock()
    return app


class SocialGraphTest(unittest.TestCase):

    def _init(self, output):
        with patch(f"{MODULE}.subprocess.Popen") as popen, patch(f"{MODULE}.subprocess.run") as run, \
                patch(f"{MODULE}.time.sleep"):
            run.return_value.stdout = output
            try:
                _app().init_social_graph("socfb-Reed98")
            finally:
                popen.return_value.terminate.assert_called_once()  # the port-forward never outlives the load
            return run.call_args.args[0]

    def test_loads_the_graph_through_a_port_forward(self):
        command = self._init("Registering Users...\nSucceeded: 962\nAdding follows...\nSucceeded: 37624\n")
        self.assertIn("--graph=socfb-Reed98", command)
        self.assertIn("--port=18080", command)

    def test_a_partial_graph_is_an_error(self):
        with self.assertRaises(RuntimeError):
            self._init("Succeeded: 962\nSucceeded: 37000\nFailed: 624 Error: Internal Server Error\n")

    def test_deploy_loads_the_graph_only_when_asked(self):
        for env, calls in (({}, 0), ({"AIOPSLAB_SOCIAL_GRAPH": "socfb-Reed98"}, 1)):
            app = _app()
            with patch(f"{MODULE}.Helm"), patch.object(app, "init_social_graph") as init, patch.dict(os.environ, env):
                if not env:
                    os.environ.pop("AIOPSLAB_SOCIAL_GRAPH", None)
                app.deploy()
            self.assertEqual(init.call_count, calls)


if __name__ == "__main__":
    unittest.main()
