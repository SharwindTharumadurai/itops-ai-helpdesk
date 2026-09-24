#!/usr/bin/env python3
import os

import aws_cdk as cdk

from itops.itops_stack import ItOpsStack

app = cdk.App()
ItOpsStack(
    app, "ItOpsAi",
    env=cdk.Environment(account=os.getenv("CDK_DEFAULT_ACCOUNT"), region=os.getenv("CDK_DEFAULT_REGION")),
    description="AI-driven IT support & endpoint automation (free-tier sized)",
)
app.synth()
