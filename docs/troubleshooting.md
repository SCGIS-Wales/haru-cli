# Troubleshooting

Start with `haru doctor`. It checks configuration, sign-in, and Bedrock
permissions, and prints what to fix.

```bash
haru doctor                # configuration, token, identity, Bedrock reachability
haru doctor --invoke       # definitive: makes one real (billable) Bedrock call
haru doctor --all-roles    # probe every account and role you are assigned
haru doctor --json         # machine-readable, for a support ticket
```

## "`haru login` works but `haru chat` fails"

This is the common case, and it is usually not a bug. **Signing in and being
allowed to call Bedrock are two different permissions.**

haru's path is: browser SSO → `sso:GetRoleCredentials` → an assumed role in
**your own AWS account** → `bedrock-runtime` Converse. Signing in proves only
that IAM Identity Center assigned you a role; calling Bedrock additionally
requires that role to hold `bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream`, and that Bedrock model access is
enabled for the Claude models in that account and region.

### Kiro or Amazon Q working tells you nothing about haru

Kiro CLI and Amazon Q authenticate through the same Identity Center, then send
the SSO token as a bearer credential to the **managed Amazon Q service**. They
never call Bedrock in your account, so they need no `bedrock:*` IAM
permissions at all — [Kiro's IAM documentation](https://kiro.dev/docs/cli/enterprise/iam/)
lists `sso:*`, `user-subscriptions:*`, and `q:*` actions and no
`bedrock:InvokeModel`.

Consequently an Amazon Q permission set (names like `AmazonQUsers`) is
**expected to fail** with haru, and there is no Kiro role to copy. Run
`haru doctor --all-roles` to find which of your assigned roles can actually
reach Bedrock:

```
ACCOUNT       NAME                ROLE                 CREDS   BEDROCK   INVOKE
881490127383  amazonq-prod        AmazonQUsers         ok      denied    denied
111122223333  ml-sandbox          BedrockDeveloper     ok      ok        ok
```

Then re-run `haru login` and choose the working combination, or pin it with
`auth.sso.account_id` and `auth.sso.role_name`.

## Policy to give your AWS administrator

Attach to the permission set you sign in with. **Both invoke statements are
required.** haru defaults to `us.` cross-region inference profiles, so Bedrock
authorizes against the underlying foundation-model ARN in *every region the
profile routes to* — a policy granting only the inference-profile ARN produces
an `AccessDeniedException` naming a foundation-model ARN you never configured.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeViaInferenceProfile",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": "arn:aws:bedrock:us-east-1:ACCOUNT_ID:inference-profile/us.anthropic.claude-*"
    },
    {
      "Sid": "InvokeUnderlyingFoundationModelsInEveryRoutedRegion",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-*",
        "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-*",
        "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-*"
      ]
    },
    {
      "Sid": "GuardrailsWhenConfigured",
      "Effect": "Allow",
      "Action": "bedrock:ApplyGuardrail",
      "Resource": "arn:aws:bedrock:us-east-1:ACCOUNT_ID:guardrail/*"
    },
    {
      "Sid": "Diagnostics",
      "Effect": "Allow",
      "Action": ["bedrock:ListFoundationModels", "bedrock:GetInferenceProfile"],
      "Resource": "*"
    }
  ]
}
```

There is **no `bedrock:Converse` IAM action** — Converse is authorized by
`bedrock:InvokeModel` and ConverseStream by
`bedrock:InvokeModelWithResponseStream`. A policy naming `bedrock:Converse`
grants nothing.

`sts:GetCallerIdentity` and the `sso:*` calls need no policy; they are
authorized by the service itself. That is why `haru doctor` can show a valid
assumed-role ARN while Bedrock is still denied.

## Model access is separate from IAM

Even with the policy above, Bedrock refuses models that are not enabled for
the account. In the AWS console: **Bedrock → Model access → Manage model
access**, enable the Anthropic models, and confirm the region matches
`auth.bedrock_region` (or the model entry's `region`, which wins).

## Debug logging

```bash
haru --debug chat
```

Raises haru's own logging to DEBUG and AWS SDK logging to INFO, showing the
API operations, endpoints, retries, and error codes behind a failure. Request
and response bodies and headers are never logged, and a redaction filter masks
credential-shaped values as defence in depth.

Set persistent logging in `logging.yaml`:

```yaml
logging:
  level: INFO      # DEBUG, INFO, WARNING, ERROR
  format: json     # json or text
  file: null       # a path here writes a 0600 log file as well as stderr
```

## Other failures

| Symptom | Cause and fix |
| --- | --- |
| `No configuration found` | Run `haru config init`, or pass `--config`, or set `$HARU_CONFIG`. |
| `No cached SSO token` | Run `haru login`. |
| `AWS rejected role 'X' in account 'Y'` | The role is not assigned to you, or a stale pin is in your config. Run `haru doctor --all-roles`. |
| `Guardrails are enabled but no guardrail_id` | Set `guardrails.guardrail_id`, or `enabled: false`. Guardrails fail closed by design. |
| Bedrock rejects sampling parameters | Claude 5-series and Opus 4.7+ reject non-default `temperature`/`top_p`/`top_k`. Leave them unset for those models. |
