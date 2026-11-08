# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Interface to the wrk workload generator."""

from kubernetes import client, config
from aiopslab.paths import BASE_DIR
import os
import shlex
import yaml
import time


def parse_schedule(schedule):
    """Parse "300:20,300:50:2" into [(300, 20, 0), (300, 50, 2)]: seconds, requests per second and mix of each segment.

    The mix is an index into the request mixes of the payload script (WRK_MIX); 0, the default, is the script's usual mix.
    """
    segments = []
    for segment in schedule.split(","):
        values = [int(value) for value in segment.split(":")]
        if len(values) == 2:
            values.append(0)
        seconds, rate, mix = values
        if seconds <= 0 or rate <= 0 or mix < 0:
            raise ValueError(f"Invalid workload segment: {segment}")
        segments.append((seconds, rate, mix))
    return segments


class Wrk:
    def __init__(self, rate, dist="norm", connections=2, duration=6, threads=2, latency=True):
        self.rate = rate
        self.dist = dist
        self.connections = connections
        self.duration = duration
        self.threads = threads
        self.latency = latency

        config.load_kube_config()
    
    def create_configmap(self, name, namespace, payload_script_path):
        with open(payload_script_path, "r") as script_file:
            script_content = script_file.read()

        configmap_body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=name),
            data={payload_script_path.name: script_content},
        )

        api_instance = client.CoreV1Api()
        try:
            print(f"Checking for existing ConfigMap '{name}'...")
            api_instance.delete_namespaced_config_map(name=name, namespace=namespace)
            print(f"ConfigMap '{name}' deleted.")
        except client.exceptions.ApiException as e:
            if e.status != 404:
                print(f"Error deleting ConfigMap '{name}': {e}")
                return

        try:
            print(f"Creating ConfigMap '{name}'...")
            api_instance.create_namespaced_config_map(namespace=namespace, body=configmap_body)
            print(f"ConfigMap '{name}' created successfully.")
        except client.exceptions.ApiException as e:
            print(f"Error creating ConfigMap '{name}': {e}")

    def continuous_command(self, segments, payload_script, url):
        """Shell command that repeats the segments until the cluster is deleted.

        wrk exits at once when it cannot connect, so each segment retries until its own end:
        a frontend that is down for a while must not shift the load schedule.
        The payload script reads the segment's request mix from WRK_MIX.
        """
        connections = os.environ.get("AIOPSLAB_WORKLOAD_CONNECTIONS", self.connections)
        wrk = shlex.join(["wrk", "-D", self.dist, "-t", str(self.threads), "-c", str(connections),
                          "-L", "-s", f"/scripts/{payload_script}", url])
        latency = " --latency" if self.latency else ""
        return (
            'run() { end=$(($(date +%s) + $1)); '
            'while [ "$(date +%s)" -lt "$end" ]; do '
            'echo "segment $(date +%s) $1 $2 $3"; '
            f'WRK_MIX="$3" {wrk} -d "$((end - $(date +%s)))s" -R "$2"{latency} || sleep 5; '
            'done; }; '
            'while true; do ' + ' '.join(f'run {seconds} {rate} {mix};' for seconds, rate, mix in segments) + ' done'
        )

    def create_wrk_job(self, job_name, namespace, payload_script, url):
        wrk_job_yaml = BASE_DIR / "generators" / "workload" / "wrk-job-template.yaml"
        with open(wrk_job_yaml, "r") as f:
            job_template = yaml.safe_load(f)

        job_template["metadata"]["name"] = job_name
        container = job_template["spec"]["template"]["spec"]["containers"][0]
        schedule = os.environ.get("AIOPSLAB_WORKLOAD_SCHEDULE")
        if schedule:
            container["command"] = ["/bin/sh", "-c"]
            container["args"] = [self.continuous_command(parse_schedule(schedule), payload_script, url)]
        else:
            container["args"] = [
                "wrk",
                "-D", self.dist,
                "-t", str(self.threads),
                "-c", str(self.connections),
                "-d", f"{self.duration}s",
                "-L",
                "-s", f"/scripts/{payload_script}",
                url,
                "-R", str(self.rate),
            ]

            if self.latency:
                container["args"].append("--latency")

        job_template["spec"]["template"]["spec"]["volumes"] = [
            {
                "name": "wrk2-scripts",
                "configMap": {"name": "wrk2-payload-script"},
            }
        ]
        job_template["spec"]["template"]["spec"]["containers"][0]["volumeMounts"] = [
            {
                "name": "wrk2-scripts",
                "mountPath": f"/scripts/{payload_script}",
                "subPath": payload_script,
            }
        ]

        api_instance = client.BatchV1Api()
        try:
            existing_job = api_instance.read_namespaced_job(name=job_name, namespace=namespace)
            if existing_job:
                print(f"Job '{job_name}' already exists. Deleting it...")
                api_instance.delete_namespaced_job(
                    name=job_name,
                    namespace=namespace,
                    body=client.V1DeleteOptions(
                        propagation_policy="Foreground"
                    )
                )
                # Creating it again fails until the old pod is gone: a looping pod can take its whole grace period.
                # The 404 of the read ends the wait, in the except below.
                for _ in range(60):
                    api_instance.read_namespaced_job(name=job_name, namespace=namespace)
                    time.sleep(2)
        except client.exceptions.ApiException as e:
            if e.status != 404:
                print(f"Error checking for existing job: {e}")
                return

        try:
            response = api_instance.create_namespaced_job(namespace=namespace, body=job_template)
            print(f"Job created: {response.metadata.name}")
        except client.exceptions.ApiException as e:
            print(f"Error creating job: {e}")
            return

        try:
            while True:
                job_status = api_instance.read_namespaced_job_status(name=job_name, namespace=namespace)
                if job_status.status.ready:
                    print("Job completed successfully.")
                    break
                elif job_status.status.failed:
                    print("Job failed.")
                    break
                time.sleep(5)
        except client.exceptions.ApiException as e:
            print(f"Error monitoring job: {e}")

    def start_workload(self, payload_script, url):
        namespace = "default"
        configmap_name = "wrk2-payload-script"

        self.create_configmap(name=configmap_name, namespace=namespace, payload_script_path=payload_script)

        self.create_wrk_job(
            job_name="wrk2-job",
            namespace=namespace,
            payload_script=payload_script.name,
            url=url
        )

