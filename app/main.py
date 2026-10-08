import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, Response

from app.api.dependencies import Container
from app.api.errors import register_error_handlers
from app.api.routes import bookings, fares, holds, webhooks
from app.config import Settings
from app.infrastructure.airline_client import AirlineClient
from app.infrastructure.database import connect, init_db
from app.infrastructure.psp_client import PspClient
from app.infrastructure.seed import seed_fares
from app.workers.background import BackgroundWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    psp_client = PspClient(settings)
    airline_client = AirlineClient(settings)
    container = Container(settings, psp_client, airline_client,
                          BackgroundWorker(settings, psp_client, airline_client))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_db(settings.db_path)
        conn = connect(settings.db_path)
        seed_fares(conn)
        conn.close()
        if settings.run_worker:
            container.worker.start()
        yield
        if settings.run_worker:
            container.worker.stop()

    app = FastAPI(title="Travel.pk Booking API", version="1.0.0", lifespan=lifespan)
    app.state.container = container
    register_error_handlers(app)

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/docs")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get("/health", tags=["health"])
    def health():
        return {"ok": True}

    for route_module in (fares, holds, bookings, webhooks):
        app.include_router(route_module.router)
    return app
