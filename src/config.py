import os
from functools import lru_cache

import yaml

from .models import (
    AllocationPolicy,
    HoldPolicy,
    OperatingConstants,
    PendingConfirmationPolicy,
    ReschedulePolicy,
)

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")


def _load(filename: str) -> dict:
    with open(os.path.join(CONFIG_DIR, filename)) as f:
        return yaml.safe_load(f)


@lru_cache
def get_allocation_policy() -> AllocationPolicy:
    return AllocationPolicy(**_load("allocation_policy.yaml"))


@lru_cache
def get_hold_policy() -> HoldPolicy:
    return HoldPolicy(**_load("hold_policy.yaml"))


@lru_cache
def get_pending_confirmation_policy() -> PendingConfirmationPolicy:
    return PendingConfirmationPolicy(**_load("pending_confirmation_policy.yaml"))


@lru_cache
def get_reschedule_policy() -> ReschedulePolicy:
    return ReschedulePolicy(**_load("reschedule_policy.yaml"))


@lru_cache
def get_operating_constants() -> OperatingConstants:
    return OperatingConstants(**_load("operating_constants.yaml"))
