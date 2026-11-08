#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""Render the reviewed, bootstrap-free trial templates from a local CDK assembly."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_TEMPLATE = REPO_ROOT / "infra/spoke/launchpad-workspace-role.yaml"
TEMPLATE_LIMIT = 51_200
LAMBDA_LAYOUTS = {
    "CustomS3AutoDeleteObjectsCustomResourceProviderHandler9D90184F": (
        "nodejs24.x",
        "index.handler",
        "index.js",
        {"index.js"},
    ),
    "HrDatabaseCB6A2375": (
        "python3.12",
        "handler.lambda_handler",
        "handler.py",
        {"handler.py", "tool_schema.json"},
    ),
    "OfficeFacts563FC41A": (
        "python3.12",
        "handler.lambda_handler",
        "handler.py",
        {"handler.py", "openapi.yaml"},
    ),
}
MARKETPLACE_STATEMENT = {
    "Action": ["aws-marketplace:Subscribe", "aws-marketplace:ViewSubscriptions"],
    "Condition": {"StringEquals": {"aws:CalledViaLast": "bedrock-mantle.amazonaws.com"}},
    "Effect": "Allow",
    "Resource": "*",
    "Sid": "MarketplaceOperationsFromBedrockMantleFor3pModels",
}


class RenderError(ValueError):
    """The assembly no longer matches the supported trial package contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RenderError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_python(source: str) -> None:
    tree = ast.parse(source)
    require(
        any(isinstance(n, ast.FunctionDef) and n.name == "lambda_handler" for n in tree.body),
        "Python asset must define lambda_handler",
    )
    # The two sample handlers use only json/time; expanding this set needs a package review.
    forbidden = {
        "open",
        "__file__",
        "__builtins__",
        "__import__",
        "eval",
        "exec",
        "compile",
        "globals",
        "locals",
        "vars",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            require(
                all(n.name in {"json", "time"} for n in node.names),
                "Python asset imports a module outside the reviewed json/time dependencies",
            )
        elif isinstance(node, ast.ImportFrom):
            require(
                node.level == 0 and node.module in {"json", "time"},
                "Python asset has a local or unsupported import",
            )
        elif isinstance(node, ast.Name):
            require(node.id not in forbidden, "Python asset uses file access or dynamic code")
        elif isinstance(node, ast.Attribute):
            require(
                node.attr
                not in {
                    "open",
                    "read_text",
                    "read_bytes",
                    "write_text",
                    "write_bytes",
                    "__dict__",
                    "__globals__",
                    "__builtins__",
                },
                "Python asset uses file access or dynamic module inspection",
            )


def validate_node(source: str) -> None:
    calls = re.findall(r"\brequire\s*\(\s*(['\"])([^'\"]+)\1\s*\)", source)
    require(
        len(calls) == len(re.findall(r"\brequire\b", source)),
        "Node asset contains a dynamic or aliased require",
    )
    require(
        all(
            module in {"@aws-sdk/client-s3", "https", "url", "node:https", "node:url"}
            for _, module in calls
        ),
        "Node asset requires an unbundled dependency",
    )
    require(
        not re.search(r"\b(?:import|eval|Function|createRequire)\b", source),
        "Node asset uses an unsupported dynamic module/code loader",
    )
    require("module.exports" in source, "Node asset must be the CommonJS index.js handler")


def asset_source(cdk_out: Path, assets: dict, props: dict, layout: tuple) -> tuple[str, str]:
    runtime, handler, filename, expected_files = layout
    require(
        props.get("Runtime") == runtime and props.get("Handler") == handler,
        "Lambda runtime or handler changed; review its inline packaging",
    )
    code = props.get("Code", {})
    require(
        set(code) == {"S3Bucket", "S3Key"} and isinstance(code["S3Key"], str),
        "Lambda Code must reference one synthesized S3 zip asset",
    )
    require(
        re.fullmatch(r"[0-9a-f]{64}\.zip", code["S3Key"]) is not None,
        "Lambda S3Key must be a synthesized asset hash",
    )
    asset_id = code["S3Key"][:-4]
    entry = assets["files"].get(asset_id, {})
    require(
        entry.get("source") == {"path": f"asset.{asset_id}", "packaging": "zip"},
        "Lambda source must be a local CDK zip asset directory",
    )
    destinations = list(entry.get("destinations", {}).values())
    require(
        len(destinations) == 1
        and destinations[0].get("objectKey") == code["S3Key"]
        and destinations[0].get("bucketName") == code["S3Bucket"],
        "Lambda asset destination does not match the synthesized Code reference",
    )
    directory = cdk_out / entry["source"]["path"]
    require(
        directory.is_dir() and not directory.is_symlink(), "Missing/plain asset directory required"
    )
    children = list(directory.iterdir())
    require(
        {p.name for p in children} == expected_files
        and all(p.is_file() and not p.is_symlink() for p in children),
        f"Unsupported asset layout: {directory.name}; extra runtime files cannot be inlined",
    )
    source = (directory / filename).read_text(encoding="utf-8")
    if runtime.startswith("python"):
        validate_python(source)
    else:
        validate_node(source)
    return asset_id, source


def render_base(cdk_out: Path) -> tuple[dict, dict]:
    template = json.loads((cdk_out / "launchpad-base.template.json").read_text())
    assets = json.loads((cdk_out / "launchpad-base.assets.json").read_text())
    resources = template["Resources"]
    functions = {k for k, v in resources.items() if v["Type"] == "AWS::Lambda::Function"}
    require(
        functions == set(LAMBDA_LAYOUTS), "Expected exactly the three reviewed Lambda resources"
    )
    require(not assets.get("dockerImages"), "Docker assets are outside this trial renderer")
    seen_assets, source_hashes = set(), {}
    for logical_id, layout in LAMBDA_LAYOUTS.items():
        props = resources[logical_id]["Properties"]
        asset_id, source = asset_source(cdk_out, assets, props, layout)
        seen_assets.add(asset_id)
        source_hashes[logical_id] = sha256(source.encode())
        props["Code"] = {"ZipFile": source}
        if layout[0].startswith("python"):
            props["Handler"] = "index.lambda_handler"
    extra = {k: v for k, v in assets["files"].items() if k not in seen_assets}
    require(
        len(seen_assets) == 3
        and len(extra) == 1
        and next(iter(extra.values()))["source"]
        == {
            "path": "launchpad-base.template.json",
            "packaging": "file",
        },
        "Asset manifest contains an unknown asset",
    )

    statements = resources["AgentExecutionRoleDefaultPolicyF9CB1199"]["Properties"][
        "PolicyDocument"
    ]["Statement"]
    matches = [s for s in statements if s.get("Sid") == MARKETPLACE_STATEMENT["Sid"]]
    require(matches == [MARKETPLACE_STATEMENT], "Marketplace statement changed; review its removal")
    statements.remove(matches[0])
    require(
        "aws-marketplace:Subscribe" not in json.dumps(template),
        "Another Marketplace Subscribe grant remains",
    )
    require(
        "BootstrapVersion" in template.get("Parameters", {})
        and "CheckBootstrapVersion" in template.get("Rules", {}),
        "Expected CDK bootstrap parameter and rule",
    )
    del template["Parameters"]["BootstrapVersion"]
    del template["Rules"]["CheckBootstrapVersion"]
    require(
        "BootstrapVersion" not in json.dumps(template)
        and "cdk-hnb659fds" not in json.dumps(template),
        "An unsupported CDK bootstrap dependency remains",
    )
    return template, source_hashes


def render_access(workspace: dict) -> dict:
    template = copy.deepcopy(workspace)
    require(
        "ExistingNodeRoleName" not in template["Parameters"]
        and "NodeMayAssumeLaunchpadWorkspace" not in template["Resources"],
        "Workspace template already contains trial access additions",
    )
    template["Parameters"]["ExistingNodeRoleName"] = {
        "Type": "String",
        "Description": "Existing EC2 instance role; must match HubRoleArn.",
    }
    template["Resources"]["NodeMayAssumeLaunchpadWorkspace"] = {
        "Type": "AWS::IAM::Policy",
        "Properties": {
            "PolicyName": "assume-launchpad-trial-workspace",
            "Roles": [{"Ref": "ExistingNodeRoleName"}],
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "sts:AssumeRole",
                        "Resource": {"Fn::GetAtt": ["WorkspaceRole", "Arn"]},
                        "Condition": {"StringEquals": {"sts:ExternalId": {"Ref": "ExternalId"}}},
                    }
                ],
            },
        },
    }
    return template


def render(cdk_out: Path, output: Path) -> dict:
    base, source_hashes = render_base(cdk_out)
    workspace_bytes = WORKSPACE_TEMPLATE.read_bytes()
    access = render_access(yaml.safe_load(workspace_bytes))
    outputs = {
        "launchpad-base-trial.template.json": (
            json.dumps(base, separators=(",", ":")) + "\n"
        ).encode(),
        "launchpad-console-access.template.json": (json.dumps(access, indent=2) + "\n").encode(),
    }
    for name, data in outputs.items():
        require(
            len(data) <= TEMPLATE_LIMIT, f"{name} exceeds CloudFormation's 51200-byte body limit"
        )
    manifest = {
        "inputs": {
            "launchpad-base.template.json": sha256(
                (cdk_out / "launchpad-base.template.json").read_bytes()
            ),
            "launchpad-base.assets.json": sha256(
                (cdk_out / "launchpad-base.assets.json").read_bytes()
            ),
            "infra/spoke/launchpad-workspace-role.yaml": sha256(workspace_bytes),
        },
        "lambda_sources": source_hashes,
        "outputs": {
            name: {"sha256": sha256(data), "bytes": len(data)} for name, data in outputs.items()
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        (output / name).write_bytes(data)
    (output / "sha256-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdk-out", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = render(args.cdk_out, args.output)
    except (
        RenderError,
        OSError,
        KeyError,
        TypeError,
        SyntaxError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as exc:
        parser.exit(2, f"Trial template rendering failed: {exc}\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
