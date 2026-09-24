"""ITOps AI - all AWS resources, sized to stay inside the AWS always-free tier.

  API Gateway HTTP API (JWT authorizer = Entra ID)  ->  Lambda "api"  ->  DynamoDB
                                                                       ->  SSM Run Command -> hybrid endpoints
                                                                       ->  Microsoft Graph / LLM API (HTTPS)
  EventBridge (SSM status events + 6h schedule)     ->  Lambda "events" ->  DynamoDB
  SSM Command documents generated from lambda/itops/catalog.json + scripts/
  IAM service role for hybrid activations, $1 budget + optional IAM kill switch
"""
import json
from pathlib import Path

from aws_cdk import (
    Aws, CfnOutput, Duration, RemovalPolicy, Stack,
    aws_apigatewayv2 as apigw,
    aws_budgets as budgets,
    aws_dynamodb as ddb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_ssm as ssm,
)
from aws_cdk.aws_apigatewayv2_authorizers import HttpJwtAuthorizer
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from constructs import Construct

ROOT = Path(__file__).resolve().parents[2]
# Deployment-specific values (tenant, app IDs, email, group IDs) live in this git-ignored file;
# see infra/cdk.local.example.json. They override the shared defaults in cdk.json.
LOCAL_SETTINGS = ROOT / "infra" / "cdk.local.json"
CODE_DIR = ROOT / "lambda" / "itops"
SCRIPTS_DIR = ROOT / "scripts"
MANAGED_TAG = "ITOps:Managed"
SECRET_PARAMS = ["/itops/llm/api-key", "/itops/graph/client-secret", "/itops/llm/groq-api-key"]
STEP_TYPES = {
    "windows": ("aws:runPowerShellScript", "Windows"),
    "linux": ("aws:runShellScript", "Linux"),
    "macos": ("aws:runShellScript", "MacOS"),
}


def build_document(action):
    """Turn one catalog entry into an SSM Command document (schema 2.2).
    Parameter constraints are enforced by SSM itself on top of Lambda validation."""
    params = {}
    for name, spec in action.get("parameters", {}).items():
        p = {"type": "String", "description": spec.get("description", name)}
        if "allowedValues" in spec:
            p["allowedValues"] = spec["allowedValues"]
        if "pattern" in spec:
            p["allowedPattern"] = spec["pattern"]
        if "default" in spec:
            p["default"] = spec["default"]
        params[name] = p
    steps = []
    for platform, rel_path in action["scripts"].items():
        step_action, platform_type = STEP_TYPES[platform]
        body = (SCRIPTS_DIR / rel_path).read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")
        steps.append({
            "action": step_action,
            "name": f"run_{platform}",
            "precondition": {"StringEquals": ["platformType", platform_type]},
            "inputs": {"timeoutSeconds": str(action.get("timeout", 600)), "runCommand": body.split("\n")},
        })
    return {"schemaVersion": "2.2", "description": f"ITOps: {action['title']}",
            "parameters": params, "mainSteps": steps}


class ItOpsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        local = json.loads(LOCAL_SETTINGS.read_text(encoding="utf-8")) if LOCAL_SETTINGS.exists() else {}

        def ctx(key, default=None):
            value = local[key] if key in local else self.node.try_get_context(key)
            return default if value in (None, "") else value

        issuer, audience, budget_email = ctx("jwtIssuer"), ctx("jwtAudience"), ctx("budgetEmail")
        missing = [k for k, v in {"jwtIssuer": issuer, "jwtAudience": audience, "budgetEmail": budget_email}.items() if not v]
        if missing:
            raise ValueError(f"Set these in infra/cdk.local.json (copy cdk.local.example.json): {', '.join(missing)}")
        group_allowlist = ctx("graphGroupAllowlist", {})
        if isinstance(group_allowlist, str):
            group_allowlist = json.loads(group_allowlist)

        catalog = json.loads((CODE_DIR / "catalog.json").read_text(encoding="utf-8"))
        arn = lambda service, resource, account=Aws.ACCOUNT_ID: f"arn:{Aws.PARTITION}:{service}:{Aws.REGION}:{account}:{resource}"

        # ------------------------------------------------------------ DynamoDB
        table = ddb.Table(
            self, "Table",
            partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="SK", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PROVISIONED,  # always-free covers provisioned, not on-demand
            read_capacity=5, write_capacity=5,
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.RETAIN,
        )
        for index in ("GSI1", "GSI2"):
            table.add_global_secondary_index(
                index_name=index,
                partition_key=ddb.Attribute(name=f"{index}PK", type=ddb.AttributeType.STRING),
                sort_key=ddb.Attribute(name=f"{index}SK", type=ddb.AttributeType.STRING),
                read_capacity=5, write_capacity=5,
            )

        # ------------------------------------------------- SSM Command documents
        # Generated scripts are the only path to arbitrary code. With allowGeneratedScripts=false
        # the Lambda role cannot even call the generic Run*Script documents.
        allow_generated = str(ctx("allowGeneratedScripts", True)).lower() == "true"
        document_arns = set()
        if allow_generated:
            document_arns |= {arn("ssm", "document/AWS-RunPowerShellScript", ""),
                              arn("ssm", "document/AWS-RunShellScript", "")}
        for action_id, action in catalog["actions"].items():
            if action["executor"] != "ssm":
                continue
            if action.get("managed_document"):
                document_arns.add(arn("ssm", f"document/{action['document']}", ""))
                continue
            ssm.CfnDocument(
                self, f"Doc{''.join(p.title() for p in action_id.split('_'))}",
                name=action["document"],
                document_type="Command",
                document_format="JSON",
                content=build_document(action),
                update_method="NewVersion",
            )
        document_arns.add(arn("ssm", "document/ITOps-*"))

        # ----------------------------------------------------------- Lambdas
        def log_group(name):
            return logs.LogGroup(self, f"{name}Logs", retention=logs.RetentionDays.TWO_WEEKS,
                                 removal_policy=RemovalPolicy.DESTROY)

        common = dict(
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            code=lambda_.Code.from_asset(str(CODE_DIR), exclude=["__pycache__", "*.pyc"]),
            memory_size=256,
        )
        api_fn = lambda_.Function(
            self, "ApiFunction", handler="api.handler", timeout=Duration.seconds(29),
            log_group=log_group("Api"),
            environment={
                "TABLE_NAME": table.table_name,
                "LLM_PROVIDER": ctx("llmProvider", "gemini"),
                "LLM_MODEL": ctx("llmModel", ""),
                "LLM_API_KEY_PARAM": SECRET_PARAMS[0],
                "LLM_FALLBACK_PROVIDER": ctx("llmFallbackProvider", ""),
                "LLM_FALLBACK_MODEL": ctx("llmFallbackModel", ""),
                "LLM_FALLBACK_KEY_PARAM": SECRET_PARAMS[2],
                "GRAPH_TENANT_ID": ctx("graphTenantId", ""),
                "GRAPH_CLIENT_ID": ctx("graphClientId", ""),
                "GRAPH_SECRET_PARAM": SECRET_PARAMS[1],
                "GRAPH_GROUP_ALLOWLIST": json.dumps(group_allowlist),
                "ALLOWED_UPN_DOMAINS": ctx("allowedUpnDomains", ""),
                "PROTECTED_UPNS": ctx("protectedUpns", ""),
                "ADMIN_ROLE": ctx("adminRole", "ITOps.Admin"),
                "REQUIRE_TWO_PERSON": str(ctx("requireTwoPerson", True)).lower(),
                "ALLOW_GENERATED_SCRIPTS": str(allow_generated).lower(),
            },
            **common,
        )
        events_fn = lambda_.Function(
            self, "EventsFunction", handler="ssm_events.handler", timeout=Duration.seconds(120),
            log_group=log_group("Events"),
            environment={"TABLE_NAME": table.table_name},
            **common,
        )
        table.grant_read_write_data(api_fn)
        table.grant_read_write_data(events_fn)

        # Least privilege: SendCommand only to our documents AND only to tagged managed instances.
        api_fn.add_to_role_policy(iam.PolicyStatement(
            sid="SendToTaggedManagedInstances",
            actions=["ssm:SendCommand"],
            resources=[arn("ssm", "managed-instance/*")],
            conditions={"StringEquals": {f"ssm:resourceTag/{MANAGED_TAG}": "true"}},
        ))
        api_fn.add_to_role_policy(iam.PolicyStatement(
            sid="SendOnlyApprovedDocuments", actions=["ssm:SendCommand"], resources=sorted(document_arns)))
        api_fn.add_to_role_policy(iam.PolicyStatement(
            sid="ReadCommandResults", actions=["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"],
            resources=["*"]))
        api_fn.add_to_role_policy(iam.PolicyStatement(
            sid="ReadOwnSecrets", actions=["ssm:GetParameter"],
            resources=[arn("ssm", f"parameter{p}") for p in SECRET_PARAMS]))

        events_fn.add_to_role_policy(iam.PolicyStatement(
            sid="ReadFleetState",
            actions=["ssm:GetCommandInvocation", "ssm:ListCommandInvocations", "ssm:ListCommands",
                     "ssm:DescribeInstanceInformation"],
            resources=["*"]))
        events_fn.add_to_role_policy(iam.PolicyStatement(
            sid="ReadDeviceTags", actions=["ssm:ListTagsForResource"],
            resources=[arn("ssm", "managed-instance/*")]))

        # --------------------------------------------------------- EventBridge
        events.Rule(
            self, "SsmStatusRule",
            event_pattern=events.EventPattern(
                source=["aws.ssm"], detail_type=["EC2 Command Invocation Status-change Notification"]),
            targets=[targets.LambdaFunction(events_fn, retry_attempts=4)],
        )
        events.Rule(self, "InventorySync", schedule=events.Schedule.rate(Duration.hours(6)),
                    targets=[targets.LambdaFunction(events_fn)])

        # ------------------------------------------------------ HTTP API
        api = apigw.HttpApi(
            self, "HttpApi",
            api_name="itops-api",
            default_authorizer=HttpJwtAuthorizer("EntraJwt", jwt_issuer=issuer, jwt_audience=[audience]),
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=ctx("allowedOrigins", ["http://localhost:5173"]),
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST],
                allow_headers=["authorization", "content-type"],
                max_age=Duration.hours(1),
            ),
        )
        integration = HttpLambdaIntegration("ApiIntegration", api_fn)
        for method, path in [
            (apigw.HttpMethod.POST, "/chat"),
            (apigw.HttpMethod.POST, "/scripts/generate"),
            (apigw.HttpMethod.GET, "/tickets"),
            (apigw.HttpMethod.GET, "/tickets/{ticket_id}"),
            (apigw.HttpMethod.POST, "/tickets/{ticket_id}/approve"),
            (apigw.HttpMethod.POST, "/tickets/{ticket_id}/reject"),
            (apigw.HttpMethod.GET, "/devices"),
            (apigw.HttpMethod.GET, "/catalog"),
        ]:
            api.add_routes(path=path, methods=[method], integration=integration)
        # Throttle the whole API: caps LLM quota burn and any cost from abuse.
        stage = api.default_stage.node.default_child
        stage.default_route_settings = apigw.CfnStage.RouteSettingsProperty(
            throttling_burst_limit=10, throttling_rate_limit=5)

        # ---------------------------------------- Hybrid activation service role
        hybrid_role = iam.Role(
            self, "HybridEndpointRole",
            role_name=f"ITOpsHybridEndpointRole-{Aws.REGION}",
            assumed_by=iam.ServicePrincipal("ssm.amazonaws.com", conditions={
                "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
                "ArnLike": {"aws:SourceArn": arn("ssm", "*")},
            }),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")],
            description="Assumed by SSM Agent on hybrid (on-prem) laptops and servers",
        )
        # AmazonSSMManagedInstanceCore allows ssm:GetParameter on "*". Any local admin on a
        # laptop can use the agent's credentials, so explicitly deny our secrets.
        hybrid_role.add_to_policy(iam.PolicyStatement(
            sid="DenyPlatformSecrets", effect=iam.Effect.DENY,
            actions=["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "ssm:GetParameterHistory"],
            resources=[arn("ssm", "parameter/itops/*")],
        ))

        # ------------------------------------------------ $1 budget (+ kill switch)
        budget_name = "itops-1usd-cap"
        subscriber = [budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=budget_email)]
        notifications = [
            budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    notification_type=kind, comparison_operator="GREATER_THAN",
                    threshold=threshold, threshold_type="PERCENTAGE"),
                subscribers=subscriber)
            for kind, threshold in (("ACTUAL", 1), ("ACTUAL", 50), ("ACTUAL", 100), ("FORECASTED", 100))
        ]
        budget = budgets.CfnBudget(
            self, "Budget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=budget_name, budget_type="COST", time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=1, unit="USD")),
            notifications_with_subscribers=notifications,
        )

        if str(ctx("killSwitch", True)).lower() == "true":
            deny_all = iam.ManagedPolicy(
                self, "KillSwitchDeny",
                description="Attached by AWS Budgets when spend exceeds $1: stops all ITOps Lambda activity",
                statements=[iam.PolicyStatement(effect=iam.Effect.DENY, actions=["*"], resources=["*"])],
            )
            action_role = iam.Role(self, "BudgetActionRole", assumed_by=iam.ServicePrincipal("budgets.amazonaws.com"))
            action_role.add_to_policy(iam.PolicyStatement(
                actions=["iam:AttachRolePolicy", "iam:DetachRolePolicy"],
                resources=[api_fn.role.role_arn, events_fn.role.role_arn],
                conditions={"ArnEquals": {"iam:PolicyARN": deny_all.managed_policy_arn}},
            ))
            kill = budgets.CfnBudgetsAction(
                self, "KillSwitch",
                budget_name=budget_name,
                action_type="APPLY_IAM_POLICY",
                notification_type="ACTUAL",
                approval_model="AUTOMATIC",
                action_threshold=budgets.CfnBudgetsAction.ActionThresholdProperty(type="PERCENTAGE", value=100),
                execution_role_arn=action_role.role_arn,
                definition=budgets.CfnBudgetsAction.DefinitionProperty(
                    iam_action_definition=budgets.CfnBudgetsAction.IamActionDefinitionProperty(
                        policy_arn=deny_all.managed_policy_arn,
                        roles=[api_fn.role.role_name, events_fn.role.role_name])),
                subscribers=[budgets.CfnBudgetsAction.SubscriberProperty(type="EMAIL", address=budget_email)],
            )
            kill.node.add_dependency(budget)

        # ------------------------------------------------------------ outputs
        CfnOutput(self, "ApiUrl", value=api.api_endpoint)
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "HybridRoleName", value=hybrid_role.role_name)
        CfnOutput(self, "EventsFunctionName", value=events_fn.function_name)
