"""The same state/history contract for local SQLite and bounded DynamoDB."""

import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4
from contextlib import contextmanager


class BusyError(RuntimeError):
    pass


class SQLiteStore:
    def __init__(self, path, history_days=365):
        self.path = str(path)
        self.history_days = history_days
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS history (id TEXT PRIMARY KEY, recorded REAL NOT NULL, value TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS history_time ON history(recorded)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO state VALUES (?,?)",
                (key, json.dumps(value, allow_nan=False)),
            )

    def append(self, value):
        with self.connect() as db:
            db.execute(
                "INSERT INTO history VALUES (?,?,?)",
                (uuid4().hex, time.time(), json.dumps(value, allow_nan=False)),
            )
            db.execute(
                "DELETE FROM history WHERE recorded < ?",
                (time.time() - self.history_days * 86400,),
            )

    def history(self, limit=96):
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT value FROM history ORDER BY recorded DESC LIMIT ?",
                    (min(limit, 672),),
                )
            ][::-1]

    def acquire(self, owner, ttl=240):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM state WHERE key='lease'").fetchone()
            lease = json.loads(row[0]) if row else {}
            if lease.get("until", 0) > time.time():
                raise BusyError("Another controller operation is running")
            db.execute(
                "INSERT OR REPLACE INTO state VALUES ('lease',?)",
                (json.dumps({"owner": owner, "until": time.time() + ttl}),),
            )

    def is_owner(self, owner, min_remaining=0):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key='lease'").fetchone()
            lease = json.loads(row[0]) if row else {}
            return (
                lease.get("owner") == owner
                and lease.get("until", 0) > time.time() + min_remaining
            )

    def release(self, owner):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM state WHERE key='lease'").fetchone()
            if row and json.loads(row[0]).get("owner") == owner:
                db.execute("DELETE FROM state WHERE key='lease'")


class DynamoStore:
    def __init__(self, table_name, history_days=365):
        import boto3

        self.table = boto3.resource("dynamodb").Table(table_name)
        self.history_days = history_days

    def get(self, key, default=None):
        row = self.table.get_item(
            Key={"pk": "household", "sk": "state:" + key}, ConsistentRead=True
        ).get("Item")
        return json.loads(row["value"]) if row else default

    def put(self, key, value):
        self.table.put_item(
            Item={
                "pk": "household",
                "sk": "state:" + key,
                "value": json.dumps(value, allow_nan=False),
            }
        )

    def append(self, value):
        self.table.put_item(
            Item={
                "pk": "household",
                "sk": f"event:{time.time_ns():020d}:{uuid4().hex}",
                "value": json.dumps(value, allow_nan=False),
                "expires_at": int(time.time() + self.history_days * 86400),
            }
        )

    def history(self, limit=96):
        from boto3.dynamodb.conditions import Key

        rows = self.table.query(
            KeyConditionExpression=Key("pk").eq("household")
            & Key("sk").begins_with("event:"),
            ScanIndexForward=False,
            Limit=min(limit, 672),
        ).get("Items", [])
        return [json.loads(r["value"]) for r in rows][::-1]

    def acquire(self, owner, ttl=240):
        from botocore.exceptions import ClientError

        try:
            self.table.put_item(
                Item={
                    "pk": "household",
                    "sk": "lease",
                    "owner": owner,
                    "until": int(time.time()) + ttl,
                },
                ConditionExpression="attribute_not_exists(pk) OR #until < :now",
                ExpressionAttributeNames={"#until": "until"},
                ExpressionAttributeValues={":now": int(time.time())},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise BusyError("Another controller operation is running") from None
            raise

    def is_owner(self, owner, min_remaining=0):
        lease = self.table.get_item(
            Key={"pk": "household", "sk": "lease"}, ConsistentRead=True
        ).get("Item", {})
        return (
            lease.get("owner") == owner
            and lease.get("until", 0) > time.time() + min_remaining
        )

    def release(self, owner):
        from botocore.exceptions import ClientError

        try:
            self.table.delete_item(
                Key={"pk": "household", "sk": "lease"},
                ConditionExpression="#owner = :owner",
                ExpressionAttributeNames={"#owner": "owner"},
                ExpressionAttributeValues={":owner": owner},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise


def make_store(settings):
    name = os.getenv("DYNAMODB_TABLE")
    if name:
        return DynamoStore(name, settings.history_days)
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        raise ValueError(
            "DYNAMODB_TABLE is required on Lambda; local files are not durable"
        )
    return SQLiteStore(settings.database_path, settings.history_days)
