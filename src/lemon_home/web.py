from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .models import infer_due_date
from .store import LemonStore, StoreError

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app = FastAPI(title="Lemon Home")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def get_store() -> LemonStore:
    root = Path(os.environ.get("LEMON_HOME_DATA_DIR", "data"))
    return LemonStore(root=root)


def mask_text(value: str, keep_start: int, keep_end: int) -> str:
    if len(value) <= keep_start + keep_end:
        return "*" * len(value)
    middle = "*" * (len(value) - keep_start - keep_end)
    return f"{value[:keep_start]}{middle}{value[-keep_end:]}"


def tenant_view(payload: dict[str, Any]) -> dict[str, Any]:
    masked = dict(payload)
    masked["id_number"] = mask_text(masked["id_number"], 3, 2)
    masked["phone"] = mask_text(masked["phone"], 3, 2)
    return masked


def request_context(request: Request, **extra: Any) -> dict[str, Any]:
    return {
        "request": request,
        "today": date.today().isoformat(),
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
        **extra,
    }


def redirect_with_message(url: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    params: dict[str, str] = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"{url}{suffix}", status_code=303)


def save_upload(upload: UploadFile) -> Path:
    temp_dir = Path(".tmp_web_uploads")
    temp_dir.mkdir(exist_ok=True)
    suffix = Path(upload.filename or "").suffix
    target = temp_dir / f"upload_{os.getpid()}_{date.today().isoformat()}_{id(upload)}{suffix}"
    with target.open("wb") as handle:
        while chunk := upload.file.read(1024 * 1024):
            handle.write(chunk)
    return target


def list_house_media(store: LemonStore, house_id: str) -> dict[str, list[str]]:
    house_dir = store.house_dir(house_id)
    photos = sorted(path.name for path in (house_dir / "media" / "photos").glob("*") if path.is_file() and not path.name.startswith("."))
    videos = sorted(path.name for path in (house_dir / "media" / "videos").glob("*") if path.is_file() and not path.name.startswith("."))
    return {"photos": photos, "videos": videos}


def list_leases(store: LemonStore, house_id: str) -> list[dict[str, Any]]:
    lease_root = store.house_dir(house_id) / "leases"
    leases: list[dict[str, Any]] = []
    for lease_dir in sorted(lease_root.glob("L*")):
        lease = store.get_lease(house_id, lease_dir.name).to_dict()
        tenants = [tenant_view(item.to_dict()) for item in store.list_tenants(house_id, lease_dir.name)]
        bills = [bill.to_dict() for bill in store.list_bills(house_id, lease_dir.name)]
        payments = [payment.to_dict() for payment in store.list_payments(house_id, lease_dir.name)]
        deposit = store.get_deposit(house_id, lease_dir.name).to_dict()
        history = store.read_history(house_id, lease_dir.name)
        lease["tenants"] = tenants
        lease["bills"] = bills
        lease["payments"] = payments
        lease["deposit"] = deposit
        lease["history"] = history
        leases.append(lease)
    return leases


def build_house_snapshot(store: LemonStore, house: dict[str, Any]) -> dict[str, Any]:
    leases = list_leases(store, house["id"])
    outstanding_total = 0.0
    due_count = 0
    latest_bill_status = None
    for lease in leases:
        for bill in lease["bills"]:
            outstanding = round(bill["amount_due"] - bill["amount_paid"], 2)
            if outstanding > 0:
                outstanding_total += outstanding
                due_count += 1
            latest_bill_status = bill["status"]
    snapshot = dict(house)
    snapshot["leases"] = leases
    snapshot["outstanding_total"] = round(outstanding_total, 2)
    snapshot["due_count"] = due_count
    snapshot["latest_bill_status"] = latest_bill_status
    return snapshot


def collect_bill_rows(store: LemonStore) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for house in store.list_houses():
        house_payload = house.to_dict()
        for lease in list_leases(store, house.id):
            primary_name = None
            for tenant in lease["tenants"]:
                if tenant["id"] == lease["primary_tenant_id"]:
                    primary_name = tenant["name"]
                    break
            for bill in lease["bills"]:
                row = {
                    "house_id": house_payload["id"],
                    "address": house_payload["address"],
                    "lease_id": lease["id"],
                    "bill_id": bill["id"],
                    "month": bill["month"],
                    "status": bill["status"],
                    "due_date": bill["due_date"],
                    "amount_due": bill["amount_due"],
                    "amount_paid": bill["amount_paid"],
                    "outstanding_amount": round(bill["amount_due"] - bill["amount_paid"], 2),
                    "primary_tenant_name": primary_name,
                }
                rows.append(row)
    rows.sort(key=lambda item: (item["month"], item["house_id"], item["lease_id"]), reverse=True)
    return rows


@app.get("/")
def dashboard(request: Request):
    store = get_store()
    status_filter = request.query_params.get("status", "all")
    debt_filter = request.query_params.get("debt", "all")
    house_snapshots = [build_house_snapshot(store, house.to_dict()) for house in store.list_houses()]
    houses = house_snapshots
    if status_filter != "all":
        houses = [house for house in houses if house["status"] == status_filter]
    if debt_filter == "due":
        houses = [house for house in houses if house["outstanding_total"] > 0]
    active_count = len([house for house in house_snapshots if house["status"] == "occupied"])
    due_count = sum(house["due_count"] for house in house_snapshots)
    outstanding_total = sum(house["outstanding_total"] for house in house_snapshots)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=request_context(
            request,
            houses=houses,
            stats={
                "house_count": len(houses),
                "active_count": active_count,
                "due_count": due_count,
                "outstanding_total": round(outstanding_total, 2),
            },
            filters={"status": status_filter, "debt": debt_filter},
        ),
    )


@app.get("/bills")
def bill_index(request: Request):
    store = get_store()
    status_filter = request.query_params.get("status", "all")
    month_filter = request.query_params.get("month", "")
    rows = collect_bill_rows(store)
    if status_filter != "all":
        rows = [row for row in rows if row["status"] == status_filter]
    if month_filter:
        rows = [row for row in rows if row["month"] == month_filter]
    stats = {
        "bill_count": len(rows),
        "outstanding_total": round(sum(row["outstanding_amount"] for row in rows), 2),
        "paid_count": len([row for row in rows if row["status"] == "paid"]),
    }
    return templates.TemplateResponse(
        request=request,
        name="bill_index.html",
        context=request_context(request, bills=rows, stats=stats, filters={"status": status_filter, "month": month_filter}),
    )


@app.get("/houses/new")
def house_new(request: Request):
    return templates.TemplateResponse(request=request, name="house_form.html", context=request_context(request))


@app.post("/houses")
def house_create(
    address: str = Form(...),
    area: float = Form(...),
    monthly_rent: float = Form(...),
    deposit: float = Form(...),
    layout: str | None = Form(None),
):
    store = get_store()
    house = store.create_house(address=address, area=area, monthly_rent=monthly_rent, deposit=deposit, layout=layout)
    return redirect_with_message(f"/houses/{house.id}", message=f"房屋 {house.id} 已创建")


@app.get("/houses/{house_id}")
def house_detail(request: Request, house_id: str):
    store = get_store()
    try:
        house = store.get_house(house_id).to_dict()
    except StoreError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    leases = list_leases(store, house_id)
    media = list_house_media(store, house_id)
    return templates.TemplateResponse(
        request=request,
        name="house_detail.html",
        context=request_context(request, house=house, leases=leases, media=media),
    )


@app.post("/houses/{house_id}/media")
def house_media_create(
    house_id: str,
    media_type: str = Form(...),
    file: UploadFile = File(...),
):
    store = get_store()
    temp_path = save_upload(file)
    try:
        store.add_house_media(house_id, temp_path, media_type)
    except StoreError as error:
        return redirect_with_message(f"/houses/{house_id}", error=str(error))
    finally:
        temp_path.unlink(missing_ok=True)
    return redirect_with_message(f"/houses/{house_id}", message="素材已上传")


@app.post("/houses/{house_id}/leases")
def lease_create(
    house_id: str,
    start_date: str = Form(...),
    billing_day: int = Form(1),
    custom_cycle_start_day: int | None = Form(None),
    end_date: str | None = Form(None),
):
    store = get_store()
    try:
        lease = store.create_lease(
            house_id=house_id,
            start_date=start_date,
            billing_day=billing_day,
            custom_cycle_start_day=custom_cycle_start_day,
            end_date=end_date or None,
        )
    except StoreError as error:
        return redirect_with_message(f"/houses/{house_id}", error=str(error))
    return redirect_with_message(f"/houses/{house_id}", message=f"租约 {lease.id} 已创建")


@app.post("/houses/{house_id}/leases/{lease_id}/checkout")
def lease_checkout(house_id: str, lease_id: str, end_date: str = Form(...)):
    store = get_store()
    try:
        store.checkout_lease(house_id, lease_id, end_date)
    except StoreError as error:
        return redirect_with_message(f"/houses/{house_id}", error=str(error))
    return redirect_with_message(f"/houses/{house_id}", message=f"租约 {lease_id} 已退租")


@app.post("/houses/{house_id}/leases/{lease_id}/tenants")
def tenant_create(
    house_id: str,
    lease_id: str,
    name: str = Form(...),
    id_number: str = Form(...),
    phone: str = Form(...),
    primary: bool = Form(False),
    id_front: UploadFile = File(...),
    id_back: UploadFile = File(...),
):
    store = get_store()
    front_path = save_upload(id_front)
    back_path = save_upload(id_back)
    try:
        store.add_tenant(house_id, lease_id, name, id_number, phone, primary, front_path, back_path)
    except StoreError as error:
        return redirect_with_message(f"/houses/{house_id}", error=str(error))
    finally:
        front_path.unlink(missing_ok=True)
        back_path.unlink(missing_ok=True)
    return redirect_with_message(f"/houses/{house_id}", message=f"租客 {name} 已添加")


@app.post("/houses/{house_id}/leases/{lease_id}/bills")
def bill_create(
    house_id: str,
    lease_id: str,
    month: str = Form(...),
    due_date: str | None = Form(None),
):
    store = get_store()
    try:
        lease = store.get_lease(house_id, lease_id)
        actual_due_date = due_date or infer_due_date(month, lease.billing_day)
        store.generate_bill(house_id, lease_id, month, actual_due_date)
    except StoreError as error:
        return redirect_with_message(f"/houses/{house_id}", error=str(error))
    return redirect_with_message(f"/houses/{house_id}", message=f"{month} 账单已生成")


@app.post("/houses/{house_id}/leases/{lease_id}/payments")
def payment_create(
    house_id: str,
    lease_id: str,
    bill_id: str = Form(...),
    amount: float = Form(...),
    paid_at: str = Form(...),
    method: str = Form("unspecified"),
    note: str | None = Form(None),
):
    store = get_store()
    try:
        store.add_payment(house_id, lease_id, bill_id, amount, paid_at, method, note)
    except StoreError as error:
        return redirect_with_message(f"/houses/{house_id}", error=str(error))
    return redirect_with_message(f"/houses/{house_id}", message=f"账单 {bill_id} 收款已记录")


@app.post("/houses/{house_id}/leases/{lease_id}/deposit-refund")
def deposit_refund(
    house_id: str,
    lease_id: str,
    amount_refunded: float = Form(...),
    deduction_amount: float = Form(0.0),
    deduction_reason: str | None = Form(None),
    refunded_at: str = Form(...),
):
    store = get_store()
    try:
        store.refund_deposit(house_id, lease_id, amount_refunded, deduction_amount, deduction_reason, refunded_at)
    except StoreError as error:
        return redirect_with_message(f"/houses/{house_id}", error=str(error))
    return redirect_with_message(f"/houses/{house_id}", message="押金退款已登记")


@app.get("/media/{house_id}/{kind}/{filename}")
def media_file(house_id: str, kind: str, filename: str):
    store = get_store()
    if kind not in {"photos", "videos"}:
        raise HTTPException(status_code=404, detail="Invalid media kind")
    path = store.house_dir(house_id) / "media" / kind / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(path)


def run() -> None:
    host = os.environ.get("LEMON_HOME_HOST", "0.0.0.0")
    port = int(os.environ.get("LEMON_HOME_PORT", "8000"))
    uvicorn.run("lemon_home.web:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
