from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
import fcntl
from pathlib import Path
from typing import Any

from .models import Bill, DepositRecord, House, Lease, Payment, Tenant, utc_now


class StoreError(RuntimeError):
    pass


class LemonStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.houses_dir = self.root / "houses"
        self.houses_dir.mkdir(parents=True, exist_ok=True)

    def next_id(self, prefix: str, parent: Path) -> str:
        parent.mkdir(parents=True, exist_ok=True)
        counter_path = parent / f".{prefix.lower()}_counter"
        with self._locked_counter(counter_path) as handle:
            current = handle.read().strip()
            if current:
                next_value = int(current) + 1
            else:
                next_value = self._scan_max_numeric_suffix(prefix, parent) + 1
            handle.seek(0)
            handle.truncate()
            handle.write(str(next_value))
            handle.flush()
        return f"{prefix}{next_value:04d}"

    @contextmanager
    def _locked_counter(self, counter_path: Path):
        counter_path.parent.mkdir(parents=True, exist_ok=True)
        with counter_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            try:
                yield handle
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _scan_max_numeric_suffix(self, prefix: str, parent: Path) -> int:
        max_value = 0
        for path in parent.iterdir():
            if path.name.startswith("."):
                continue
            candidate = path.stem if path.is_file() else path.name
            if not candidate.startswith(prefix):
                continue
            suffix = candidate.replace(prefix, "", 1)
            if suffix.isdigit():
                max_value = max(max_value, int(suffix))
        return max_value

    def house_dir(self, house_id: str) -> Path:
        return self.houses_dir / house_id

    def lease_dir(self, house_id: str, lease_id: str) -> Path:
        return self.house_dir(house_id) / "leases" / lease_id

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise StoreError(f"Missing file: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def append_history(self, path: Path, action: str, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {"timestamp": utc_now(), "action": action, "payload": payload}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def create_house(
        self,
        address: str,
        area: float,
        monthly_rent: float,
        deposit: float,
        layout: str | None,
    ) -> House:
        house_id = self.next_id("H", self.houses_dir)
        house = House(
            id=house_id,
            address=address,
            area=area,
            monthly_rent=monthly_rent,
            deposit=deposit,
            layout=layout,
        )
        house_root = self.house_dir(house_id)
        (house_root / "media" / "photos").mkdir(parents=True, exist_ok=True)
        (house_root / "media" / "videos").mkdir(parents=True, exist_ok=True)
        (house_root / "leases").mkdir(parents=True, exist_ok=True)
        self.write_json(house_root / "house.json", house.to_dict())
        self.append_history(house_root / "history.jsonl", "house_created", house.to_dict())
        return house

    def list_houses(self) -> list[House]:
        houses: list[House] = []
        for house_dir in sorted(self.houses_dir.glob("H*")):
            payload = self.read_json(house_dir / "house.json")
            houses.append(House(**payload))
        return houses

    def get_house(self, house_id: str) -> House:
        return House(**self.read_json(self.house_dir(house_id) / "house.json"))

    def save_house(self, house: House) -> None:
        house.updated_at = utc_now()
        self.write_json(self.house_dir(house.id) / "house.json", house.to_dict())

    def create_lease(
        self,
        house_id: str,
        start_date: str,
        billing_day: int,
        custom_cycle_start_day: int | None,
        end_date: str | None,
    ) -> Lease:
        house = self.get_house(house_id)
        lease_parent = self.house_dir(house_id) / "leases"
        lease_id = self.next_id("L", lease_parent)
        lease = Lease(
            id=lease_id,
            house_id=house_id,
            start_date=start_date,
            end_date=end_date,
            billing_day=billing_day,
            custom_cycle_start_day=custom_cycle_start_day,
            monthly_rent=house.monthly_rent,
            deposit_amount=house.deposit,
        )
        lease_root = self.lease_dir(house_id, lease_id)
        (lease_root / "tenants").mkdir(parents=True, exist_ok=True)
        (lease_root / "bills").mkdir(parents=True, exist_ok=True)
        (lease_root / "payments").mkdir(parents=True, exist_ok=True)
        self.write_json(lease_root / "lease.json", lease.to_dict())
        deposit = DepositRecord(
            lease_id=lease_id,
            house_id=house_id,
            amount_received=house.deposit,
            amount_refundable=house.deposit,
        )
        self.write_json(lease_root / "deposit.json", deposit.to_dict())
        self.append_history(lease_root / "history.jsonl", "lease_created", lease.to_dict())
        house.status = "occupied"
        self.save_house(house)
        return lease

    def get_lease(self, house_id: str, lease_id: str) -> Lease:
        return Lease(**self.read_json(self.lease_dir(house_id, lease_id) / "lease.json"))

    def save_lease(self, lease: Lease) -> None:
        lease.updated_at = utc_now()
        self.write_json(self.lease_dir(lease.house_id, lease.id) / "lease.json", lease.to_dict())

    def copy_media(self, source: Path, target_dir: Path, preferred_name: str) -> str:
        if not source.exists():
            raise StoreError(f"Missing attachment: {source}")
        suffix = source.suffix.lower()
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{preferred_name}{suffix}"
        shutil.copy2(source, target_path)
        return target_path.name

    def add_house_media(self, house_id: str, source: Path, media_type: str) -> str:
        if media_type not in {"photo", "video"}:
            raise StoreError("media_type must be photo or video")
        house_root = self.house_dir(house_id)
        target_dir = house_root / "media" / ("photos" if media_type == "photo" else "videos")
        media_id = self.next_id("M", target_dir)
        file_name = self.copy_media(source, target_dir, media_id)
        self.append_history(house_root / "history.jsonl", "house_media_added", {"file": file_name, "type": media_type})
        return file_name

    def add_tenant(
        self,
        house_id: str,
        lease_id: str,
        name: str,
        id_number: str,
        phone: str,
        is_primary: bool,
        id_front: Path,
        id_back: Path,
    ) -> Tenant:
        lease = self.get_lease(house_id, lease_id)
        tenant_parent = self.lease_dir(house_id, lease_id) / "tenants"
        tenant_id = self.next_id("T", tenant_parent)
        tenant_dir = tenant_parent / tenant_id
        front_name = self.copy_media(id_front, tenant_dir, "id_front")
        back_name = self.copy_media(id_back, tenant_dir, "id_back")
        tenant = Tenant(
            id=tenant_id,
            name=name,
            id_number=id_number,
            phone=phone,
            is_primary=is_primary,
            id_front_file=front_name,
            id_back_file=back_name,
        )
        self.write_json(tenant_dir / "tenant.json", tenant.to_dict())
        if is_primary:
            lease.primary_tenant_id = tenant_id
            self.save_lease(lease)
        self.append_history(self.lease_dir(house_id, lease_id) / "history.jsonl", "tenant_added", tenant.to_dict())
        return tenant

    def list_tenants(self, house_id: str, lease_id: str) -> list[Tenant]:
        tenant_parent = self.lease_dir(house_id, lease_id) / "tenants"
        tenants: list[Tenant] = []
        for tenant_dir in sorted(tenant_parent.glob("T*")):
            tenants.append(Tenant(**self.read_json(tenant_dir / "tenant.json")))
        return tenants

    def get_bill_path(self, house_id: str, lease_id: str, bill_id: str) -> Path:
        return self.lease_dir(house_id, lease_id) / "bills" / f"{bill_id}.json"

    def generate_bill(self, house_id: str, lease_id: str, month: str, due_date: str | None) -> Bill:
        lease = self.get_lease(house_id, lease_id)
        bill = Bill(
            id=month,
            month=month,
            lease_id=lease_id,
            house_id=house_id,
            amount_due=lease.monthly_rent,
            due_date=due_date,
        )
        bill_path = self.get_bill_path(house_id, lease_id, bill.id)
        if bill_path.exists():
            raise StoreError(f"Bill already exists: {bill.id}")
        self.write_json(bill_path, bill.to_dict())
        self.append_history(self.lease_dir(house_id, lease_id) / "history.jsonl", "bill_generated", bill.to_dict())
        return bill

    def list_bills(self, house_id: str, lease_id: str) -> list[Bill]:
        bill_dir = self.lease_dir(house_id, lease_id) / "bills"
        bills: list[Bill] = []
        for path in sorted(bill_dir.glob("*.json")):
            bills.append(Bill(**self.read_json(path)))
        return bills

    def get_bill(self, house_id: str, lease_id: str, bill_id: str) -> Bill:
        return Bill(**self.read_json(self.get_bill_path(house_id, lease_id, bill_id)))

    def save_bill(self, bill: Bill) -> None:
        self.write_json(self.get_bill_path(bill.house_id, bill.lease_id, bill.id), bill.to_dict())

    def add_payment(
        self,
        house_id: str,
        lease_id: str,
        bill_id: str,
        amount: float,
        paid_at: str,
        method: str,
        note: str | None,
    ) -> Payment:
        bill = self.get_bill(house_id, lease_id, bill_id)
        payment_dir = self.lease_dir(house_id, lease_id) / "payments"
        payment_id = self.next_id("P", payment_dir)
        payment = Payment(
            id=payment_id,
            bill_id=bill_id,
            lease_id=lease_id,
            house_id=house_id,
            amount=amount,
            paid_at=paid_at,
            method=method,
            note=note,
        )
        bill.apply_payment(amount)
        self.save_bill(bill)
        self.write_json(payment_dir / f"{payment_id}.json", payment.to_dict())
        self.append_history(
            self.lease_dir(house_id, lease_id) / "history.jsonl",
            "payment_added",
            {"payment": payment.to_dict(), "bill_status": bill.status, "amount_paid": bill.amount_paid},
        )
        return payment

    def list_payments(self, house_id: str, lease_id: str) -> list[Payment]:
        payment_dir = self.lease_dir(house_id, lease_id) / "payments"
        payments: list[Payment] = []
        for path in sorted(payment_dir.glob("*.json")):
            payments.append(Payment(**self.read_json(path)))
        return payments

    def get_deposit(self, house_id: str, lease_id: str) -> DepositRecord:
        return DepositRecord(**self.read_json(self.lease_dir(house_id, lease_id) / "deposit.json"))

    def refund_deposit(
        self,
        house_id: str,
        lease_id: str,
        amount_refunded: float,
        deduction_amount: float,
        deduction_reason: str | None,
        refunded_at: str,
    ) -> DepositRecord:
        deposit = self.get_deposit(house_id, lease_id)
        deposit.apply_refund(amount_refunded, deduction_amount, deduction_reason, refunded_at)
        self.write_json(self.lease_dir(house_id, lease_id) / "deposit.json", deposit.to_dict())
        self.append_history(self.lease_dir(house_id, lease_id) / "history.jsonl", "deposit_refunded", deposit.to_dict())
        return deposit

    def checkout_lease(self, house_id: str, lease_id: str, end_date: str) -> Lease:
        lease = self.get_lease(house_id, lease_id)
        lease.status = "checked_out"
        lease.end_date = end_date
        self.save_lease(lease)
        house = self.get_house(house_id)
        house.status = "vacant"
        self.save_house(house)
        self.append_history(self.lease_dir(house_id, lease_id) / "history.jsonl", "lease_checked_out", {"end_date": end_date})
        return lease

    def read_history(self, house_id: str, lease_id: str | None = None) -> list[dict[str, Any]]:
        if lease_id is None:
            path = self.house_dir(house_id) / "history.jsonl"
        else:
            path = self.lease_dir(house_id, lease_id) / "history.jsonl"
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events
