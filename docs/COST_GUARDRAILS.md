# Cost guardrails: $1 budget cap

## What `cdk deploy` already creates
- **Budget `itops-1usd-cap`**: monthly cost budget of **$1.00**, with email alerts at:
  - actual > 1% ($0.01), the first cent of spend of any kind
  - actual > 50% and > 100%
  - forecasted > 100%
- **Kill switch** (`killSwitch: true`, the default): an AWS Budgets *action* that, when actual spend
  passes $1, **automatically attaches a Deny-`*` IAM policy** to both Lambda roles. From then on the
  Lambdas cannot read DynamoDB, call SSM or read secrets. The system fails closed.
  - To recover: find the cause, then in *Billing → Budgets → itops-1usd-cap → Actions*, **reverse** the
    action (or detach `KillSwitchDeny` from the two roles).

> AWS billing data refreshes a few times a day, so alerts can arrive hours after spend occurs. A budget
> is an alarm, not a real-time hard cap. What actually bounds volume is the API throttle (5 req/s, burst 10).
> The kill switch limits how long an overrun can continue.

Confirm the **"AWS Notification – Subscription Confirmation"** email, or you will get no alerts.

## Manual setup (console), if you are not using CDK or want an account-wide budget
1. *Billing and Cost Management → Budgets → Create budget → Use a template → **Zero spend budget***
   (alerts on the first $0.01), **or** *Customize → Cost budget*:
   - Period: Monthly, recurring. Budgeted amount: **1.00 USD**. Scope: all AWS services.
   - Alert 1: Actual cost > **1%** → your email
   - Alert 2: Actual cost > **100%** → your email
   - Alert 3: Forecasted cost > **100%** → your email
2. *Billing preferences → Alert preferences → **Receive AWS Free Tier alerts*** (emails at 85% of any
   free-tier limit).
3. *Cost Anomaly Detection → Create monitor* (AWS services, daily summary, $1 threshold). This is free.

## Manual setup (CLI)
```powershell
$acct = aws sts get-caller-identity --query Account --output text
@'
{"BudgetName":"account-1usd-cap","BudgetLimit":{"Amount":"1","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}
'@ | Set-Content budget.json -Encoding ascii
@'
[
 {"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":1,"ThresholdType":"PERCENTAGE"},
  "Subscribers":[{"SubscriptionType":"EMAIL","Address":"you@example.com"}]},
 {"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":100,"ThresholdType":"PERCENTAGE"},
  "Subscribers":[{"SubscriptionType":"EMAIL","Address":"you@example.com"}]},
 {"Notification":{"NotificationType":"FORECASTED","ComparisonOperator":"GREATER_THAN","Threshold":100,"ThresholdType":"PERCENTAGE"},
  "Subscribers":[{"SubscriptionType":"EMAIL","Address":"you@example.com"}]}
]
'@ | Set-Content notifications.json -Encoding ascii
aws budgets create-budget --account-id $acct --budget file://budget.json --notifications-with-subscribers file://notifications.json
```

## Why each design choice keeps cost at $0
| Choice | Avoids |
|---|---|
| DynamoDB **provisioned** 5/5 ×3 (15 RCU/WCU) | On-demand DynamoDB is not covered by the always-free tier |
| TTL on execution logs (180 d) and command maps (30 d) | Storage growth toward 25 GB |
| Parameter Store **Standard** + `aws/ssm` key | Secrets Manager ($0.40/secret/month) and customer-managed KMS keys ($1/key/month) |
| SSM **Standard** node tier, Run Command only | Advanced tier ($ per node-hour); Session Manager on hybrid nodes needs Advanced |
| No NAT gateway, no VPC for the Lambdas | A NAT gateway costs ~$32/month; Lambda outside a VPC reaches the LLM and Graph for free |
| 14-day log retention | Unbounded CloudWatch Logs storage |
| No S3 output buckets for SSM | Command output is read with `GetCommandInvocation` (last 24 KB) instead |
| arm64 Lambda at 256 MB | Uses less of the GB-second allowance |
| API throttle | Unbounded Lambda and API Gateway calls, and LLM quota burn |

## Things that are *not* free
- **API Gateway HTTP API** is free for 1M calls/month only during the account's **first 12 months**.
  After that it costs about $1 per million requests, which is fractions of a cent at helpdesk volume. For
  strictly $0 forever, you could swap it for a **Lambda Function URL** (always free), but you would then
  have to validate the Entra JWT inside the Lambda.
- **CDK bootstrap** creates an S3 bucket for deployment assets (a few MB). That is well under a cent a month.
- **AWS free-tier rules changed for accounts created after 15 July 2025** (credit-based Free plan).
  Check *Billing → Free Tier* for which offers apply to your account.
- Running a `FullScan` antivirus action or `install_updates` on many nodes is still free on the AWS side.
  The cost is on the endpoints themselves (CPU and bandwidth).
