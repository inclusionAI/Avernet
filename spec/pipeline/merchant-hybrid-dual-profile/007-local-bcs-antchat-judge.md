# Local BCS AntChat judge configuration

## Incident

The local BCS template previously placed an API-key value in `api_key_env`.
BCS interprets that field as the name of an environment variable, so session
service initialization could not resolve the credential and startup failed.

## Requirement

The local template keeps the AntChat OpenAI-compatible judge and Kimi-K2.6
model, but references the stable `ANTCHAT_API_KEY` environment variable. The
actual credential is supplied only through the ignored worktree `.env.local`
file and must not be committed or emitted in logs. The explicit
`BCS_E2E_MOCK_BASE_URL` path may still replace the judge endpoint for E2E tests.

## Acceptance

1. Generated local and merchant-hybrid BCS runtime configs select AntChat,
   Kimi-K2.6, and `api_key_env = "ANTCHAT_API_KEY"`.
2. Starting BCS with `ANTCHAT_API_KEY` loaded from `.env.local` reaches its
   health endpoint.
3. The mixed chat acceptance completes a Claude reply and deletes only its own
   generated acceptance group.
