from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@dataclass
class House:
    id: str
    address: str
    area: float
    layout: str | None = None
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    status: str = "vacant"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Lease:
    id: str
    house_id: str
    start_date: str
    end_date: str | None = None
    payment_cycle: int = 1
    note: str = ""
    status: str = "active"
    primary_tenant_id: str | None = None
    monthly_rent: float = 0.0
    deposit_amount: float = 0.0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Tenant:
    id: str
    name: str
    id_number: str
    phone: str
    is_primary: bool = False
    id_front_file: str | None = None
    id_back_file: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Bill:
    id: str
    month: str
    lease_id: str
    house_id: str
    amount_due: float
    amount_paid: float = 0.0
    status: str = "unpaid"
    due_date: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def apply_payment(self, amount: float) -> None:
        self.amount_paid = round(self.amount_paid + amount, 2)
        if self.amount_paid <= 0:
            self.status = "unpaid"
        elif self.amount_paid < self.amount_due:
            self.status = "partial"
        else:
            self.status = "paid"
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Payment:
    id: str
    bill_id: str
    lease_id: str
    house_id: str
    amount: float
    paid_at: str
    method: str = "unspecified"
    note: str | None = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class DepositRecord:
    lease_id: str
    house_id: str
    amount_received: float
    amount_refundable: float
    amount_refunded: float = 0.0
    deduction_amount: float = 0.0
    deduction_reason: str | None = None
    refunded_at: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def apply_refund(
        self,
        amount_refunded: float,
        deduction_amount: float,
        deduction_reason: str | None,
        refunded_at: str,
    ) -> None:
        self.amount_refunded = amount_refunded
        self.deduction_amount = deduction_amount
        self.deduction_reason = deduction_reason
        self.refunded_at = refunded_at
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def file_name(path: str | Path | None) -> str | None:
    if path is None:
        return None
    return Path(path).name
