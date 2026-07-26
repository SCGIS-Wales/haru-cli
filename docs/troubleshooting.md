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

### There is no Kiro IAM role to copy

If Kiro or Amazon Q works for you and haru does not, the natural next step is
to hunt for "the role Kiro uses" and grant yourself the same thing. **That
search cannot succeed, because no such role exists.** Kiro authorizes purely
through the OAuth 2.0 flow:

- Its stored credential (`~/.aws/sso/cache/kiro-auth-token.json`) holds
  `accessToken`, `refreshToken`, `expiresAt`, and — in the Identity Center
  variant — `clientId` and `clientSecret`. It contains no `accessKeyId`,
  `secretAccessKey`, or `sessionToken`. Those three fields appear if and only
  if a role was assumed; their absence is the proof.
- That access token is refreshed at `https://oidc.{region}.amazonaws.com/token`
  (Identity Center) or `https://prod.{region}.auth.desktop.kiro.dev/refreshToken`
  (Builder ID, not an AWS endpoint at all), and is then sent as a **bearer
  token** to AWS's managed Amazon Q / CodeWhisperer service. Nothing is
  SigV4-signed, so no AWS credentials are needed at any point.
- [Kiro's enterprise IAM documentation](https://kiro.dev/docs/cli/enterprise/iam/)
  lists `sso:*`, `identitystore:*`, `user-subscriptions:*`, `q:*`,
  `codewhisperer:*`, and `iam:CreateServiceLinkedRole`. There is no
  `bedrock:InvokeModel` anywhere, and those actions are for *administering*
  Amazon Q, not for a user to assume.

Kiro's authorization is the token's scopes plus your Amazon Q Developer
**subscription assignment** in Identity Center — an entitlement, not an IAM
permission. So finding no Bedrock-capable role in an Amazon Q account is the
expected state, not a misconfiguration.

haru is deliberately the opposite: browser SSO → `sso:GetRoleCredentials` →
real STS credentials → SigV4-signed `bedrock-runtime` calls **in your own
account**. That is what makes every request attributable and auditable under
your own IAM policies and CloudTrail, and it is exactly why haru needs a role
carrying `bedrock:InvokeModel`. The two models are not interchangeable, and
Kiro's is not available to third-party clients through any supported route.

The sign-in itself is identical — haru runs the same OAuth 2.0
authorization-code + PKCE flow against Identity Center that Kiro and `aws sso
login` use. **The divergence is authorization, not authentication.** Signing in
has always worked; the gap is the IAM grant to reach Bedrock afterward.

#### Can't the subscription authorize haru instead of an IAM role?

No — and it is worth being precise about why, because every documented path was
checked. The root cause is that **Amazon Q Developer is a subscription product
while Amazon Bedrock is a metered API**: Bedrock has no subscription or
entitlement concept, so every call is authorized against an IAM principal and
billed per token. There is no setting that makes it behave like a subscription.

- **Calling Amazon Q from haru** — not possible. AWS states CodeWhisperer/Q
  *"does not have public APIs … and they are not provided by any SDK"*; the
  `codewhisperer:*` actions exist only to be named in IAM policies. The
  subscription entitles you to AWS's own clients, nothing else.
- **Registering haru as an Identity Center customer managed OAuth 2.0
  application** — a real feature, but it propagates identity to a fixed set of
  *trusted applications*, and Amazon Q Developer is not among them. A
  self-registered client also cannot request Q's managed-application scopes.
- **[Trusted identity propagation](https://docs.aws.amazon.com/singlesignon/latest/userguide/trustedidentitypropagation-overview.html)**
  — its receiving services are Athena, EMR, Lake Formation, Redshift, S3 Access
  Grants, QuickSight, and Amazon Q *Business*. **Bedrock is not on the list**,
  and where TIP works it still yields an identity-enhanced *IAM role* session —
  the role is enriched, not removed.
- **[Bedrock API keys](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html)**
  (`AWS_BEARER_TOKEN_BEDROCK`) — a change of authentication transport, not
  authorization. Short-term keys *"inherit permissions from the IAM principal
  used to generate it"* and additionally require `bedrock:CallWithBearerToken`;
  generating one needs already-working Bedrock credentials, so it is circular.

Every route into Bedrock terminates at an IAM principal holding
`bedrock:InvokeModel`. The permission set below is not one option among several
— it is the only one, and it is a *smaller* ask than any alternative above.

> Third-party "gateway" projects that proxy Kiro credentials to that managed
> endpoint are out of bounds here: they drive a private, undocumented API
> through an unapproved client. In a regulated environment that is a
> compliance problem, not a workaround.

Run `haru doctor --all-roles --admin-request` to generate a complete, pasteable
Bedrock access request — the probe evidence, the exact policy, your configured
models, and the Kiro explanation — to send your AWS administrator.

Consequently an Amazon Q permission set (names like `AmazonQUsers`) is
**expected to fail** with haru. Run `haru doctor --all-roles` to find which of
your assigned roles can actually reach Bedrock:

```
ACCOUNT       NAME                ROLE                 CREDS   BEDROCK   INVOKE
881490127383  amazonq-prod        AmazonQUsers         ok      denied    denied
111122223333  ml-sandbox          BedrockDeveloper     ok      ok        ok
```

Then re-run `haru login` and choose the working combination, or pin it with
`auth.sso.account_id` and `auth.sso.role_name`.

Without `--invoke`, the `BEDROCK` column reflects `ListFoundationModels` only
— the control plane. A role granted `bedrock:InvokeModel` on one model ARN but
not `ListFoundationModels` (a common least-privilege setup) shows `denied`
here while working fine. Add `--invoke` for a definitive answer; it makes one
minimal billable Bedrock call per role.

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

Raises haru's own logging to DEBUG, which prints a line per AWS API call —
the operation name, its non-sensitive parameters, and any error code:

```
DEBUG haru.auth.session: aws sso.GetRoleCredentials account_id=881490127383 role_name=HaruBedrockInvoke sso_region=us-east-1 target_region=us-east-1
DEBUG haru.auth.session: aws sso.GetRoleCredentials failed code=ForbiddenException
```

The AWS SDK loggers stay capped at INFO even here. That cap is a security
control: botocore logs full request and response headers and bodies at DEBUG.
haru's own lines replace that visibility without it, carrying identifiers,
counts, regions, and error codes only — never a token, secret, credential,
authorization code, or prompt text. A redaction filter masks
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
