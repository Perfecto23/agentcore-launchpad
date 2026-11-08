#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""Exercise the renderer with a genuine CDK assembly and invalid package mutations."""

import argparse
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "render_trial",
    Path(__file__).with_name("render-trial.py"),
)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class RenderTrialTests(unittest.TestCase):
    source: Path

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assembly = self.root / "cdk.out"
        shutil.copytree(self.source, self.assembly)
        self.template_file = self.assembly / "launchpad-base.template.json"
        self.template = json.loads(self.template_file.read_text())

    def save_template(self):
        self.template_file.write_text(json.dumps(self.template))

    def asset(self, logical_id):
        key = self.template["Resources"][logical_id]["Properties"]["Code"]["S3Key"]
        return self.assembly / ("asset." + key[:-4])

    def test_genuine_assembly_is_deterministic_and_hashes_match(self):
        first = renderer.render(self.assembly, self.root / "first")
        second = renderer.render(self.assembly, self.root / "second")
        self.assertEqual(first, second)
        for name, details in first["outputs"].items():
            data = (self.root / "first" / name).read_bytes()
            self.assertEqual(data, (self.root / "second" / name).read_bytes())
            self.assertEqual(details["sha256"], renderer.sha256(data))
            self.assertEqual(details["bytes"], len(data))
            self.assertLessEqual(len(data), 51_200)
        base = json.loads((self.root / "first/launchpad-base-trial.template.json").read_bytes())
        functions = [
            v["Properties"]
            for v in base["Resources"].values()
            if v["Type"] == "AWS::Lambda::Function"
        ]
        self.assertEqual(len(functions), 3)
        self.assertTrue(all(set(p["Code"]) == {"ZipFile"} for p in functions))
        self.assertEqual(
            sorted(p["Handler"] for p in functions),
            ["index.handler", "index.lambda_handler", "index.lambda_handler"],
        )
        self.assertNotIn("BootstrapVersion", json.dumps(base))
        self.assertNotIn("aws-marketplace:Subscribe", json.dumps(base))

    def test_access_template_only_adds_the_approved_parameter_and_policy(self):
        original = renderer.yaml.safe_load(renderer.WORKSPACE_TEMPLATE.read_text())
        access = renderer.render_access(original)
        self.assertEqual(
            access.pop("Parameters"),
            {
                **original["Parameters"],
                "ExistingNodeRoleName": {
                    "Type": "String",
                    "Description": "Existing EC2 instance role; must match HubRoleArn.",
                },
            },
        )
        policy = access["Resources"].pop("NodeMayAssumeLaunchpadWorkspace")
        access["Parameters"] = original["Parameters"]
        self.assertEqual(access, original)
        statement = policy["Properties"]["PolicyDocument"]["Statement"]
        self.assertEqual(len(statement), 1)
        self.assertEqual(statement[0]["Action"], "sts:AssumeRole")
        self.assertEqual(statement[0]["Resource"], {"Fn::GetAtt": ["WorkspaceRole", "Arn"]})
        self.assertEqual(
            statement[0]["Condition"], {"StringEquals": {"sts:ExternalId": {"Ref": "ExternalId"}}}
        )

    def test_python_local_import_and_file_read_are_rejected(self):
        path = self.asset("HrDatabaseCB6A2375") / "handler.py"
        original = path.read_text()
        for addition in [
            "import helper",
            "from . import helper",
            "import pathlib",
            "payload = open('tool_schema.json').read()",
        ]:
            with self.subTest(addition=addition):
                path.write_text(original + "\n" + addition + "\n")
                with self.assertRaises(renderer.RenderError):
                    renderer.render(self.assembly, self.root / "invalid")
                self.assertFalse((self.root / "invalid").exists())

    def test_extra_python_runtime_file_is_rejected(self):
        (self.asset("HrDatabaseCB6A2375") / "helper.py").write_text("VALUE = 1\n")
        with self.assertRaisesRegex(renderer.RenderError, "asset layout"):
            renderer.render(self.assembly, self.root / "invalid")

    def test_node_unbundled_and_dynamic_dependencies_are_rejected(self):
        path = (
            self.asset("CustomS3AutoDeleteObjectsCustomResourceProviderHandler9D90184F")
            / "index.js"
        )
        original = path.read_text()
        for addition in [
            "require('third-party')",
            "require('./helper')",
            "require(variable)",
            "require('fs').readFileSync('data.json')",
            "import('./helper.js')",
        ]:
            with self.subTest(addition=addition):
                path.write_text(original + "\n" + addition + ";\n")
                with self.assertRaises(renderer.RenderError):
                    renderer.render(self.assembly, self.root / "invalid")

    def test_unknown_lambda_is_rejected(self):
        self.template["Resources"]["AnotherLambda"] = self.template["Resources"][
            "HrDatabaseCB6A2375"
        ]
        self.save_template()
        with self.assertRaisesRegex(renderer.RenderError, "three reviewed Lambda"):
            renderer.render(self.assembly, self.root / "invalid")

    def test_unknown_asset_is_rejected(self):
        path = self.assembly / "launchpad-base.assets.json"
        assets = json.loads(path.read_text())
        assets["files"]["unknown"] = {"source": {"path": "extra.zip", "packaging": "file"}}
        path.write_text(json.dumps(assets))
        with self.assertRaisesRegex(renderer.RenderError, "unknown asset"):
            renderer.render(self.assembly, self.root / "invalid")

    def test_symlink_asset_is_rejected(self):
        path = self.asset("HrDatabaseCB6A2375") / "handler.py"
        saved = self.root / "external.py"
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaisesRegex(renderer.RenderError, "asset layout"):
            renderer.render(self.assembly, self.root / "invalid")

    def test_large_template_is_rejected_before_output(self):
        self.template["Description"] = "x" * 51_200
        self.save_template()
        with self.assertRaisesRegex(renderer.RenderError, "51200-byte"):
            renderer.render(self.assembly, self.root / "invalid")
        self.assertFalse((self.root / "invalid").exists())

    def test_remaining_bootstrap_dependency_is_rejected(self):
        self.template["Outputs"]["NewBootstrapReference"] = {"Value": {"Ref": "BootstrapVersion"}}
        self.save_template()
        with self.assertRaisesRegex(renderer.RenderError, "bootstrap dependency remains"):
            renderer.render(self.assembly, self.root / "invalid")

    def test_changed_marketplace_statement_requires_review(self):
        statements = self.template["Resources"]["AgentExecutionRoleDefaultPolicyF9CB1199"][
            "Properties"
        ]["PolicyDocument"]["Statement"]
        statement = next(
            s for s in statements if s.get("Sid") == renderer.MARKETPLACE_STATEMENT["Sid"]
        )
        statement["Action"].append("aws-marketplace:Unsubscribe")
        self.save_template()
        with self.assertRaisesRegex(renderer.RenderError, "Marketplace statement changed"):
            renderer.render(self.assembly, self.root / "invalid")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdk-out", type=Path, required=True)
    args = parser.parse_args()
    RenderTrialTests.source = args.cdk_out
    unittest.main(argv=[__file__], verbosity=2)
