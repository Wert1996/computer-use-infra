import aws_cdk as cdk
import aws_cdk.aws_cognito as cognito
import aws_cdk.aws_dynamodb as dynamodb
import aws_cdk.aws_lambda as _lambda
import aws_cdk.aws_iam as iam
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as apigwv2_auth
from constructs import Construct

LAMBDA_DIR = "lambda"


class Authentication(Construct):
    def __init__(self, scope: Construct, construct_id: str) -> None:
        super().__init__(scope, construct_id)

        # Create Cognito User Pool
        self.user_pool = cognito.UserPool(
            self, "UserPool",
            user_pool_name="cuseinfra-users",
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=cdk.RemovalPolicy.DESTROY,  # For development only
        )

        # Create User Pool Client
        self.user_pool_client = self.user_pool.add_client(
            "UserPoolClient",
            user_pool_client_name="cuseinfra-api-client",
            auth_flows=cognito.AuthFlow(
                admin_user_password=True,
                user_password=True,
                user_srp=True,
            ),
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ],
        )

        # Create DynamoDB table for user permissions
        self.permissions_table = dynamodb.Table(
            self, "UserPermissions",
            table_name="user_permissions",
            partition_key=dynamodb.Attribute(
                name="userId", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="tenantId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.DESTROY,  # For development only
        )

        # Add GSI for querying by tenantId
        self.permissions_table.add_global_secondary_index(
            index_name="tenantId-index",
            partition_key=dynamodb.Attribute(
                name="tenantId", type=dynamodb.AttributeType.STRING
            ),
        )

        # Create pre-token generation Lambda
        self.pretokengen_fn = _lambda.Function(
            self, "PreTokenGenFn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset(f"{LAMBDA_DIR}/cognito_pretokengen"),
            timeout=cdk.Duration.seconds(10),
            environment={
                "PERMISSIONS_TABLE": self.permissions_table.table_name,
            },
        )

        # Grant read access to permissions table
        self.permissions_table.grant_read_data(self.pretokengen_fn)

        # Add trigger to User Pool
        self.user_pool.add_trigger(
            cognito.UserPoolOperation.PRE_TOKEN_GENERATION,
            self.pretokengen_fn,
        )

        # Create JWT authorizer
        self.jwt_authorizer = apigwv2_auth.HttpJwtAuthorizer(
            "JwtAuthorizer",
            jwt_audience=[self.user_pool_client.user_pool_client_id],
            jwt_issuer=f"https://cognito-idp.{cdk.Aws.REGION}.amazonaws.com/{self.user_pool.user_pool_id}",
        )

        # Export useful values
        self.user_pool_id = self.user_pool.user_pool_id
        self.permissions_table_name = self.permissions_table.table_name

        # CloudFormation outputs
        cdk.CfnOutput(
            self, "UserPoolId",
            value=self.user_pool.user_pool_id,
            export_name="CuseinfraUserPoolId",
        )
        cdk.CfnOutput(
            self, "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
            export_name="CuseinfraUserPoolClientId",
        )
        cdk.CfnOutput(
            self, "PermissionsTableName",
            value=self.permissions_table.table_name,
            export_name="CuseinfraPermissionsTable",
        )