from __future__ import annotations

import unittest

from app.streamlit_app import DISCLAIMER, render


class RecordingStreamlit:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

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
        raise RuntimeError("database offline")

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
                kind == "error" and "database offline" in text
                for kind, text in st.messages
            )
        )
        self.assertTrue(
            any("No request was made to NSE" in text for _, text in st.messages)
        )


if __name__ == "__main__":
    unittest.main()
