#!/usr/bin/env python3
"""Poll a public HTTP API and store each result in PostgreSQL."""

import logging
import os
import time
from typing import Any

import psycopg
import requests
from psycopg.types.json import Jsonb

API_URL = os.getenv("API_URL", "https://jsonplaceholder.typicode.com/posts/1")
INTERVAL_SECONDS = int(os.getenv("INTERVAL_SECONDS", "300"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "api_monitor"),
    "user": os.getenv("DB_USER", "api_monitor_app"),
    "password": os.getenv("DB_PASSWORD", ""),
    "connect_timeout": 5,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("api-monitor")


def validate_config() -> None:
    if not DB_CONFIG["password"]:
        raise RuntimeError("DB_PASSWORD is not configured")
    if INTERVAL_SECONDS < 1:
        raise RuntimeError("INTERVAL_SECONDS must be greater than 0")
    if REQUEST_TIMEOUT_SECONDS < 1:
        raise RuntimeError("REQUEST_TIMEOUT_SECONDS must be greater than 0")


def init_db() -> None:
    """Create the history table if it does not exist yet."""
    with psycopg.connect(**DB_CONFIG) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_request_logs (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                api_url TEXT NOT NULL,
                http_status INTEGER,
                duration_ms INTEGER NOT NULL,
                response_body JSONB,
                error_message TEXT
            )
            """
        )
    logger.info("Database table api_request_logs is ready")


def save_result(
    http_status: int | None,
    duration_ms: int,
    response_body: Any,
    error_message: str | None,
) -> None:
    json_value = Jsonb(response_body) if response_body is not None else None
    with psycopg.connect(**DB_CONFIG) as conn:
        conn.execute(
            """
            INSERT INTO api_request_logs
                (api_url, http_status, duration_ms, response_body, error_message)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (API_URL, http_status, duration_ms, json_value, error_message),
        )


def check_api() -> None:
    started = time.monotonic()
    status: int | None = None
    body: Any = None
    error: str | None = None

    try:
        response = requests.get(
            API_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "api-monitor/1.0"},
        )
        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = {"text": response.text[:100_000]}

        if not response.ok:
            error = f"HTTP {response.status_code}"
    except requests.RequestException as exc:
        error = f"{type(exc).__name__}: {exc}"

    duration_ms = round((time.monotonic() - started) * 1000)

    try:
        save_result(status, duration_ms, body, error)
        if error:
            logger.warning(
                "API check saved: status=%s duration_ms=%s error=%s",
                status,
                duration_ms,
                error,
            )
        else:
            logger.info(
                "API check saved: status=%s duration_ms=%s",
                status,
                duration_ms,
            )
    except psycopg.Error:
        logger.exception("Could not save API check to PostgreSQL")


def main() -> None:
    validate_config()
    logger.info(
        "Starting API monitor: url=%s interval_seconds=%s",
        API_URL,
        INTERVAL_SECONDS,
    )
    init_db()

    while True:
        check_api()
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("API monitor stopped")
