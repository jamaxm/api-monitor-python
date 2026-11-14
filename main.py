#!/usr/bin/env python3
"""Poll FakerAPI resources and store request history plus typed records in PostgreSQL."""

import logging
import os
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
import requests
from psycopg.types.json import Jsonb

FAKERAPI_BASE_URL = os.getenv("FAKERAPI_BASE_URL", "https://fakerapi.it/api/v1").rstrip("/")
API_QUANTITY = int(os.getenv("API_QUANTITY", "5"))
API_LOCALE = os.getenv("API_LOCALE", "en_US")
API_SEED = os.getenv("API_SEED", "").strip()
INTERVAL_SECONDS = int(os.getenv("INTERVAL_SECONDS", "900"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
API_RESOURCES = tuple(
    item.strip()
    for item in os.getenv(
        "API_RESOURCES",
        "addresses,books,companies,persons,places,products,texts,users",
    ).split(",")
    if item.strip()
)

SUPPORTED_RESOURCES = {
    "addresses",
    "books",
    "companies",
    "persons",
    "places",
    "products",
    "texts",
    "users",
}

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "api_monitor"),
    "user": os.getenv("DB_USER", "api_monitor_app"),
    "password": os.getenv("DB_PASSWORD", ""),
    "connect_timeout": 5,
}

SENSITIVE_KEYS = {"password", "passwd", "secret", "token", "apikey", "api_key"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("api-monitor")

SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS api_sync_runs (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ,
        status TEXT NOT NULL DEFAULT 'running'
            CHECK (status IN ('running', 'success', 'partial_failure', 'failed')),
        resources_total INTEGER NOT NULL,
        resources_succeeded INTEGER NOT NULL DEFAULT 0,
        resources_failed INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_request_logs (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        sync_run_id BIGINT NOT NULL REFERENCES api_sync_runs(id) ON DELETE CASCADE,
        resource TEXT NOT NULL,
        api_url TEXT NOT NULL,
        http_status INTEGER,
        duration_ms INTEGER NOT NULL,
        response_body JSONB,
        error_message TEXT,
        checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (sync_run_id, resource)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_api_request_logs_resource_checked_at
    ON api_request_logs (resource, checked_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS faker_addresses (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        sync_run_id BIGINT NOT NULL REFERENCES api_sync_runs(id) ON DELETE CASCADE,
        source_id INTEGER NOT NULL,
        street TEXT,
        street_name TEXT,
        building_number TEXT,
        city TEXT,
        zipcode TEXT,
        country TEXT,
        country_code TEXT,
        latitude NUMERIC(10, 7),
        longitude NUMERIC(10, 7),
        UNIQUE (sync_run_id, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS faker_books (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        sync_run_id BIGINT NOT NULL REFERENCES api_sync_runs(id) ON DELETE CASCADE,
        source_id INTEGER NOT NULL,
        title TEXT,
        author TEXT,
        genre TEXT,
        description TEXT,
        isbn TEXT,
        image TEXT,
        published DATE,
        publisher TEXT,
        UNIQUE (sync_run_id, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS faker_companies (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        sync_run_id BIGINT NOT NULL REFERENCES api_sync_runs(id) ON DELETE CASCADE,
        source_id INTEGER NOT NULL,
        name TEXT,
        email TEXT,
        vat TEXT,
        phone TEXT,
        country TEXT,
        addresses JSONB NOT NULL DEFAULT '[]'::jsonb,
        website TEXT,
        image TEXT,
        contact JSONB,
        UNIQUE (sync_run_id, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS faker_persons (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        sync_run_id BIGINT NOT NULL REFERENCES api_sync_runs(id) ON DELETE CASCADE,
        source_id INTEGER NOT NULL,
        firstname TEXT,
        lastname TEXT,
        email TEXT,
        phone TEXT,
        birthday DATE,
        gender TEXT,
        address JSONB,
        website TEXT,
        image TEXT,
        UNIQUE (sync_run_id, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS faker_places (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        sync_run_id BIGINT NOT NULL REFERENCES api_sync_runs(id) ON DELETE CASCADE,
        source_id INTEGER NOT NULL,
        latitude NUMERIC(10, 7),
        longitude NUMERIC(10, 7),
        UNIQUE (sync_run_id, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS faker_products (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        sync_run_id BIGINT NOT NULL REFERENCES api_sync_runs(id) ON DELETE CASCADE,
        source_id INTEGER NOT NULL,
        name TEXT,
        description TEXT,
        ean TEXT,
        upc TEXT,
        image TEXT,
        images JSONB NOT NULL DEFAULT '[]'::jsonb,
        net_price NUMERIC(14, 2),
        taxes NUMERIC(8, 2),
        price NUMERIC(14, 2),
        categories JSONB NOT NULL DEFAULT '[]'::jsonb,
        tags JSONB NOT NULL DEFAULT '[]'::jsonb,
        UNIQUE (sync_run_id, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS faker_texts (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        sync_run_id BIGINT NOT NULL REFERENCES api_sync_runs(id) ON DELETE CASCADE,
        source_id INTEGER NOT NULL,
        title TEXT,
        author TEXT,
        genre TEXT,
        content TEXT,
        UNIQUE (sync_run_id, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS faker_users (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        sync_run_id BIGINT NOT NULL REFERENCES api_sync_runs(id) ON DELETE CASCADE,
        source_id INTEGER NOT NULL,
        uuid TEXT,
        firstname TEXT,
        lastname TEXT,
        username TEXT,
        email TEXT,
        ip TEXT,
        mac_address TEXT,
        website TEXT,
        image TEXT,
        UNIQUE (sync_run_id, source_id)
    )
    """,
)


def _text(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    return str(value)


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def redact_sensitive_fields(value: Any) -> Any:
    """Redact password/token-like values before persisting API response bodies."""
    if isinstance(value, dict):
        cleaned = {}
        for key, child in value.items():
            normalized_key = str(key).replace("-", "_").lower()
            compact_key = normalized_key.replace("_", "")
            if normalized_key in SENSITIVE_KEYS or compact_key in SENSITIVE_KEYS:
                continue
            cleaned[key] = redact_sensitive_fields(child)
        return cleaned
    if isinstance(value, list):
        return [redact_sensitive_fields(item) for item in value]
    return value


def validate_config() -> None:
    if not DB_CONFIG["password"]:
        raise RuntimeError("DB_PASSWORD is not configured")
    if INTERVAL_SECONDS < 1:
        raise RuntimeError("INTERVAL_SECONDS must be greater than 0")
    if REQUEST_TIMEOUT_SECONDS < 1:
        raise RuntimeError("REQUEST_TIMEOUT_SECONDS must be greater than 0")
    if not 1 <= API_QUANTITY <= 1000:
        raise RuntimeError("API_QUANTITY must be between 1 and 1000")
    if not API_RESOURCES:
        raise RuntimeError("API_RESOURCES must contain at least one resource")
    unknown = sorted(set(API_RESOURCES) - SUPPORTED_RESOURCES)
    if unknown:
        raise RuntimeError(f"Unsupported API_RESOURCES: {', '.join(unknown)}")
    if API_SEED:
        try:
            int(API_SEED)
        except ValueError as exc:
            raise RuntimeError("API_SEED must be an integer when set") from exc


def init_db() -> None:
    """Create run history, request log, and one typed table per selected resource."""
    with psycopg.connect(**DB_CONFIG) as conn:
        for statement in SCHEMA_SQL:
            conn.execute(statement)
    logger.info("Database schema is ready")


def create_sync_run(resources_total: int) -> int:
    with psycopg.connect(**DB_CONFIG) as conn:
        row = conn.execute(
            "INSERT INTO api_sync_runs (resources_total) VALUES (%s) RETURNING id",
            (resources_total,),
        ).fetchone()
    if row is None:
        raise RuntimeError("Could not create api_sync_runs entry")
    return int(row[0])


def _source_id(item: dict[str, Any], index: int) -> int:
    """FakerAPI ids are only unique within a generated response; no-id resources use row order."""
    source_id = item.get("id")
    try:
        return int(source_id) if source_id is not None else index
    except (ValueError, TypeError):
        return index


def _jsonb(value: Any, default: Any) -> Jsonb:
    return Jsonb(redact_sensitive_fields(default if value is None else value))


def persist_resource_record(
    conn: psycopg.Connection,
    sync_run_id: int,
    resource: str,
    index: int,
    item: dict[str, Any],
) -> None:
    """Insert one item into a typed table; IDs are unique only inside the sync run."""
    table: str
    fields: dict[str, Any]
    if resource == "addresses":
        table = "faker_addresses"
        fields = {
            "street": _text(item, "street"),
            "street_name": _text(item, "streetName"),
            "building_number": _text(item, "buildingNumber"),
            "city": _text(item, "city"),
            "zipcode": _text(item, "zipcode"),
            "country": _text(item, "country"),
            # The upstream API currently uses county_code in its address payload.
            "country_code": _text(item, "country_code") or _text(item, "county_code"),
            "latitude": _decimal(item.get("latitude")),
            "longitude": _decimal(item.get("longitude")),
        }
    elif resource == "books":
        table = "faker_books"
        fields = {
            "title": _text(item, "title"),
            "author": _text(item, "author"),
            "genre": _text(item, "genre"),
            "description": _text(item, "description"),
            "isbn": _text(item, "isbn"),
            "image": _text(item, "image"),
            "published": _date(item.get("published")),
            "publisher": _text(item, "publisher"),
        }
    elif resource == "companies":
        table = "faker_companies"
        fields = {
            "name": _text(item, "name"),
            "email": _text(item, "email"),
            "vat": _text(item, "vat"),
            "phone": _text(item, "phone"),
            "country": _text(item, "country"),
            "addresses": _jsonb(item.get("addresses"), []),
            "website": _text(item, "website"),
            "image": _text(item, "image"),
            "contact": _jsonb(item.get("contact"), {}) if item.get("contact") is not None else None,
        }
    elif resource == "persons":
        table = "faker_persons"
        fields = {
            "firstname": _text(item, "firstname"),
            "lastname": _text(item, "lastname"),
            "email": _text(item, "email"),
            "phone": _text(item, "phone"),
            "birthday": _date(item.get("birthday")),
            "gender": _text(item, "gender"),
            "address": _jsonb(item.get("address"), {}) if item.get("address") is not None else None,
            "website": _text(item, "website"),
            "image": _text(item, "image"),
        }
    elif resource == "places":
        table = "faker_places"
        fields = {
            "latitude": _decimal(item.get("latitude")),
            "longitude": _decimal(item.get("longitude")),
        }
    elif resource == "products":
        table = "faker_products"
        fields = {
            "name": _text(item, "name"),
            "description": _text(item, "description"),
            "ean": _text(item, "ean"),
            "upc": _text(item, "upc"),
            "image": _text(item, "image"),
            "images": _jsonb(item.get("images"), []),
            "net_price": _decimal(item.get("net_price")),
            "taxes": _decimal(item.get("taxes")),
            "price": _decimal(item.get("price")),
            "categories": _jsonb(item.get("categories"), []),
            "tags": _jsonb(item.get("tags"), []),
        }
    elif resource == "texts":
        table = "faker_texts"
        fields = {
            "title": _text(item, "title"),
            "author": _text(item, "author"),
            "genre": _text(item, "genre"),
            "content": _text(item, "content"),
        }
    elif resource == "users":
        table = "faker_users"
        # Do not persist the synthetic password field exposed by the upstream endpoint.
        fields = {
            "uuid": _text(item, "uuid"),
            "firstname": _text(item, "firstname"),
            "lastname": _text(item, "lastname"),
            "username": _text(item, "username"),
            "email": _text(item, "email"),
            "ip": _text(item, "ip"),
            "mac_address": _text(item, "macAddress"),
            "website": _text(item, "website"),
            "image": _text(item, "image"),
        }
    else:
        raise ValueError(f"Unsupported resource: {resource}")

    columns = ["sync_run_id", "source_id", *fields.keys()]
    values = [sync_run_id, _source_id(item, index), *fields.values()]
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(columns)
    conn.execute(
        f"""
        INSERT INTO {table} ({column_sql})
        VALUES ({placeholders})
        ON CONFLICT (sync_run_id, source_id) DO NOTHING
        """,
        values,
    )


def persist_resource_result(
    sync_run_id: int,
    resource: str,
    api_url: str,
    http_status: int | None,
    duration_ms: int,
    response_body: Any,
    error_message: str | None,
    items: list[dict[str, Any]],
) -> None:
    """Atomically store a sanitized response log and its typed resource rows."""
    safe_body = redact_sensitive_fields(response_body)
    with psycopg.connect(**DB_CONFIG) as conn:
        conn.execute(
            """
            INSERT INTO api_request_logs
                (sync_run_id, resource, api_url, http_status, duration_ms, response_body, error_message)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                sync_run_id,
                resource,
                api_url,
                http_status,
                duration_ms,
                Jsonb(safe_body) if safe_body is not None else None,
                error_message[:2000] if error_message else None,
            ),
        )
        if error_message is None:
            for index, item in enumerate(items, start=1):
                persist_resource_record(conn, sync_run_id, resource, index, item)


def fetch_resource(sync_run_id: int, resource: str) -> bool:
    api_url = f"{FAKERAPI_BASE_URL}/{resource}"
    params: dict[str, Any] = {
        "_quantity": API_QUANTITY,
        "_locale": API_LOCALE,
    }
    if API_SEED:
        params["_seed"] = int(API_SEED)

    started = time.monotonic()
    http_status: int | None = None
    response_body: Any = None
    items: list[dict[str, Any]] = []
    error_message: str | None = None

    try:
        response = requests.get(
            api_url,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "public-api-monitor/1.0"},
        )
        http_status = response.status_code
        try:
            response_body = response.json()
        except ValueError:
            response_body = {"text": response.text[:100_000]}

        if not response.ok:
            error_message = f"HTTP {response.status_code}"
        elif not isinstance(response_body, dict) or not isinstance(response_body.get("data"), list):
            error_message = "Unexpected API response format: expected an object with a data array"
        elif any(not isinstance(item, dict) for item in response_body["data"]):
            error_message = "Unexpected API response format: data entries must be objects"
        else:
            items = response_body["data"]
    except requests.RequestException as exc:
        error_message = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"

    duration_ms = round((time.monotonic() - started) * 1000)

    try:
        persist_resource_result(
            sync_run_id=sync_run_id,
            resource=resource,
            api_url=api_url,
            http_status=http_status,
            duration_ms=duration_ms,
            response_body=response_body,
            error_message=error_message,
            items=items,
        )
    except psycopg.Error:
        logger.exception("Could not persist response for resource=%s", resource)
        return False
    except Exception:
        logger.exception("Unexpected persistence error for resource=%s", resource)
        return False

    if error_message:
        logger.warning(
            "Resource sync failed: resource=%s status=%s duration_ms=%s error=%s",
            resource,
            http_status,
            duration_ms,
            error_message,
        )
        return False

    logger.info(
        "Resource synced: resource=%s status=%s rows=%s duration_ms=%s",
        resource,
        http_status,
        len(items),
        duration_ms,
    )
    return True


def finish_sync_run(sync_run_id: int, succeeded: int, failed: int, total: int) -> None:
    if failed == 0:
        status = "success"
    elif succeeded:
        status = "partial_failure"
    else:
        status = "failed"
    with psycopg.connect(**DB_CONFIG) as conn:
        conn.execute(
            """
            UPDATE api_sync_runs
            SET finished_at = now(),
                status = %s,
                resources_total = %s,
                resources_succeeded = %s,
                resources_failed = %s
            WHERE id = %s
            """,
            (status, total, succeeded, failed, sync_run_id),
        )
    logger.info(
        "Sync run finished: run_id=%s status=%s succeeded=%s failed=%s",
        sync_run_id,
        status,
        succeeded,
        failed,
    )


def sync_cycle() -> None:
    run_id = create_sync_run(len(API_RESOURCES))
    succeeded = 0
    failed = 0
    for resource in API_RESOURCES:
        if fetch_resource(run_id, resource):
            succeeded += 1
        else:
            failed += 1
    finish_sync_run(run_id, succeeded, failed, len(API_RESOURCES))


def main() -> None:
    validate_config()
    logger.info(
        "Starting FakerAPI monitor: base_url=%s resources=%s quantity=%s interval_seconds=%s",
        FAKERAPI_BASE_URL,
        ",".join(API_RESOURCES),
        API_QUANTITY,
        INTERVAL_SECONDS,
    )
    init_db()

    while True:
        started = time.monotonic()
        try:
            sync_cycle()
        except psycopg.Error:
            logger.exception("Database error during sync cycle")
        except Exception:
            logger.exception("Unexpected error during sync cycle")

        elapsed = time.monotonic() - started
        time.sleep(max(1, INTERVAL_SECONDS - int(elapsed)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("FakerAPI monitor stopped")
