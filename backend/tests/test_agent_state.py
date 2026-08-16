"""
The agent's narration: what it recorded, and what a poller gets back.

Two properties matter more than the formatting. A poll must return exactly the
lines the caller has not seen — a log that re-sends its whole buffer every two
seconds is worse than no log — and writing a line must never be able to raise,
because these lines quote text scraped off a job posting and the caller is
halfway through an application it cannot repeat.
"""

import io
import pytest

from services import agent_state


@pytest.fixture(autouse=True)
def clean_log():
    """Each test starts from an empty buffer; nothing here should leak forward."""
    agent_state._log.clear()
    agent_state._seq = 0
    agent_state._state.update(state="idle", phase=None, started_at=None,
                              human_required=None, job="")
    yield
    agent_state._log.clear()
    agent_state._seq = 0


class TestTail:
    def test_returns_everything_when_asked_from_the_start(self):
        agent_state.log("one")
        agent_state.log("two")
        assert [l["msg"] for l in agent_state.tail()["lines"]] == ["one", "two"]

    def test_returns_only_what_the_caller_has_not_seen(self):
        agent_state.log("one")
        seen = agent_state.tail()["seq"]
        agent_state.log("two")
        fresh = agent_state.tail(seen)
        assert [l["msg"] for l in fresh["lines"]] == ["two"]

    def test_a_caller_that_is_up_to_date_gets_nothing(self):
        agent_state.log("one")
        seq = agent_state.tail()["seq"]
        assert agent_state.tail(seq)["lines"] == []

    def test_seq_is_returned_even_when_no_lines_are(self):
        # The poller needs the cursor back regardless, or it re-asks from 0.
        assert agent_state.tail(0)["seq"] == 0

    def test_sequence_numbers_are_unique_and_increasing(self):
        for i in range(5):
            agent_state.log(str(i))
        seqs = [l["seq"] for l in agent_state.tail()["lines"]]
        assert seqs == sorted(set(seqs))

    def test_carries_the_current_state_and_job(self):
        agent_state.start("applying")
        agent_state.set_job("AI Engineer @ Acme")
        t = agent_state.tail()
        assert (t["state"], t["phase"], t["job"]) == ("running", "applying", "AI Engineer @ Acme")


class TestBuffer:
    def test_is_bounded(self):
        for i in range(agent_state._LOG_MAX + 50):
            agent_state.log(str(i))
        assert len(agent_state.tail()["lines"]) == agent_state._LOG_MAX

    def test_drops_the_oldest_first(self):
        for i in range(agent_state._LOG_MAX + 3):
            agent_state.log(str(i))
        assert agent_state.tail()["lines"][0]["msg"] == "3"

    def test_a_caller_behind_the_window_still_gets_what_survives(self):
        # Rather than an error path: a gap in a log is not worth failing over.
        for i in range(agent_state._LOG_MAX + 10):
            agent_state.log(str(i))
        assert len(agent_state.tail(1)["lines"]) == agent_state._LOG_MAX


class TestRecording:
    def test_stores_the_line_without_its_terminal_indentation(self):
        agent_state.log("    [ok] uploaded tailored resume")
        assert agent_state.tail()["lines"][0]["msg"] == "[ok] uploaded tailored resume"

    def test_tags_the_line_with_the_job_it_came_from(self):
        agent_state.log("    tailoring", job="AI Engineer @ Acme")
        assert agent_state.tail()["lines"][0]["job"] == "AI Engineer @ Acme"

    def test_a_run_marks_its_own_start_and_end(self):
        agent_state.start("applying")
        agent_state.finish()
        msgs = [l["msg"] for l in agent_state.tail()["lines"]]
        assert "started" in msgs[0] and "finished" in msgs[-1]

    def test_finishing_clears_the_job(self):
        agent_state.start("applying")
        agent_state.set_job("AI Engineer @ Acme")
        agent_state.finish()
        assert agent_state.tail()["job"] == ""


class TestUnprintableText:
    """
    A console that cannot encode the text is not a reason to lose an
    application. These are the characters that actually turn up: a rupee sign
    in "Expected CTC", a company name in Devanagari, an emoji in a bullet.
    """

    @pytest.fixture
    def cp1252_stdout(self, monkeypatch):
        # Exactly what a Windows console does — including raising on encode.
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        monkeypatch.setattr("sys.stdout", stream)
        return stream

    @pytest.mark.parametrize("text", [
        "    [?] no answer for: Expected CTC (₹ lakhs)",
        "    [3/5] काम @ संस्था",
        "    [ok] shortlisted \U0001f389",
    ])
    def test_does_not_raise(self, cp1252_stdout, text):
        agent_state.log(text)   # must not raise

    def test_the_record_keeps_the_original_text(self, cp1252_stdout):
        agent_state.log("    [?] Expected CTC (₹ lakhs)")
        # Only the terminal copy is flattened; what the UI reads is intact.
        assert "₹" in agent_state.tail()["lines"][0]["msg"]
