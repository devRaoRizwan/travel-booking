# Real servers on ephemeral ports. Airline timeout 0.5s vs slow mode 1.0s (production: 3s vs 6s).

import socket
import sqlite3
import threading
import time
import uuid
from dataclasses import replace

import httpx
import pytest
import uvicorn

from app.config import Settings
from app.domain.principal import ALL_SCOPES
from app.infrastructure.database import connect, init_db
from app.main import create_app
from app.services.auth import create_partner, create_partner_key
from stubs.airline import create_airline_app
from stubs.psp import create_psp_app

HOST = "127.0.0.1"
WEBHOOK_SECRET = "test-webhook-secret"
DEFAULT_FARE = "F-LHE-KHI-PK302"

PASSENGER = {
    "title": "MR", "given_name": "Ali", "surname": "Khan", "gender": "M", "date_of_birth": "1990-04-12",
    "nationality": "PK",
    "document": {"type": "passport", "number": "AB1234567", "issuing_country": "PK", "expires_on": "2030-01-01"},
}
CONTACT = {"email": "ali@example.com", "phone": "+923001234567"}
CONSENT = {"terms_version": "tos-2.1", "fare_rules_accepted": True}


def pytest_configure(config):
    config.addinivalue_line("markers", "settings(**kwargs): override app Settings for one test")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def wait_until(condition, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    last_value = None
    while time.time() < deadline:
        last_value = condition()
        if last_value:
            return last_value
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s (last={last_value!r})")


class ServerThread:
    def __init__(self, asgi_app, port: int | None = None):
        self.port = port or free_port()
        self.url = f"http://{HOST}:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(asgi_app, host=HOST, port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        wait_until(lambda: self.server.started, timeout=10, interval=0.01)
        return self

    def __exit__(self, *exc_info):
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture(scope="session")
def stub_servers():
    with ServerThread(create_psp_app(WEBHOOK_SECRET)) as psp_server, \
            ServerThread(create_airline_app()) as airline_server:
        yield psp_server, airline_server


class ApiHarness:
    def __init__(self, api_url: str, settings: Settings, psp_url: str, airline_url: str):
        self.url = api_url
        self.settings = settings
        self.http = httpx.Client(base_url=api_url, timeout=10)
        self.psp = httpx.Client(base_url=psp_url, timeout=10)
        self.airline = httpx.Client(base_url=airline_url, timeout=10)
        conn = connect(settings.db_path)
        create_partner(conn, "acme", "Acme Travel")
        create_partner(conn, "rival", "Rival Travel")
        self.partner_key = create_partner_key(conn, "acme", ALL_SCOPES)
        self.rival_partner_key = create_partner_key(conn, "rival", ALL_SCOPES)
        self.read_only_key = create_partner_key(conn, "acme", {"fares:read", "bookings:read"})
        conn.close()

    def open_db(self) -> sqlite3.Connection:
        return connect(self.settings.db_path)

    def auth_headers(self, token: str | None = None) -> dict:
        return {"Authorization": f"Bearer {token or self.partner_key}"}

    def post_hold(self, fare_id=DEFAULT_FARE, passenger_count=1, token=None, extra_headers=None) -> httpx.Response:
        body = {"fare_id": fare_id, "passengers": [PASSENGER] * passenger_count,
                "contact": CONTACT, "consent": CONSENT}
        return self.http.post("/v1/holds", json=body, headers={**self.auth_headers(token), **(extra_headers or {})})

    def create_hold(self, fare_id=DEFAULT_FARE, passenger_count=1) -> dict:
        response = self.post_hold(fare_id, passenger_count)
        assert response.status_code == 201, response.text
        return response.json()

    # idempotency_key=False omits the header; None generates one.
    def post_payment(self, ref, idempotency_key=None, method="jazzcash", token=None) -> httpx.Response:
        headers = self.auth_headers(token)
        if idempotency_key is not False:
            headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
        return self.http.post(f"/v1/bookings/{ref}/payments", json={"method": method}, headers=headers)

    def get_booking_response(self, ref, token=None) -> httpx.Response:
        return self.http.get(f"/v1/bookings/{ref}", headers=self.auth_headers(token))

    def get_booking(self, ref) -> dict:
        response = self.get_booking_response(ref)
        assert response.status_code == 200, response.text
        return response.json()

    def post_cancel(self, ref, token=None) -> httpx.Response:
        return self.http.post(f"/v1/bookings/{ref}/cancel", headers=self.auth_headers(token))

    def mint_customer_token(self, ref) -> dict:
        response = self.http.post(f"/v1/bookings/{ref}/customer-token", headers=self.auth_headers())
        assert response.status_code == 201, response.text
        return response.json()

    def wait_for_state(self, ref, expected_state, timeout=5.0) -> dict:
        def booking_in_state():
            booking = self.get_booking(ref)
            return booking if booking["state"] == expected_state else None
        return wait_until(booking_in_state, timeout)

    def wait_for_refund_succeeded(self, ref, timeout=5.0) -> dict:
        def booking_with_refund():
            booking = self.get_booking(ref)
            refund = booking["refund"]
            return booking if refund and refund["status"] == "succeeded" else None
        return wait_until(booking_with_refund, timeout)

    def seats_left(self, fare_id) -> int:
        conn = self.open_db()
        try:
            return conn.execute("SELECT seats_left FROM fares WHERE id = ?", (fare_id,)).fetchone()[0]
        finally:
            conn.close()

    def booking_state_in_db(self, ref) -> str:
        conn = self.open_db()
        try:
            return conn.execute("SELECT state FROM bookings WHERE ref = ?", (ref,)).fetchone()[0]
        finally:
            conn.close()

    def execute_sql(self, sql, params=()) -> None:
        conn = self.open_db()
        try:
            conn.execute(sql, params)
        finally:
            conn.close()

    def configure_psp(self, **config) -> None:
        self.psp.post("/_control", json=config).raise_for_status()

    def configure_airline(self, **config) -> None:
        self.airline.post("/_control", json=config).raise_for_status()

    def psp_state(self) -> dict:
        return self.psp.get("/_control/payments").json()

    def pnrs_for_booking(self, ref) -> list:
        issued_pnrs = self.airline.get("/_control/pnrs").json()["pnrs"]
        return [pnr for pnr in issued_pnrs if pnr["booking_ref"] == ref]

    def deliver_webhook(self, psp_payment_id, **options) -> dict:
        response = self.psp.post(f"/_control/payments/{psp_payment_id}/deliver", json=options)
        response.raise_for_status()
        return response.json()


@pytest.fixture
def api(stub_servers, tmp_path, request):
    psp_server, airline_server = stub_servers
    httpx.post(psp_server.url + "/_control/reset").raise_for_status()
    httpx.post(airline_server.url + "/_control/reset").raise_for_status()
    httpx.post(psp_server.url + "/_control", json={"delay_ms": [0, 300]}).raise_for_status()
    httpx.post(airline_server.url + "/_control", json={"slow_s": 1.0}).raise_for_status()

    api_port = free_port()
    settings = Settings(
        db_path=str(tmp_path / "test.db"),
        public_base_url=f"http://{HOST}:{api_port}",
        psp_url=psp_server.url, psp_webhook_secret=WEBHOOK_SECRET, psp_timeout_s=2.0,
        airline_url=airline_server.url, airline_timeout_s=0.5,
        ticketing_backoff_s=0.05, ticketing_max_attempts=4,
        refund_retry_s=0.2, worker_interval_s=0.05,
    )
    settings_marker = request.node.get_closest_marker("settings")
    if settings_marker:
        settings = replace(settings, **settings_marker.kwargs)
    init_db(settings.db_path)

    with ServerThread(create_app(settings), port=api_port) as api_server:
        yield ApiHarness(api_server.url, settings, psp_server.url, airline_server.url)
