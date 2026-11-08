# Trial CloudFormation templates

`render-trial.py` turns the existing `infra/app.py` CDK assembly into two local
CloudFormation templates. It performs no AWS calls or deployment. Run synth from
a full checkout with the intended account and region explicitly selected:

```bash
cd infra
CDK_DEFAULT_ACCOUNT=<approved-account> CDK_DEFAULT_REGION=us-west-2 \
  CDK_OUTDIR=/absolute/path/cdk.out uv run python app.py
cd ..
uv run --script deploy/aws/render-trial.py \
  --cdk-out /absolute/path/cdk.out --output /absolute/path/trial-templates
uv run --script deploy/aws/test_render_trial.py --cdk-out /absolute/path/cdk.out
```

The renderer emits `launchpad-base-trial.template.json`,
`launchpad-console-access.template.json` and `sha256-manifest.json`. The manifest
records source-template, asset-manifest, Lambda-source and output hashes without
machine-specific paths or timestamps. Repeating the same inputs produces the
same bytes. Review the templates and manifest before deployment; both templates
fit CloudFormation's 51,200-byte direct template-body limit.

The base template removes the single reviewed Marketplace subscription statement
and inlines exactly three Lambda assets. Two Python `handler.py` modules become
`index.lambda_handler`; the Node custom-resource provider keeps `index.handler`.
The two schema documents remain build-time metadata: handlers may not read them.
The renderer accepts only the current asset layouts, Python json/time imports,
and the Node provider's Lambda-bundled S3 SDK plus https/url modules. A new Lambda,
extra runtime file, symlink, local import, file read or dynamic dependency loader
requires a packaging review. This is a narrow converter for these three assets.
It removes the bootstrap version parameter/rule and rejects remaining bootstrap
references, so these templates do not require a new `CDKToolkit` stack.

The access template reads `infra/spoke/launchpad-workspace-role.yaml` unchanged
and adds only `ExistingNodeRoleName` and `NodeMayAssumeLaunchpadWorkspace`. The
added inline policy permits that existing node role to assume the one generated
workspace role with the same `ExternalId` as its trust policy. Set
`ExistingNodeRoleName` to the role named by `HubRoleArn`. All upstream workspace
permissions remain visible in the generated template and require review.

After the exact cloud resources and identities are approved, deploy these
templates through the operator's CloudFormation workflow with named-IAM
acknowledgement. Continue the application bootstrap with
`--skip-cdk --skip-observability --skip-skill-lab`; `--skip-cdk` preserves the
reviewed direct-template path. The remaining bootstrap creates shared AgentCore
resources. These flags defer account-wide tracing and Skill Lab; model use stays
within already-enabled access, without new Marketplace subscriptions.

`aws-config.example` is the profile format for the approved instance-role path.
Create its real copy in the credential store: it contains a role ARN, an ExternalId
and `credential_source = Ec2InstanceMetadata`, with no stored access key. The
[Kubernetes guide](../kubernetes/README.md#approved-instance-role-integration)
describes how to mount it and the shared-node identity boundary.
