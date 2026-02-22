#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.cuseinfra_stack import CuseinfraStack

app = cdk.App()
CuseinfraStack(app, "CuseinfraStack")
app.synth()
