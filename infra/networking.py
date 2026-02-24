import aws_cdk as cdk
import aws_cdk.aws_ec2 as ec2
from constructs import Construct


class Networking(Construct):
    def __init__(self, scope: Construct, construct_id: str) -> None:
        super().__init__(scope, construct_id)

        self.vpc = ec2.Vpc(
            self, "Vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=1,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="AgentIsolated",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )

        self.agent_subnets = self.vpc.select_subnets(subnet_group_name="AgentIsolated")

        for subnet in self.agent_subnets.subnets:
            nacl = ec2.NetworkAcl(
                self, f"AgentNacl-{subnet.node.id}",
                vpc=self.vpc,
                subnet_selection=ec2.SubnetSelection(subnets=[subnet]),
            )
            nacl.add_entry(
                "DenyVpcOutbound",
                cidr=ec2.AclCidr.ipv4("10.0.0.0/16"),
                rule_number=100,
                traffic=ec2.AclTraffic.all_traffic(),
                direction=ec2.TrafficDirection.EGRESS,
                rule_action=ec2.Action.DENY,
            )
            nacl.add_entry(
                "DenyImdsOutbound",
                cidr=ec2.AclCidr.ipv4("169.254.169.254/32"),
                rule_number=101,
                traffic=ec2.AclTraffic.all_traffic(),
                direction=ec2.TrafficDirection.EGRESS,
                rule_action=ec2.Action.DENY,
            )
            nacl.add_entry(
                "AllowHttpsOutbound",
                cidr=ec2.AclCidr.any_ipv4(),
                rule_number=200,
                traffic=ec2.AclTraffic.tcp_port(443),
                direction=ec2.TrafficDirection.EGRESS,
                rule_action=ec2.Action.ALLOW,
            )
            nacl.add_entry(
                "AllowHttpOutbound",
                cidr=ec2.AclCidr.any_ipv4(),
                rule_number=201,
                traffic=ec2.AclTraffic.tcp_port(80),
                direction=ec2.TrafficDirection.EGRESS,
                rule_action=ec2.Action.ALLOW,
            )
            nacl.add_entry(
                "AllowEphemeralInbound",
                cidr=ec2.AclCidr.any_ipv4(),
                rule_number=100,
                traffic=ec2.AclTraffic.tcp_port_range(1024, 65535),
                direction=ec2.TrafficDirection.INGRESS,
                rule_action=ec2.Action.ALLOW,
            )

        self.agent_security_group = ec2.SecurityGroup(
            self, "AgentSG",
            vpc=self.vpc,
            description="Security group for agent Fargate tasks",
            allow_all_outbound=False,
        )
        self.agent_security_group.add_egress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "Allow HTTPS outbound",
        )
        self.agent_security_group.add_egress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(80),
            "Allow HTTP outbound",
        )
