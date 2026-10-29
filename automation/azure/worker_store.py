"""Azure Table compare-and-swap state and verified GitHub OIDC identities."""

from functools import lru_cache

from worker_core import AUDIENCE, ISSUER, WorkerError


class AzureTable:
    @classmethod
    def with_client(cls, client, partition):
        instance = cls.__new__(cls)
        instance.table, instance.partition = client, partition
        return instance

    def __init__(self, connection, name, partition):
        from azure.data.tables import TableServiceClient
        self.table = TableServiceClient.from_connection_string(connection).get_table_client(name)
        self.partition = partition

    def get(self, key):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            row = self.table.get_entity(self.partition, key)
        except ResourceNotFoundError:
            raise WorkerError("State record not found", 404) from None
        return dict(row), row.metadata["etag"]

    def list(self):
        return self.table.query_entities(f"PartitionKey eq '{self.partition}'")

    def create(self, row):
        from azure.core.exceptions import ResourceExistsError
        try:
            self.table.create_entity({**row, "PartitionKey": self.partition})
            return True
        except ResourceExistsError:
            return False

    def replace(self, row, etag):
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
        from azure.data.tables import UpdateMode
        try:
            self.table.update_entity({**row, "PartitionKey": self.partition}, mode=UpdateMode.REPLACE,
                                     etag=etag, match_condition=MatchConditions.IfNotModified)
        except (ResourceModifiedError, ResourceNotFoundError):
            raise WorkerError("State changed concurrently; retry the operation") from None

    def transaction(self, changes):
        """Atomically create/replace rows in this repository's partition."""
        from azure.core import MatchConditions
        from azure.data.tables import TableTransactionError, UpdateMode
        operations = []
        for row, etag in changes:
            entity = {**row, "PartitionKey": self.partition}
            operations.append(("create", entity) if etag is None else (
                "update", entity, {"mode": UpdateMode.REPLACE, "etag": etag,
                                   "match_condition": MatchConditions.IfNotModified}))
        try:
            self.table.submit_transaction(operations)
        except TableTransactionError as exc:
            if exc.status_code not in {404, 409, 412}:
                raise
            raise WorkerError("State changed concurrently; retry the operation") from None

    def delete(self, key, etag):
        from azure.core import MatchConditions
        self.table.delete_entity(self.partition, key, etag=etag,
                                 match_condition=MatchConditions.IfNotModified)


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
