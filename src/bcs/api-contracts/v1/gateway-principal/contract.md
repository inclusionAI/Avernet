# Gateway Principal Contract for BCS V1

`X-Avernet-Principal` carries one raw compact JWT. The verifier requires
`alg=HS256`, `typ=JWT`, `kid=bare`, `iss=gateway`, `aud=bcs`, integer `iat` and
`exp`, and a non-empty `principals` array. It allows one each of `user`, `bot`,
`app`, and `access_key`; all must agree on one non-blank tenant.

Known Principal types may add fields compatibly. Unknown Principal types,
duplicate types, removed required fields, mixed tenants, invalid time claims,
and invalid signatures fail the whole request. BCS never projects `bot.token`
or `access_key_token` into its internal caller.

This contract is preparatory: BCS V1 is not production-mounted by this change.
