# Expiry runs on the loop thread; airline/PSP calls run on a pool so a slow airline can't delay expiry.

import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from app.config import Settings
from app.infrastructure.airline_client import AirlineClient
from app.infrastructure.database import connect, write_tx
from app.infrastructure.psp_client import PspClient
from app.services import refunds, ticketing
from app.services.expiry import expire_due

IO_POOL_SIZE = 4

logger = logging.getLogger("tpk.worker")


class BackgroundWorker:
    def __init__(self, settings: Settings, psp: PspClient, airline: AirlineClient):
        self.settings = settings
        self.psp = psp
        self.airline = airline
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._io_pool = ThreadPoolExecutor(max_workers=IO_POOL_SIZE, thread_name_prefix="tpk-io")
        self._loop_thread = threading.Thread(target=self._run_loop, name="tpk-worker", daemon=True)

    def start(self) -> None:
        self._loop_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        self._loop_thread.join(timeout=5)
        self._io_pool.shutdown(wait=True, cancel_futures=True)

    def kick(self) -> None:
        self._wake_event.set()

    def _run_loop(self) -> None:
        conn = connect(self.settings.db_path)
        try:
            while not self._stop_event.is_set():
                try:
                    self._tick(conn)
                except Exception:
                    logger.exception("worker tick failed")
                self._wake_event.wait(self.settings.worker_interval_s)
                self._wake_event.clear()
        finally:
            conn.close()

    def _tick(self, conn: sqlite3.Connection) -> None:
        with write_tx(conn):
            if expired_count := expire_due(conn):
                logger.info("expired %d hold(s)", expired_count)
        for booking_id in ticketing.claim_due(conn, self.settings):
            self._io_pool.submit(self._issue_ticket, booking_id)
        for refund in refunds.claim_due(conn, self.settings):
            self._io_pool.submit(self._submit_refund, refund)

    def _issue_ticket(self, booking_id: int) -> None:
        conn = connect(self.settings.db_path)
        try:
            booking_ref, fare_id, passengers = ticketing.issue_request(conn, booking_id)
            result = self.airline.issue(booking_ref, fare_id, passengers)
            logger.info("issue %s -> %s %s", booking_ref, result.outcome, result.error or result.pnr)
            ticketing.record_result(conn, self.settings, booking_id, result)
        except Exception:
            logger.exception("ticketing attempt crashed for booking %s", booking_id)
        finally:
            conn.close()
        self.kick()

    def _submit_refund(self, refund: sqlite3.Row) -> None:
        conn = connect(self.settings.db_path)
        try:
            refunds.execute(conn, self.psp, refund)
        except Exception:
            logger.exception("refund %s crashed", refund["id"])
        finally:
            conn.close()
