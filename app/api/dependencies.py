import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.domain.principal import Principal
from app.infrastructure.airline_client import AirlineClient
from app.infrastructure.database import connect
from app.infrastructure.psp_client import PspClient
from app.services.auth import authenticate
from app.services.idempotency import Outcome
from app.workers.background import BackgroundWorker


@dataclass
class Container:
    settings: Settings
    psp: PspClient
    airline: AirlineClient
    worker: BackgroundWorker


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_db(container: Container = Depends(get_container)) -> Iterator[sqlite3.Connection]:
    conn = connect(container.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


# auto_error=False: missing credentials produce the app's 401 envelope instead of FastAPI's default 403.
bearer_scheme = HTTPBearer(auto_error=False)


def get_principal(conn: sqlite3.Connection = Depends(get_db),
                  credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> Principal:
    return authenticate(conn, credentials.credentials if credentials else None)


def to_response(outcome: Outcome) -> JSONResponse:
    headers = {"Idempotent-Replayed": "true"} if outcome.replayed else None
    return JSONResponse(outcome.body, outcome.status, headers=headers)
