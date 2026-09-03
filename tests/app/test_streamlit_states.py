from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.storage_adapter import StorageReaderAdapter
from app.streamlit_app import (
    DISCLAIMER,
    _open_reader_connection,
    configured_repository,
    main,
    render,
)


class RecordingStreamlit:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.secrets: dict[str, str] = {}

    def set_page_config(self, **_kwargs) -> None:
        pass

    def title(self, message: str) -> None:
        self.messages.append(("title", message))

    def caption(self, message: str) -> None:
        self.messages.append(("caption", message))

    def info(self, message: str) -> None:
        self.messages.append(("info", message))

    def error(self, message: str) -> None:
        self.messages.append(("error", message))


class EmptyRepository:
    def list_completed_snapshots(self):
        return []

    def get_completed_snapshot(self, snapshot_id: str):
        raise AssertionError("empty repository must not be queried")


class FailedRepository:
    def list_completed_snapshots(self):
        raise RuntimeError(
            "connection failed: postgresql://reader:super-secret@db.example/data"
        )

    def get_completed_snapshot(self, snapshot_id: str):
        raise AssertionError("failed repository must not be queried")


class StreamlitStateTest(unittest.TestCase):
    def test_empty_state_and_disclaimer_are_visible(self) -> None:
        st = RecordingStreamlit()
        render(EmptyRepository(), st_module=st)
        self.assertIn(("caption", DISCLAIMER), st.messages)
        self.assertTrue(
            any("No completed snapshots" in text for kind, text in st.messages)
        )

    def test_repository_failure_is_visible_and_does_not_fall_back_to_nse(self) -> None:
        st = RecordingStreamlit()
        render(FailedRepository(), st_module=st)
        self.assertTrue(
            any(
                kind == "error" and "database is unavailable" in text
                for kind, text in st.messages
            )
        )
        self.assertFalse(any("super-secret" in text for _, text in st.messages))
        self.assertTrue(
            any("No request was made to NSE" in text for _, text in st.messages)
        )

    def test_configured_state_builds_storage_reader_adapter(self) -> None:
        st = RecordingStreamlit()
        st.secrets["READER_DATABASE_URL"] = "postgresql://reader:secret@db/data"
        connection = Mock(closed=False)
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "app.streamlit_app._cached_reader_connection",
                return_value=connection,
            ) as cached,
        ):
            repository = configured_repository(st_module=st)

        self.assertIsInstance(repository, StorageReaderAdapter)
        cached.assert_called_once_with("postgresql://reader:secret@db/data")

    def test_reader_connection_enforces_read_only_autocommit_session(self) -> None:
        database_url = "postgresql://reader:secret@db/data"
        with patch("app.streamlit_app.psycopg.connect") as connect:
            _open_reader_connection(database_url)
        connect.assert_called_once_with(
            database_url,
            autocommit=True,
            options="-c default_transaction_read_only=on",
        )

    def test_missing_configuration_state_is_actionable(self) -> None:
        st = RecordingStreamlit()
        with patch.dict("os.environ", {}, clear=True):
            main(st_module=st)
        self.assertTrue(
            any(
                "not configured" in text
                for kind, text in st.messages
                if kind == "error"
            )
        )

    def test_database_connection_failure_is_sanitized(self) -> None:
        st = RecordingStreamlit()
        database_url = "postgresql://reader:super-secret@db.example/data"
        st.secrets["READER_DATABASE_URL"] = database_url
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "app.streamlit_app._cached_reader_connection",
                side_effect=RuntimeError(database_url),
            ),
        ):
            main(st_module=st)
        rendered = " ".join(text for _, text in st.messages)
        self.assertIn("database is unavailable", rendered)
        self.assertNotIn("super-secret", rendered)


if __name__ == "__main__":
    unittest.main()
