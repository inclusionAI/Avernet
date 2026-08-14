# Gateway Principal Contract for BCS V1

`X-Avernet-Principal` carries one raw compact JWT. The verifier requires
`alg=HS256`, `typ=JWT`, `kid=bare`, `iss=gateway`, `aud=bcs`, integer `iat` and
`exp`, and a non-empty `principals` array. It allows one each of `user`, `bot`,
`app`, and `access_key`.

The outer `tenant` of a `user` Principal is optional and may be a non-blank
string, JSON `null`, or absent. BCS does not fabricate a tenant for a
tenantless User. The outer `tenant` of `bot`, `app`, and `access_key`
Principals remains a required non-blank string. Every outer tenant that is
present must agree; therefore a tenant-bearing Bot/App/AccessKey may establish
the normalized tenant when it accompanies a tenantless User. A User
`subject.tenant_id` is optional identity metadata: it must be non-blank and
equal the outer User tenant when both are present, but it does not establish
the caller tenant when the outer field is null or absent.

Known Principal types may add fields compatibly. Unknown Principal types,
duplicate types, removed required fields, mixed tenants, invalid time claims,
and invalid signatures fail the whole request. BCS never projects `bot.token`
or `access_key_token` into its internal caller.

Verification warnings correlate failures with the first 16 hexadecimal
characters of SHA-256 over the complete compact JWT. They may report an exact
schema path such as `principals[0].tenant`, but must not log any compact-JWT
segment, decoded payload, signature, signing key, credential, or claim value.

This contract is preparatory: BCS V1 is not production-mounted by this change.
