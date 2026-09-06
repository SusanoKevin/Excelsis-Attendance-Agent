import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.observability.audit import record_admin_action, recent_admin_actions


class TestRecordAdminAction:
    def test_recorded_action_is_readable_back(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUDIT_DB", str(tmp_path / "audit.db"))
        record_admin_action("admin", "user.create", target="new_user", client_ip="127.0.0.1")

        actions = recent_admin_actions()
        assert len(actions) == 1
        assert actions[0]["actor"] == "admin"
        assert actions[0]["action"] == "user.create"
        assert actions[0]["target"] == "new_user"
        assert actions[0]["client_ip"] == "127.0.0.1"

    def test_details_round_trip_as_json(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUDIT_DB", str(tmp_path / "audit.db"))
        record_admin_action("admin", "user.delete", target="old_user", details={"reason": "offboarding"})

        actions = recent_admin_actions()
        assert actions[0]["details"] == {"reason": "offboarding"}

    def test_most_recent_first(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUDIT_DB", str(tmp_path / "audit.db"))
        record_admin_action("admin", "user.create", target="first")
        record_admin_action("admin", "user.create", target="second")

        actions = recent_admin_actions()
        assert [a["target"] for a in actions] == ["second", "first"]

    def test_limit_is_respected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUDIT_DB", str(tmp_path / "audit.db"))
        for i in range(5):
            record_admin_action("admin", "user.create", target=f"user{i}")

        assert len(recent_admin_actions(limit=2)) == 2

    def test_record_failure_is_swallowed_not_raised(self, monkeypatch, tmp_path):
        """Auditing must never break the action it records."""
        monkeypatch.setenv("AUDIT_DB", str(tmp_path / "nonexistent_dir" / "audit.db"))
        record_admin_action("admin", "user.create", target="whoever")  # must not raise

    def test_read_failure_returns_empty_list_not_raised(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUDIT_DB", str(tmp_path / "nonexistent_dir" / "audit.db"))
        assert recent_admin_actions() == []
