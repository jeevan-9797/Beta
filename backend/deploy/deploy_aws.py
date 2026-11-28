"""Deploy the BETA demo backend to Mumbai using the current AWS Python SDK."""

import argparse
import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import dotenv_values


DEPLOY_DIR = Path(__file__).resolve().parent
BACKEND = DEPLOY_DIR.parent
ROOT = BACKEND.parent
REGION = "ap-south-1"
TABLE = "BETA_Household"
MODEL = "apac.amazon.nova-micro-v1:0"
GATEWAY = "beta-guardian-gateway"
ENGINE = "beta_guardian_policy_engine"
FUNCTIONS = {
    "beta-errands-mcp": "beta_entrypoints.errands",
    "beta-guardian-mcp": "beta_entrypoints.guardian",
    "beta-shield-mcp": "beta_entrypoints.shield",
    "beta-request-interceptor": "beta_entrypoints.request",
    "beta-response-interceptor": "beta_entrypoints.response",
}
PAYMENT_TOOLS = {"pay_bill", "reorder_medicine"}
SECURITY_PROPERTIES = {
    "frozen": "boolean",
    "scam_flag": "boolean",
    "payee_known": "boolean",
    "assessment_valid": "boolean",
    "approval_valid": "boolean",
    "hour_ist": "integer",
    "payments_last_hour": "integer",
}
INTEGRATIONS = {
    "beta-guardian-mcp": (
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM", "GUARDIAN_WHATSAPP_TO",
    ),
    "beta-errands-mcp": ("GOOGLE_CALENDAR_ID", "GOOGLE_SERVICE_ACCOUNT_JSON"),
}

# The zip keeps backend.* imports and supplies the Lambda entry points missing from Backend.
ENTRYPOINTS = '''\
import json
import os


def tool_name(context):
    return context.client_context.custom["bedrockAgentCoreToolName"].split("___", 1)[1]


def errands(event, context):
    from backend.mcp_errands.handler import handle_tool_call
    return handle_tool_call(tool_name(context), event)


def guardian(event, context):
    from backend.mcp_guardian.handler import handle_tool_call
    return handle_tool_call(tool_name(context), event)


def shield(event, context):
    name = tool_name(context)
    fields = {
        "assess_risk": ("household_id", "snippet", "intent", "amount"),
        "explain_scam": ("scam_type", "language"),
    }
    payload = {key: event[key] for key in fields[name] if key in event}
    payload["action"] = name
    upstream = os.getenv("SHIELD_LAMBDA_ARN")
    if upstream:
        import boto3
        result = boto3.client("lambda").invoke(
            FunctionName=upstream, InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        with result["Payload"] as stream:
            body = stream.read()
        if result.get("FunctionError"):
            return {"success": False, "error": "The upstream Shield Lambda failed."}
        return json.loads(body)
    if os.getenv("BETA_SHIELD_BUNDLED") == "true":
        from src.shield_mcp.server import lambda_handler
        return lambda_handler(payload, context)
    return {"success": False, "error": "Shield is unavailable until its Lambda is configured."}


def request(event, context):
    from backend.interceptors.request_interceptor import lambda_handler
    gateway_request = event["mcp"]["gatewayRequest"]
    body = gateway_request["body"]
    params = body.get("params", {})
    if body.get("method") == "tools/call" and params.get("name") in __PAYMENT_ACTIONS__:
        params.setdefault("arguments", {})
        return lambda_handler(event, context)
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"body": body}},
    }


def sanitize(value, interceptor):
    if isinstance(value, str):
        return interceptor.sanitize_text(value)
    if isinstance(value, list):
        return [sanitize(item, interceptor) for item in value]
    if isinstance(value, dict):
        return {key: sanitize(item, interceptor) for key, item in value.items()}
    return value


def response(event, context):
    from backend.common.config import settings
    from backend.interceptors.response_interceptor import ResponseInterceptor
    mcp = event["mcp"]
    request_body = mcp["gatewayRequest"]["body"]
    gateway_response = mcp["gatewayResponse"]
    body = gateway_response["body"]
    if request_body.get("method") == "tools/call" and isinstance(body, dict):
        params = request_body.get("params", {})
        household = (params.get("arguments") or {}).get("household_id")
        interceptor = ResponseInterceptor(household or settings.DEFAULT_HOUSEHOLD_ID)
        body = dict(body)
        for field in ("result", "error"):
            if field in body:
                body[field] = sanitize(body[field], interceptor)
        result = body.get("result") or {}
        failed = "error" in body or (isinstance(result, dict) and result.get("isError"))
        interceptor.intercept(
            params.get("name"), result, json.dumps(result), "FAILED" if failed else "SUCCESS",
        )
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayResponse": {
            "statusCode": gateway_response["statusCode"], "body": body,
        }},
    }
'''


def settings_from_env(env_file):
    path = env_file or next(
        (path for path in (BACKEND / ".env", ROOT / ".env") if path.is_file()), None
    )
    if path is not None and not path.is_file():
        raise ValueError(f"Environment file does not exist: {path}")
    values = {**(dotenv_values(path, encoding="utf-8-sig") if path else {}), **os.environ}
    upstream = (values.get("SHIELD_LAMBDA_ARN") or "").strip()
    if upstream and not re.fullmatch(
        rf"arn:aws:lambda:{REGION}:\d{{12}}:function:[\w-]+(?::[\w$-]+)?", upstream
    ):
        raise ValueError(f"SHIELD_LAMBDA_ARN must be a Lambda function ARN in {REGION}.")
    if upstream and upstream.split(":")[6] == "beta-shield-mcp":
        raise ValueError("SHIELD_LAMBDA_ARN must point to a separate upstream function.")

    base = {
        "DYNAMODB_TABLE": TABLE,
        "LOCAL_MOCK_DB": "false",
        "BEDROCK_MODEL_ID": values.get("BEDROCK_MODEL_ID") or MODEL,
        "COOLING_OFF_SECONDS": "20",
    }
    bundled = not upstream and (ROOT / "src/shield_mcp/server.py").is_file()
    environments = {name: dict(base) for name in FUNCTIONS}
    for name, keys in INTEGRATIONS.items():
        environments[name].update({key: values[key] for key in keys if values.get(key)})
    calendar = environments["beta-errands-mcp"]
    account = calendar.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if account and not account.startswith("{"):
        account_path = (path.parent if path else ROOT) / account
        account = account_path.read_text(encoding="utf-8-sig")
    if account:
        try:
            account_data = json.loads(account)
        except ValueError:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON must contain a JSON object.") from None
        if not isinstance(account_data, dict):
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON must contain a JSON object.")
        calendar["GOOGLE_SERVICE_ACCOUNT_JSON"] = json.dumps(account_data, separators=(",", ":"))
    shield = environments["beta-shield-mcp"]
    shield.update(MOCK_DB="false", BETA_SHIELD_BUNDLED=str(bundled).lower())
    if upstream:
        shield["SHIELD_LAMBDA_ARN"] = upstream
    for name, environment in environments.items():
        if sum(len(key.encode()) + len(value.encode()) for key, value in environment.items()) > 4096:
            raise ValueError(f"Lambda environment variables exceed 4 KiB for {name}.")
    return environments, bundled, upstream


def tool_schemas():
    def normalize(schema):
        # AgentCore's inline SchemaDefinition cannot carry JSON Schema's enum keyword.
        if "enum" in schema:
            choices = ", ".join(schema.pop("enum"))
            schema["description"] = f"{schema.get('description', '')} Allowed values: {choices}.".strip()
        for child in schema.get("properties", {}).values():
            normalize(child)
        if "items" in schema:
            normalize(schema["items"])

    schemas = {}
    for target, directory in (("Errands", "mcp_errands"), ("Guardian", "mcp_guardian")):
        tools = json.loads((BACKEND / directory / "tool_schema.json").read_text(encoding="utf-8"))
        schemas[target] = tools["tools"]
        for tool in schemas[target]:
            normalize(tool["inputSchema"])
    for tool in schemas["Errands"]:
        if tool["name"] in PAYMENT_TOOLS:
            tool["inputSchema"]["properties"].update({
                key: {"type": kind, "description": "Computed by the gateway request interceptor."}
                for key, kind in SECURITY_PROPERTIES.items()
            })
    schemas["Shield"] = [
        {
            "name": "assess_risk",
            "description": "Assess scam risk for a household conversation.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "household_id": {"type": "string"}, "snippet": {"type": "string"},
                    "intent": {"type": "string"}, "amount": {"type": "number"},
                },
                "required": ["household_id", "snippet", "intent"],
            },
        },
        {
            "name": "explain_scam",
            "description": "Explain a scam pattern and give advice in the requested language.",
            "inputSchema": {
                "type": "object",
                "properties": {"scam_type": {"type": "string"}, "language": {"type": "string"}},
                "required": ["scam_type"],
            },
        },
    ]
    return schemas


def cedar_policies(gateway_arn, schemas):
    source = (BACKEND / "policies/beta_policies.cedar").read_text(encoding="utf-8")
    source = re.sub(r"^\s*//[^\n]*", "", source, flags=re.MULTILINE)
    allowed_actions = {f"{target}___{tool['name']}" for target, tools in schemas.items() for tool in tools}
    for index, raw in enumerate((part.strip() for part in source.split(";") if part.strip()), 1):
        # The local policy also lists two legacy Shield tools outside the deployed contract.
        raw = re.sub(
            r'AgentCore::Action::"([^"]+)"(,?)',
            lambda match: match[0] if match[1] in allowed_actions else "", raw,
        )
        raw = re.sub(r",\s*\]", "\n    ]", raw)
        raw = re.sub(r"\bresource(?=\s*\))", 'resource == AgentCore::Gateway::"<GATEWAY_ARN>"', raw)
        raw = raw.replace("<GATEWAY_ARN>", gateway_arn)
        # JSON Schema `number` maps to Cedar Decimal. Optional injected facts need `has` guards.
        raw = re.sub(
            r"context\.input\.amount <= (\d+)",
            r'context.input.amount.lessThanOrEqual(decimal("\1.0"))', raw,
        )
        if raw.startswith("permit") and "when {" in raw:
            fields = sorted(set(re.findall(r"context\.input\.(\w+)", raw)))
            guards = "".join(f"    context.input has {field} &&\n" for field in fields)
            raw = raw.replace("when {\n", f"when {{\n{guards}", 1)
        yield f"beta_rule_{index:02}", raw + ";"


def build_zip(bundled_shield):
    with TemporaryDirectory(prefix="beta-lambda-") as temporary:
        workspace = Path(temporary)
        stage = workspace / "package"
        # Lambda Python 3.12 x86_64 runs on Amazon Linux 2023. Resolve Linux wheels on any host.
        platforms = [f"manylinux_2_{minor}_x86_64" for minor in range(34, 16, -1)]
        platforms.append("manylinux2014_x86_64")
        command = [
            sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile",
            "--only-binary=:all:", "--python-version", "3.12", "--implementation", "cp",
            "--abi", "cp312", "--target", str(stage),
        ]
        for platform in platforms:
            command.extend(("--platform", platform))
        dependencies = ["twilio>=8.11,<10", "google-api-python-client>=2.110,<3"]
        if bundled_shield:
            dependencies.append("fastmcp>=2,<3")
        subprocess.run(command + dependencies, check=True, stdout=sys.stderr)

        actions = tuple(sorted(f"Errands___{name}" for name in PAYMENT_TOOLS))
        (stage / "beta_entrypoints.py").write_text(
            ENTRYPOINTS.replace("__PAYMENT_ACTIONS__", repr(actions)), encoding="utf-8",
        )
        sources = [BACKEND / directory for directory in ("common", "mcp_errands", "mcp_guardian", "interceptors")]
        if bundled_shield:
            sources.extend((ROOT / "src/shield_mcp", ROOT / "src/shared"))
        files = [(path, path.relative_to(stage)) for path in stage.rglob("*") if path.is_file()]
        for directory in sources:
            files.extend(
                (path, path.relative_to(ROOT)) for path in directory.rglob("*")
                if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts
            )
        if sum(path.stat().st_size for path, _ in files) > 250 * 1024**2:
            raise RuntimeError("The Lambda bundle exceeds the 250 MiB uncompressed limit.")
        archive = workspace / "beta.zip"
        with ZipFile(archive, "w") as bundle:
            for path, name in sorted(files, key=lambda entry: entry[1].as_posix()):
                bundle.writestr(ZipInfo(name.as_posix()), path.read_bytes(), compress_type=ZIP_DEFLATED)
        if archive.stat().st_size > 50 * 1024**2:
            raise RuntimeError("The Lambda bundle exceeds the 50 MiB direct upload limit.")
        return archive.read_bytes()


def error_code(error):
    return error.response["Error"]["Code"]


def wait_for(description, read, ready, timeout=600):
    deadline = time.monotonic() + timeout
    while True:
        resource = read()
        if ready(resource):
            return resource
        status = resource.get("status", "")
        if "FAILED" in status or status == "DELETING":
            raise RuntimeError(f"{description} is {status}: {resource.get('statusReasons', [])}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for {description}: {status or resource}")
        time.sleep(5)


def ensure_table(dynamodb):
    db = dynamodb.meta.client
    key_schema = {"PK": "HASH", "SK": "RANGE"}
    try:
        db.describe_table(TableName=TABLE)
    except db.exceptions.ResourceNotFoundException:
        db.create_table(
            TableName=TABLE, BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": name, "KeyType": kind} for name, kind in key_schema.items()],
            AttributeDefinitions=[{"AttributeName": name, "AttributeType": "S"} for name in key_schema],
        )
    db.get_waiter("table_exists").wait(TableName=TABLE)
    table = db.describe_table(TableName=TABLE)["Table"]
    keys = {key["AttributeName"]: key["KeyType"] for key in table["KeySchema"]}
    types = {key["AttributeName"]: key["AttributeType"] for key in table["AttributeDefinitions"]}
    if keys != key_schema or any(types[name] != "S" for name in key_schema):
        raise RuntimeError(f"{TABLE} must use string PK (HASH) and SK (RANGE).")
    if table.get("BillingModeSummary", {}).get("BillingMode") != "PAY_PER_REQUEST":
        db.update_table(TableName=TABLE, BillingMode="PAY_PER_REQUEST")
        db.get_waiter("table_exists").wait(TableName=TABLE)

    def ttl():
        return db.describe_time_to_live(TableName=TABLE)["TimeToLiveDescription"]

    state = ttl()
    if state["TimeToLiveStatus"] == "DISABLING":
        state = wait_for(f"{TABLE} TTL disabling", ttl, lambda value: value["TimeToLiveStatus"] == "DISABLED")
    if state["TimeToLiveStatus"] == "DISABLED":
        db.update_time_to_live(TableName=TABLE, TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"})
    elif state.get("AttributeName") != "ttl":
        raise RuntimeError(f"{TABLE} already has TTL on a different attribute. Migrate it to 'ttl' before rerunning.")

    # Use the existing seed script's payload with insert-only writes so a rerun cannot reset demo state.
    sys.path.insert(0, str(ROOT))
    from backend.common.db import _to_dynamo_friendly
    from backend.scripts.seed_data import get_seed_payload

    table = dynamodb.Table(TABLE)
    seeded = 0
    for item in get_seed_payload():
        if item.get("action") == "HOUSEHOLD_INITIALIZED":
            item["SK"] = "AUDIT#0000-SEED"
        try:
            table.put_item(Item=_to_dynamo_friendly(item), ConditionExpression="attribute_not_exists(PK)")
        except db.exceptions.ConditionalCheckFailedException:
            continue
        seeded += 1
    print(f"{TABLE}: inserted {seeded} seed records.", file=sys.stderr)


def statement(actions, resources):
    return {"Effect": "Allow", "Action": actions, "Resource": resources}


def document(*statements):
    return json.dumps({"Version": "2012-10-17", "Statement": statements})


def ensure_role(iam, name, service, permissions, condition=None):
    trust = {"Effect": "Allow", "Action": "sts:AssumeRole", "Principal": {"Service": service}}
    if condition:
        trust["Condition"] = condition
    try:
        role = iam.get_role(RoleName=name)["Role"]
    except iam.exceptions.NoSuchEntityException:
        role = iam.create_role(RoleName=name, AssumeRolePolicyDocument=document(trust))["Role"]
        iam.get_waiter("role_exists").wait(RoleName=name)
    else:
        if role["AssumeRolePolicyDocument"] != json.loads(document(trust)):
            iam.update_assume_role_policy(RoleName=name, PolicyDocument=document(trust))
    iam.put_role_policy(RoleName=name, PolicyName="BetaDeploymentPermissions", PolicyDocument=document(*permissions))
    return role["Arn"]


def model_resources(session, account, model, bundled):
    base = f"arn:aws:bedrock:{REGION}"
    profile_model = re.fullmatch(r"[a-z]+\.(amazon\.nova-[\w-]+-v\d+:\d+)", model)
    if model == MODEL and not bundled:
        # The placeholder and ARN bridge do not invoke Bedrock. Keep the requested permissions for the demo.
        return [f"{base}:{account}:inference-profile/{model}", "arn:aws:bedrock:*::foundation-model/amazon.nova-micro-v1:0"]
    if not profile_model:
        raise ValueError("BEDROCK_MODEL_ID must be an Amazon Nova cross-region inference profile ID.")
    try:
        profile = session.client("bedrock", region_name=REGION).get_inference_profile(inferenceProfileIdentifier=model)
    except ClientError as error:
        if error_code(error) not in {"ResourceNotFoundException", "ValidationException"}:
            raise
        raise RuntimeError(
            f"Bedrock did not find {model} in {REGION}. Set BEDROCK_MODEL_ID=apac.amazon.nova-lite-v1:0 "
            "for bundled Shield, or configure SHIELD_LAMBDA_ARN."
        ) from None
    return [profile["inferenceProfileArn"], *(entry["modelArn"] for entry in profile["models"])]


def iam_propagation(operation, **kwargs):
    deadline = time.monotonic() + 120
    while True:
        try:
            return operation(**kwargs)
        except ClientError as error:
            message = error.response["Error"]["Message"].lower()
            if ("role" not in message or "assum" not in message) or time.monotonic() >= deadline:
                raise
            time.sleep(5)


def ensure_lambdas(client, role, environments, archive):
    arns = {}
    archive_hash = base64.b64encode(sha256(archive).digest()).decode("ascii")
    for name, handler in FUNCTIONS.items():
        identifier = {"FunctionName": name}
        configuration = {
            "Role": role, "Runtime": "python3.12", "Handler": handler,
            "Timeout": 60, "MemorySize": 512, "Environment": {"Variables": environments[name]},
        }
        try:
            client.get_function_configuration(**identifier)
        except client.exceptions.ResourceNotFoundException:
            result = iam_propagation(
                client.create_function, **identifier, **configuration,
                Architectures=["x86_64"], Code={"ZipFile": archive},
            )
            client.get_waiter("function_active_v2").wait(**identifier)
        else:
            client.get_waiter("function_active_v2").wait(**identifier)
            client.get_waiter("function_updated_v2").wait(**identifier)
            result = client.get_function_configuration(**identifier)
            if result["CodeSha256"] != archive_hash or result.get("Architectures") != ["x86_64"]:
                client.update_function_code(**identifier, ZipFile=archive, Architectures=["x86_64"])
                client.get_waiter("function_updated_v2").wait(**identifier)
            current = {key: result.get(key) for key in configuration}
            current["Environment"] = {"Variables": result.get("Environment", {}).get("Variables", {})}
            if current != configuration:
                result = iam_propagation(client.update_function_configuration, **identifier, **configuration)
                client.get_waiter("function_updated_v2").wait(**identifier)
        arns[name] = result["FunctionArn"]
    return arns


def by_name(operation, response_key, name, **scope):
    matches = []
    while True:
        page = operation(**scope)
        matches.extend(item for item in page[response_key] if item["name"] == name)
        if not page.get("nextToken"):
            break
        scope["nextToken"] = page["nextToken"]
    if len(matches) > 1:
        raise RuntimeError(f"Multiple AWS resources are named {name}.")
    return matches[0] if matches else None


def ready(operation, description, active, **identifier):
    return wait_for(description, lambda: operation(**identifier), lambda result: result["status"] == active)


def stage_gateway(control, configuration):
    gateway = by_name(control.list_gateways, "items", GATEWAY)
    if gateway is None:
        gateway = iam_propagation(control.create_gateway, **configuration, authorizerType="NONE")
    identity = {"gatewayIdentifier": gateway["gatewayId"]}
    gateway = ready(control.get_gateway, GATEWAY, "READY", **identity)
    # Keep target and policy changes behind IAM during setup, including deploys that fail partway.
    if gateway["authorizerType"] != "AWS_IAM":
        control.update_gateway(**identity, **configuration, authorizerType="AWS_IAM")
        gateway = ready(control.get_gateway, GATEWAY, "READY", **identity)
    return gateway


def register_targets(control, gateway_id, schemas, arns):
    tool_names = []
    scope = {"gatewayIdentifier": gateway_id}
    for name, tools in schemas.items():
        target = by_name(control.list_gateway_targets, "items", name, **scope)
        config = {
            "name": name,
            "targetConfiguration": {"mcp": {"lambda": {
                "lambdaArn": arns[f"beta-{name.lower()}-mcp"],
                "toolSchema": {"inlinePayload": tools},
            }}},
            "credentialProviderConfigurations": [{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        }
        if target is None:
            target = control.create_gateway_target(**scope, **config)
        else:
            identifier = {**scope, "targetId": target["targetId"]}
            target = ready(control.get_gateway_target, name, "READY", **identifier)
            if any(target.get(key) != value for key, value in config.items()):
                control.update_gateway_target(**identifier, **config)
        target = ready(control.get_gateway_target, name, "READY", **scope, targetId=target["targetId"])
        registered = target["targetConfiguration"]["mcp"]["lambda"]["toolSchema"]["inlinePayload"]
        tool_names.extend(f"{name}___{tool['name']}" for tool in registered)
    return tool_names


def install_policies(control, policies):
    engine = by_name(control.list_policy_engines, "policyEngines", ENGINE)
    if engine is None:
        engine = control.create_policy_engine(name=ENGINE)
    scope = {"policyEngineId": engine["policyEngineId"]}
    engine = ready(control.get_policy_engine, ENGINE, "ACTIVE", **scope)
    for name, source in policies:
        policy = by_name(control.list_policies, "policies", name, **scope)
        definition = {"cedar": {"statement": source}}
        # BETA deliberately has unconditional demo permits and standalone forbid rules.
        config = {"definition": definition, "validationMode": "IGNORE_ALL_FINDINGS"}
        if policy is None:
            policy = control.create_policy(**scope, name=name, **config)
        else:
            identity = {**scope, "policyId": policy["policyId"]}
            if policy["status"] in {"CREATING", "UPDATING"}:
                policy = ready(control.get_policy, name, "ACTIVE", **identity)
            if policy.get("enforcementMode") == "LOG_ONLY":
                config["enforcementMode"] = "ACTIVE"
            if policy["status"] != "ACTIVE" or policy.get("definition") != definition or "enforcementMode" in config:
                control.update_policy(**identity, **config)
        ready(control.get_policy, name, "ACTIVE", **scope, policyId=policy["policyId"])
    return engine["policyEngineArn"]


def deploy(profile, env_file):
    environments, bundled, upstream = settings_from_env(env_file)
    schemas = tool_schemas()
    session = boto3.Session(profile_name=profile, region_name=REGION)
    options = {"region_name": REGION, "config": Config(retries={"mode": "standard", "total_max_attempts": 8})}
    account = session.client("sts", **options).get_caller_identity()["Account"]
    control = session.client("bedrock-agentcore-control", **options)
    if not hasattr(control, "create_policy_engine"):
        raise RuntimeError("Upgrade boto3 to a version with AgentCore Policy support.")
    model = environments["beta-shield-mcp"]["BEDROCK_MODEL_ID"]
    bedrock_arns = model_resources(session, account, model, bundled)
    if not bundled and not upstream:
        print("Shield will report unavailable until its upstream Lambda is configured or aiml is merged.", file=sys.stderr)
    print(f"Building BETA Lambda archive for AWS account {account} in {REGION}.", file=sys.stderr)
    archive = build_zip(bundled)
    ensure_table(session.resource("dynamodb", **options))

    table_arn = f"arn:aws:dynamodb:{REGION}:{account}:table/{TABLE}"
    log_arn = f"arn:aws:logs:{REGION}:{account}:log-group:/aws/lambda"
    permissions = [
        statement("dynamodb:*", [table_arn, f"{table_arn}/*"]),
        statement("logs:CreateLogGroup", f"{log_arn}/beta-*"),
        statement(["logs:CreateLogStream", "logs:PutLogEvents"], f"{log_arn}/beta-*:log-stream:*"),
        statement("bedrock:InvokeModel", bedrock_arns),
    ]
    if upstream:
        permissions.append(statement("lambda:InvokeFunction", upstream))
    iam = session.client("iam", **options)
    execution_role = ensure_role(iam, "BetaLambdaExecutionRole", "lambda.amazonaws.com", permissions)
    arns = ensure_lambdas(session.client("lambda", **options), execution_role, environments, archive)

    agentcore_arn = f"arn:aws:bedrock-agentcore:{REGION}:{account}"
    gateway_scope = f"{agentcore_arn}:gateway/{GATEWAY}-*"
    engine_scope = f"{agentcore_arn}:policy-engine/{ENGINE}-*"
    gateway_role = ensure_role(
        iam, "BetaAgentCoreGatewayRole", "bedrock-agentcore.amazonaws.com",
        [
            statement("lambda:InvokeFunction", list(arns.values())),
            statement(["bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions"],
                      [gateway_scope, engine_scope]),
            statement("bedrock-agentcore:GetPolicyEngine", engine_scope),
        ],
        {"StringEquals": {"aws:SourceAccount": account}, "ArnLike": {"aws:SourceArn": gateway_scope}},
    )
    configuration = {
        "name": GATEWAY, "protocolType": "MCP", "roleArn": gateway_role,
        "interceptorConfigurations": [
            {"interceptor": {"lambda": {"arn": arns[f"beta-{point.lower()}-interceptor"]}},
             "interceptionPoints": [point], "inputConfiguration": {"passRequestHeaders": False}}
            for point in ("REQUEST", "RESPONSE")
        ],
    }
    gateway = stage_gateway(control, configuration)
    gateway_id = gateway["gatewayId"]
    tools = register_targets(control, gateway_id, schemas, arns)
    engine_arn = install_policies(control, cedar_policies(gateway["gatewayArn"], schemas))
    enforcement = {"arn": engine_arn, "mode": "ENFORCE"}
    control.update_gateway(
        gatewayIdentifier=gateway_id, **configuration,
        authorizerType="NONE", policyEngineConfiguration=enforcement,
    )
    gateway = ready(control.get_gateway, GATEWAY, "READY", gatewayIdentifier=gateway_id)
    if gateway.get("policyEngineConfiguration") != enforcement or gateway["authorizerType"] != "NONE":
        raise RuntimeError("Gateway did not report the requested policy and authorizer.")
    return {**{key: gateway[key] for key in ("gatewayUrl", "gatewayId", "gatewayArn")}, "tools": sorted(tools)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="Named AWS profile. Defaults to the boto3 credential chain.")
    parser.add_argument("--env-file", type=Path, help="Dotenv path. Defaults to backend/.env, then .env.")
    args = parser.parse_args()
    try:
        outputs = deploy(args.profile, args.env_file)
        rendered = json.dumps(outputs, indent=2) + "\n"
        output = DEPLOY_DIR / "deployed_outputs.json"
        with TemporaryDirectory(dir=DEPLOY_DIR) as temporary:
            staged = Path(temporary) / output.name
            staged.write_text(rendered, encoding="utf-8")
            staged.replace(output)
        print(rendered, end="")
    except (BotoCoreError, ClientError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Deployment failed: {error}\n")


if __name__ == "__main__":
    main()
