"""Cryptographic GitHub OIDC verification for signed runner requests."""

from functools import lru_cache
from common.accounts import AUDIENCE,ISSUER,WorkerError


@lru_cache(maxsize=1)
def oidc_keys():
    import jwt
    return jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks", timeout=10)


def verify_oidc(authorization):
    import jwt
    try:
        scheme, token = authorization.split(" ", 1)
        if scheme != "Bearer" or len(token) > 32_000:
            raise ValueError()
        key = oidc_keys().get_signing_key_from_jwt(token)
        return jwt.decode(token, key.key, algorithms=["RS256"], issuer=ISSUER, audience=AUDIENCE,
                          options={"require": ["iss", "aud", "exp", "iat", "nbf", "sub"]})
    except Exception:
        raise WorkerError("Invalid GitHub workflow identity", 401) from None


@lru_cache(maxsize=4)
def operator_keys(tenant):
    import jwt
    return jwt.PyJWKClient(f'https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys',timeout=10)


def verify_operator(authorization,tenant,allowed):
    import jwt
    from uuid import UUID
    try:
        tenant=str(UUID(tenant))
        scheme,token=authorization.split(' ',1)
        if scheme!='Bearer' or len(token)>32_000:
            raise ValueError()
        key=operator_keys(tenant).get_signing_key_from_jwt(token)
        claims=jwt.decode(token,key.key,algorithms=['RS256'],
                          audience=['https://management.azure.com/','https://management.azure.com','https://management.core.windows.net/'],
                          issuer=[f'https://sts.windows.net/{tenant}/',f'https://login.microsoftonline.com/{tenant}/v2.0'],
                          options={'require':['exp','nbf','tid','oid']})
        if claims['tid']!=tenant or claims['oid'] not in allowed:
            raise ValueError()
        return claims['oid']
    except Exception:
        raise WorkerError('An authorized Azure operator login is required',401) from None
