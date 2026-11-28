# Deploy BETA to Mumbai

The deployer sets up the BETA demo in AWS `ap-south-1`. It creates or updates the DynamoDB table, two IAM roles, five Python 3.12 Lambda functions, the AgentCore MCP gateway, and its Cedar policy engine.

The finished gateway uses `authorizerType=NONE`. Anyone who can reach the URL can call tools that Cedar allows, including some tools that write data or send notifications. Use an isolated demo AWS account, fictional household data, and sandbox integration accounts. Sharing the URL does not restrict access to its recipients. [AgentCore inbound authorization](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html)

## Prerequisites

- Check out `Backend`. Use Python 3.12 with pip and access to PyPI or your configured package mirror. The deployer builds an x86-64 Linux Lambda zip from compatible wheels, including when run on Windows.
- Sign in using your AWS credential chain or a named profile. The deploying identity needs STS caller identity, DynamoDB table and TTL management and seed writes, IAM role and inline-policy management, `iam:PassRole` on the two BETA roles, Lambda create/update/read actions, and AgentCore gateway, target, policy-engine, and policy create/update/read/list actions. Policy upload also needs `bedrock-agentcore:InvokeGateway` and `bedrock-agentcore:ManageResourceScopedPolicy`. Resolving a bundled Shield model requires `bedrock:GetInferenceProfile`. [AgentCore policy permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html)
- For Twilio or Google Calendar, copy `backend/.env.example` to `backend/.env` and fill in test credentials. The deployer reads `backend/.env`, then falls back to the repository-root `.env`. Use `--env-file` to select a different file. Shell variables take precedence. Twilio variables go to the Guardian Lambda. Google Calendar variables go to the Errands Lambda. `GOOGLE_SERVICE_ACCOUNT_JSON` accepts a JSON object or a path relative to the selected `.env` file. Its contents count toward Lambda’s 4 KiB environment limit.

## Choose a Shield configuration

- **Bundled**. If the checkout contains `src/shield_mcp/server.py`, the zip includes Jyotir’s Shield server and the adapter calls its `lambda_handler`. It sets `MOCK_DB=false` for that server. The requested default is `apac.amazon.nova-micro-v1:0`. AWS’s Nova Micro model card currently lists US and EU inference profiles. If Bedrock cannot resolve the requested profile in Mumbai, set `BEDROCK_MODEL_ID=apac.amazon.nova-lite-v1:0` in `.env` and rerun. The deployer grants `bedrock:InvokeModel` on the selected profile and its destination models. That IAM action also authorizes Converse. [Nova Micro](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-micro.html) · [Nova Lite](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-lite.html) · [Converse permissions](https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html)
- **Upstream Lambda**. Set `SHIELD_LAMBDA_ARN` in `.env` to an existing Shield function or alias ARN in `ap-south-1`. This takes precedence over bundled source. The upstream function needs Jyotir’s direct JSON handler contract and its own runtime configuration. For a cross-account ARN, grant `BetaLambdaExecutionRole` invocation permission in the upstream function’s resource policy as well.
- **Placeholder**. When both the source and ARN are absent, the deployment still registers `Shield___assess_risk` and `Shield___explain_scam`. Both return a Shield-unavailable result until one of the other configurations is deployed.

## Deploy

Run from the repository root with Python 3.12 active. Omit `--profile` to use the default boto3 credential chain.

```shell
python -m pip install --upgrade 'boto3>=1.43.119' python-dotenv
python backend/deploy/deploy_aws.py --profile beta-demo
```

The script inserts missing `kamala` demo seed records using the payload from `backend/scripts/seed_data.py`. Reruns preserve existing records, including household rules and the original voice assessment. The seeded assessment expires after five minutes. Generate a fresh assessment when testing the financial tools. DynamoDB can report TTL as `ENABLING` for up to an hour after accepting the request. [DynamoDB TTL](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/time-to-live-ttl-how-to.html)

During target and policy updates the gateway uses `AWS_IAM`. It switches to `NONE` with the policy engine in `ENFORCE` mode after all three targets and six policy rules report ready. A rerun briefly requires IAM again. If deployment fails during that stage, resolve the reported AWS error and rerun to complete the cutover.

The deployer scopes the source Cedar rules to the live gateway ARN and adapts numeric and optional input checks to AgentCore’s schema. It omits legacy Shield actions outside the two-tool contract. `Errands___reorder_medicines` is registered from the repository schema and denied by default, as the Cedar payment rules authorize the singular `Errands___reorder_medicine`. AgentCore still validates the policy schema when semantic findings are ignored for the existing unconditional demo permits and standalone forbids. [AgentCore policy schema constraints](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-schema-constraints.html)

## Verify and share

After a successful deployment, the script prints the live `gatewayUrl`, `gatewayId`, `gatewayArn`, and registered `Target___tool` names as JSON. It saves the same JSON to the gitignored `backend/deploy/deployed_outputs.json`. Cedar may filter the tools visible to a caller.

Send an MCP `tools/list` request to the printed `gatewayUrl`. For example, in PowerShell from the repository root:

```powershell
$gateway = Get-Content backend/deploy/deployed_outputs.json -Raw | ConvertFrom-Json
$body = @{jsonrpc='2.0'; id=1; method='tools/list'} | ConvertTo-Json -Compress
Invoke-RestMethod -Uri $gateway.gatewayUrl -Method Post -ContentType 'application/json' `
  -Headers @{Accept='application/json, text/event-stream'; 'MCP-Protocol-Version'='2025-03-26'} -Body $body
```

Confirm that `Errands___get_profile` is listed. You can then call it with `household_id` set to `kamala` to verify the seeded data and both interceptors before sharing the URL.
