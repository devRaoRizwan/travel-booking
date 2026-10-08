import threading

import uvicorn

from app.config import Settings
from app.domain.principal import ALL_SCOPES
from app.infrastructure.database import connect, init_db
from app.main import create_app
from app.services.auth import create_partner, create_partner_key
from stubs.airline import create_airline_app
from stubs.psp import create_psp_app

HOST = "127.0.0.1"
API_PORT, PSP_PORT, AIRLINE_PORT = 8000, 8001, 8002


# New key on every start; only its hash is stored.
def _demo_partner_key(settings: Settings) -> str:
    init_db(settings.db_path)
    conn = connect(settings.db_path)
    try:
        create_partner(conn, "demo", "Demo Partner")
        return create_partner_key(conn, "demo", ALL_SCOPES)
    finally:
        conn.close()


def _run_in_background(asgi_app, port: int) -> None:
    server = uvicorn.Server(uvicorn.Config(asgi_app, host=HOST, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()


def run() -> None:
    settings = Settings.from_env()
    demo_key = _demo_partner_key(settings)
    _run_in_background(create_psp_app(settings.psp_webhook_secret), PSP_PORT)
    _run_in_background(create_airline_app(), AIRLINE_PORT)

    print(f"""
  Travel.pk booking API   http://{HOST}:{API_PORT}
  OpenAPI docs (Swagger)  http://{HOST}:{API_PORT}/docs
  OpenAPI docs (ReDoc)    http://{HOST}:{API_PORT}/redoc
  PSP stub                http://{HOST}:{PSP_PORT}
  Airline stub            http://{HOST}:{AIRLINE_PORT}

  Demo partner key (shown once): {demo_key}
""")
    uvicorn.run(create_app(settings), host=HOST, port=API_PORT, log_level="info")
