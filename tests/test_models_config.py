import pytest
import yaml
from pydantic import ValidationError

from src import config
from src.models import (
    AllocationPolicy,
    Booked,
    Escalated,
    FeasibleSpan,
    HoldConfirmed,
    HoldPolicy,
    LostRace,
    OperatingConstants,
    OptionToken,
    Options,
    PendingConfirmationPolicy,
    Rejected,
    ReschedulePolicy,
    ToolResult,
)


def test_models_import_cleanly():
    assert ToolResult is not None
    for cls in (
        Booked,
        Rejected,
        LostRace,
        Escalated,
        Options,
        HoldConfirmed,
        FeasibleSpan,
        OptionToken,
        AllocationPolicy,
        HoldPolicy,
        PendingConfirmationPolicy,
        ReschedulePolicy,
        OperatingConstants,
    ):
        assert cls is not None


def test_all_policies_load_and_validate():
    assert config.get_allocation_policy().version == "1.0.0"
    assert config.get_hold_policy().mode == "adaptive"
    assert config.get_pending_confirmation_policy().queued_timeout_seconds == 120
    assert config.get_reschedule_policy().enabled is False
    assert config.get_operating_constants().eta_buffer_minutes == 15


def test_missing_required_field_raises_validation_error(tmp_path):
    bad_yaml = tmp_path / "bad_allocation_policy.yaml"
    data = yaml.safe_load(open(config.CONFIG_DIR + "/allocation_policy.yaml"))
    del data["waiting_linear"]  # required field
    bad_yaml.write_text(yaml.dump(data))

    with pytest.raises(ValidationError):
        AllocationPolicy(**yaml.safe_load(bad_yaml.read_text()))
