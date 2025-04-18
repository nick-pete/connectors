import time
from datetime import datetime, timedelta
from typing import Union

import schedule
from feedly.opencti_connector.connector import FeedlyConnector
from pycti import OpenCTIConnectorHelper, get_config_variable
from pytz import utc

STATE_VERSION = 1


class FeedlyRunner:
    def __init__(self, helper: OpenCTIConnectorHelper):
        self.helper = helper
        self.interval_in_minutes: int = self.get_param("interval", True)
        self.connector = FeedlyConnector(self.get_param("api_key", False), self.helper)
        self.stream_ids = self.get_param("stream_ids", False).split(",")
        self.days_to_back_fill: int = self.get_param("days_to_back_fill", True)

    def run(self):
        self.helper.log_info("Starting Feedly connector")
        schedule.every(self.interval_in_minutes).minutes.do(self.run_once)
        self.run_once()
        while True:
            schedule.run_pending()
            time.sleep(1)

    def run_once(self):
        self.helper.log_info("Starting new run")
        for stream_id in self.stream_ids:
            self.run_stream(stream_id)

    def run_stream(self, stream_id: str):
        now = datetime.now(tz=utc)
        run_name = f"{stream_id} @ {now.strftime('%Y-%m-%d %H:%M:%S')}"
        try:
            self.helper.log_info(f"Fetching stream {stream_id}")
            state = self._get_state()
            stream_state = state["streams"].get(stream_id, {})

            self.helper.work_id = self.helper.api.work.initiate_work(
                self.helper.connect_id, f"Start {run_name}"
            )

            last_article_publish_date = self.connector.fetch_and_publish(
                stream_id,
                self._get_newer_than(stream_id, stream_state),
            )

            success_message = f"Finished {run_name}"
            self.helper.log_info(success_message)
            self.helper.api.work.to_processed(self.helper.work_id, success_message)

            self._update_state(state, stream_id, last_article_publish_date, now)
        except Exception as e:
            error_message = f"Failed {run_name} ({e})"
            self.helper.log_error(error_message)
            self.helper.api.work.to_processed(
                self.helper.work_id, error_message, in_error=True
            )

    def get_param(
        self, param_name: str, is_number: bool, default_value: str = None
    ) -> Union[int, str]:
        return get_config_variable(
            f"FEEDLY_{param_name.upper()}",
            ["feedly", param_name.lower()],
            self.helper.config,
            is_number,
            default_value,
        )

    def _get_state(self) -> dict:
        state = self.helper.get_state()
        if not state or "streams" not in state:
            state = {"streams": {}, "version": STATE_VERSION}
        return state

    def _get_newer_than(self, stream_id: str, stream_state: dict) -> datetime:
        if "/tag/" in stream_id:
            saved_date = stream_state.get("last_run")
        else:
            saved_date = stream_state.get("last_article_publish_date")
        if saved_date:
            return datetime.fromisoformat(saved_date)
        return datetime.now(tz=utc) - timedelta(days=self.days_to_back_fill)

    def _update_state(
        self, state: dict, stream_id: str, last_article_publish_date: str, now: datetime
    ) -> None:
        state["streams"][stream_id] = {
            "last_run": now.isoformat(),
            "last_article_publish_date": last_article_publish_date
            or state.get("last_article_publish_date"),
        }
        self.helper.set_state(state)
