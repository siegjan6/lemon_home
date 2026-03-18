from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import typer

from .store import LemonStore, StoreError

app = typer.Typer(help="Agent-friendly apartment management CLI")
house_app = typer.Typer(help="Manage houses")
lease_app = typer.Typer(help="Manage leases")
tenant_app = typer.Typer(help="Manage tenants")
bill_app = typer.Typer(help="Manage bills")
payment_app = typer.Typer(help="Manage payments")
deposit_app = typer.Typer(help="Manage deposits")
history_app = typer.Typer(help="Show change history")

app.add_typer(house_app, name="house")
app.add_typer(lease_app, name="lease")
app.add_typer(tenant_app, name="tenant")
app.add_typer(bill_app, name="bill")
app.add_typer(payment_app, name="payment")
app.add_typer(deposit_app, name="deposit")
app.add_typer(history_app, name="history")


def get_store() -> LemonStore:
    root = Path(os.environ.get("LEMON_HOME_DATA_DIR", "data"))
    return LemonStore(root=root)


def print_output(payload: Any, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, str):
        typer.echo(payload)
        return
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def mask_text(value: str, keep_start: int, keep_end: int) -> str:
    if len(value) <= keep_start + keep_end:
        return "*" * len(value)
    middle = "*" * (len(value) - keep_start - keep_end)
    return f"{value[:keep_start]}{middle}{value[-keep_end:]}"


def mask_record(payload: Any, full: bool) -> Any:
    if full:
        return payload
    if isinstance(payload, list):
        return [mask_record(item, full=full) for item in payload]
    if isinstance(payload, dict):
        cloned = dict(payload)
        if "id_number" in cloned and cloned["id_number"]:
            cloned["id_number"] = mask_text(cloned["id_number"], 3, 2)
        if "phone" in cloned and cloned["phone"]:
            cloned["phone"] = mask_text(cloned["phone"], 3, 2)
        return cloned
    return payload


def normalize_date(value: str | None) -> str:
    return value or date.today().isoformat()


def fail(error: Exception) -> None:
    raise typer.Exit(code=1) from error


@app.callback()
def root() -> None:
    """Apartment management CLI."""


@house_app.command("add")
def house_add(
    address: str = typer.Option(...),
    area: float = typer.Option(...),
    layout: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    try:
        house = store.create_house(address, area, layout)
    except StoreError as error:
        typer.echo(str(error), err=True)
        fail(error)
    payload = house.to_dict()
    if json_output:
        print_output(payload, True)
    else:
        print_output(f"Created house {house.id} at {house.address}", False)


@house_app.command("list")
def house_list(
    json_output: bool = typer.Option(False, "--json"),
    full: bool = typer.Option(False, "--full"),
) -> None:
    store = get_store()
    houses = [house.to_dict() for house in store.list_houses()]
    print_output(mask_record(houses, full), json_output)


@house_app.command("show")
def house_show(
    house_id: str,
    json_output: bool = typer.Option(False, "--json"),
    full: bool = typer.Option(False, "--full"),
) -> None:
    store = get_store()
    house = store.get_house(house_id).to_dict()
    print_output(mask_record(house, full), json_output)


@house_app.command("update")
def house_update(
    house_id: str,
    address: str | None = typer.Option(None),
    area: float | None = typer.Option(None),
    layout: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    house = store.get_house(house_id)
    if address is not None:
        house.address = address
    if area is not None:
        house.area = area
    if layout is not None:
        house.layout = layout
    store.save_house(house)
    store.append_history(store.house_dir(house_id) / "history.jsonl", "house_updated", house.to_dict())
    print_output(house.to_dict(), json_output)


@house_app.command("archive")
def house_archive(
    house_id: str,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    house = store.get_house(house_id)
    house.archived = True
    house.status = "archived"
    store.save_house(house)
    store.append_history(store.house_dir(house_id) / "history.jsonl", "house_archived", {"house_id": house_id})
    print_output(house.to_dict(), json_output)


@house_app.command("media-add")
def house_media_add(
    house_id: str,
    file: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    media_type: str = typer.Option(..., help="photo or video"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    file_name = store.add_house_media(house_id, file, media_type)
    print_output({"house_id": house_id, "file": file_name, "type": media_type}, json_output)


@lease_app.command("create")
def lease_create(
    house_id: str = typer.Option(...),
    start_date: str = typer.Option(...),
    monthly_rent: float = typer.Option(...),
    deposit_amount: float = typer.Option(...),
    end_date: str | None = typer.Option(None),
    billing_start_date: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    lease = store.create_lease(house_id, start_date, monthly_rent, deposit_amount, end_date, billing_start_date=billing_start_date)
    print_output(lease.to_dict(), json_output)


@lease_app.command("show")
def lease_show(
    house_id: str,
    lease_id: str,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    payload = store.get_lease(house_id, lease_id).to_dict()
    print_output(payload, json_output)


@lease_app.command("checkout")
def lease_checkout(
    house_id: str,
    lease_id: str,
    end_date: str = typer.Option(...),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    payload = store.checkout_lease(house_id, lease_id, end_date).to_dict()
    print_output(payload, json_output)


@tenant_app.command("add")
def tenant_add(
    house_id: str = typer.Option(...),
    lease_id: str = typer.Option(...),
    name: str = typer.Option(...),
    id_number: str = typer.Option(...),
    phone: str = typer.Option(...),
    id_front: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    id_back: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    primary: bool = typer.Option(False, "--primary"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    tenant = store.add_tenant(house_id, lease_id, name, id_number, phone, primary, id_front, id_back)
    print_output(mask_record(tenant.to_dict(), full=False), json_output)


@tenant_app.command("list")
def tenant_list(
    house_id: str,
    lease_id: str,
    json_output: bool = typer.Option(False, "--json"),
    full: bool = typer.Option(False, "--full"),
) -> None:
    store = get_store()
    tenants = [tenant.to_dict() for tenant in store.list_tenants(house_id, lease_id)]
    print_output(mask_record(tenants, full), json_output)


@bill_app.command("generate")
def bill_generate(
    house_id: str = typer.Option(...),
    lease_id: str = typer.Option(...),
    month: str = typer.Option(..., help="YYYY-MM"),
    amount: float | None = typer.Option(None),
    due_date: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    bill = store.generate_bill(house_id, lease_id, month, due_date, amount=amount)
    print_output(bill.to_dict(), json_output)


@bill_app.command("list")
def bill_list(
    house_id: str,
    lease_id: str,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    bills = [bill.to_dict() for bill in store.list_bills(house_id, lease_id)]
    print_output(bills, json_output)


@payment_app.command("add")
def payment_add(
    house_id: str = typer.Option(...),
    lease_id: str = typer.Option(...),
    bill_id: str = typer.Option(...),
    amount: float = typer.Option(...),
    paid_at: str | None = typer.Option(None),
    method: str = typer.Option("unspecified"),
    note: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    payment = store.add_payment(
        house_id=house_id,
        lease_id=lease_id,
        bill_id=bill_id,
        amount=amount,
        paid_at=normalize_date(paid_at),
        method=method,
        note=note,
    )
    print_output(payment.to_dict(), json_output)


@payment_app.command("list")
def payment_list(
    house_id: str,
    lease_id: str,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    payload = [payment.to_dict() for payment in store.list_payments(house_id, lease_id)]
    print_output(payload, json_output)


@deposit_app.command("refund")
def deposit_refund(
    house_id: str = typer.Option(...),
    lease_id: str = typer.Option(...),
    amount_refunded: float = typer.Option(...),
    deduction_amount: float = typer.Option(0.0),
    deduction_reason: str | None = typer.Option(None),
    refunded_at: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    payload = store.refund_deposit(
        house_id=house_id,
        lease_id=lease_id,
        amount_refunded=amount_refunded,
        deduction_amount=deduction_amount,
        deduction_reason=deduction_reason,
        refunded_at=normalize_date(refunded_at),
    ).to_dict()
    print_output(payload, json_output)


@history_app.command("show")
def history_show(
    house_id: str,
    lease_id: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    store = get_store()
    payload = store.read_history(house_id, lease_id)
    print_output(payload, json_output)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
