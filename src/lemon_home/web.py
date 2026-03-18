from __future__ import annotations

import calendar
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .store import LemonStore, StoreError

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app = FastAPI(title="Lemon Home")

STATUS_CN: dict[str, str] = {
    "vacant": "空置",
    "occupied": "在租",
    "archived": "已归档",
    "active": "生效中",
    "checked_out": "已结束",
}


def status_cn(value: str) -> str:
    return STATUS_CN.get(value, value)


templates.env.filters["status_cn"] = status_cn

CYCLE_CN = {1: "月付", 2: "双月付", 3: "季付", 6: "半年付", 12: "年付"}


def cycle_cn(value: int) -> str:
    return CYCLE_CN.get(value, f"{value}个月一付")


templates.env.filters["cycle_cn"] = cycle_cn

METHOD_CN = {"transfer": "转账", "cash": "现金", "wechat": "微信", "alipay": "支付宝", "other": "其他", "unspecified": "未指定"}


def method_cn(value: str) -> str:
    return METHOD_CN.get(value, value)


templates.env.filters["method_cn"] = method_cn


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


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
    return masked


def request_context(request: Request, **extra: Any) -> dict[str, Any]:
    path = request.url.path
    if path == "/":
        nav_active = "dashboard"
    elif path.startswith("/contracts"):
        nav_active = "contracts"
    elif path == "/houses" or path == "/houses/new":
        nav_active = "houses"
    else:
        nav_active = ""
    return {
        "request": request,
        "today": date.today().isoformat(),
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
        "nav_active": nav_active,
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
        history = store.read_history(house_id, lease_dir.name)
        lease["tenants"] = tenants
        lease["history"] = history
        leases.append(lease)
    return leases


def build_house_snapshot(store: LemonStore, house: dict[str, Any]) -> dict[str, Any]:
    leases = list_leases(store, house["id"])
    current_rent = None
    for lease in leases:
        if lease["status"] == "active":
            current_rent = lease["monthly_rent"]
    snapshot = dict(house)
    snapshot["leases"] = leases
    snapshot["current_rent"] = current_rent
    return snapshot


def build_lease_summary(store: LemonStore, house_id: str, lease_id: str) -> dict[str, Any]:
    lease = store.get_lease(house_id, lease_id).to_dict()
    tenants = [tenant_view(item.to_dict()) for item in store.list_tenants(house_id, lease_id)]
    primary_name = None
    for tenant in tenants:
        if tenant["id"] == lease["primary_tenant_id"]:
            primary_name = tenant["name"]
            break
    lease["primary_tenant_name"] = primary_name
    lease["tenant_count"] = len(tenants)
    return lease


def list_lease_summaries(store: LemonStore, house_id: str) -> list[dict[str, Any]]:
    lease_root = store.house_dir(house_id) / "leases"
    summaries: list[dict[str, Any]] = []
    for lease_dir in sorted(lease_root.glob("L*")):
        summaries.append(build_lease_summary(store, house_id, lease_dir.name))
    return summaries


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def calc_next_rent(lease_data: dict, bills: list[dict]) -> dict:
    start = date.fromisoformat(lease_data["start_date"])
    cycle = lease_data.get("payment_cycle") or 1
    monthly_rent = lease_data["monthly_rent"]
    rent_bills = [b for b in bills if b["id"].startswith("RENT-")]
    n = len(rent_bills)
    period_start = _add_months(start, cycle * n)
    period_end = _add_months(period_start, cycle) - timedelta(days=1)
    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "amount": round(monthly_rent * cycle, 2),
        "period_num": n + 1,
        "label": f"第{n + 1}期",
    }


def build_payment_timeline(deposit: dict, payments: list[dict]) -> list[dict]:
    timeline = []
    timeline.append({
        "date": deposit["created_at"][:10],
        "type": "deposit_in",
        "label": "押金收取",
        "amount": deposit["amount_received"],
        "method": None,
        "note": None,
    })
    if deposit.get("refunded_at"):
        note_parts = []
        if deposit.get("deduction_amount"):
            note_parts.append(f"扣除 ¥{deposit['deduction_amount']:.2f}")
            if deposit.get("deduction_reason"):
                note_parts[-1] += f"（{deposit['deduction_reason']}）"
        timeline.append({
            "date": deposit["refunded_at"],
            "type": "deposit_out",
            "label": "押金退还",
            "amount": deposit["amount_refunded"],
            "method": None,
            "note": "; ".join(note_parts) if note_parts else None,
        })
    for p in payments:
        timeline.append({
            "date": p["paid_at"],
            "type": "income",
            "label": "租金",
            "amount": p["amount"],
            "method": p.get("method"),
            "note": p.get("note"),
        })
    timeline.sort(key=lambda x: x["date"], reverse=True)
    return timeline


def build_rent_gantt(
    store: LemonStore,
    real_today: date,
    window_center: date,
    months_before: int = 2,
    months_after: int = 2,
) -> dict[str, Any]:
    """Build gantt chart data for all active leases.

    real_today: actual current date, used for status determination (overdue/upcoming).
    window_center: center of the visible time window (shifted by offset navigation).
    """
    window_start = _add_months(window_center.replace(day=1), -months_before)
    window_end = _add_months(window_center.replace(day=1), months_after + 1) - timedelta(days=1)
    total_days = (window_end - window_start).days + 1

    rows: list[dict[str, Any]] = []
    stats = {"overdue_count": 0, "overdue_amount": 0.0, "upcoming_count": 0, "upcoming_amount": 0.0, "paid_amount": 0.0}

    for house in store.list_houses():
        if house.status != "occupied":
            continue
        lease_root = store.house_dir(house.id) / "leases"
        active_leases = []
        for lease_dir in sorted(lease_root.glob("L*")):
            lease = store.get_lease(house.id, lease_dir.name)
            if lease.status == "active":
                active_leases.append(lease)

        for idx, lease in enumerate(active_leases):
            bills = [b.to_dict() for b in store.list_bills(house.id, lease.id)]
            rent_bills = {b["id"]: b for b in bills if b["id"].startswith("RENT-")}
            lease_start = date.fromisoformat(lease.start_date)
            lease_end = date.fromisoformat(lease.end_date) if lease.end_date else None
            cycle = lease.payment_cycle or 1
            amount_per_period = round(lease.monthly_rent * cycle, 2)

            periods: list[dict[str, Any]] = []
            n = 0
            while True:
                period_start = _add_months(lease_start, cycle * n)
                period_end = _add_months(period_start, cycle) - timedelta(days=1)
                if period_start > window_end:
                    break
                if lease_end and period_start > lease_end:
                    break
                n += 1

                # Skip periods entirely before the visible window
                if period_end < window_start:
                    continue

                bill_id = f"RENT-{n:03d}"
                bill = rent_bills.get(bill_id)

                # Status uses real_today, not window_center
                if bill and bill["status"] == "paid":
                    status = "paid"
                elif bill and bill["status"] == "partial":
                    status = "partial"
                elif period_start < real_today:
                    status = "overdue"
                elif (period_start - real_today).days <= 3:
                    status = "upcoming"
                else:
                    status = "future"

                # Clamp to window for display
                bar_start = max(period_start, window_start)
                bar_end = min(period_end, window_end)
                col_start = (bar_start - window_start).days + 1
                col_end = (bar_end - window_start).days + 2  # grid-column end is exclusive

                period_data = {
                    "num": n,
                    "start": period_start.isoformat(),
                    "end": period_end.isoformat(),
                    "amount": amount_per_period,
                    "status": status,
                    "col_start": col_start,
                    "col_end": col_end,
                    "house_id": house.id,
                    "lease_id": lease.id,
                }
                periods.append(period_data)

                # Stats: always use real_today for "本月已收"
                current_month_start = real_today.replace(day=1)
                current_month_end = _add_months(current_month_start, 1) - timedelta(days=1)
                if status == "overdue":
                    stats["overdue_count"] += 1
                    stats["overdue_amount"] += amount_per_period
                elif status in ("upcoming", "partial"):
                    stats["upcoming_count"] += 1
                    stats["upcoming_amount"] += amount_per_period
                if status == "paid" and current_month_start <= period_start <= current_month_end:
                    stats["paid_amount"] += amount_per_period

            if not periods:
                continue

            # Sort priority: overdue first
            worst = "future"
            priority_order = {"overdue": 0, "partial": 1, "upcoming": 2, "future": 3, "paid": 4}
            for p in periods:
                if priority_order.get(p["status"], 5) < priority_order.get(worst, 5):
                    worst = p["status"]

            label = house.address
            if len(active_leases) > 1:
                label = f"{house.address}（合同 {idx + 1}）"

            rows.append({
                "label": label,
                "house_id": house.id,
                "lease_id": lease.id,
                "periods": periods,
                "worst_status": worst,
                "sort_key": priority_order.get(worst, 5),
            })

    rows.sort(key=lambda r: r["sort_key"])

    # Week markers for the header
    weeks: list[dict[str, Any]] = []
    d = window_start
    # Align to Monday
    while d.weekday() != 0:
        d += timedelta(days=1)
    while d <= window_end:
        col = (d - window_start).days + 1
        weeks.append({"date": d.isoformat(), "label": f"{d.month}/{d.day}", "col": col})
        d += timedelta(days=7)

    # Month markers
    months: list[dict[str, Any]] = []
    m = window_start.replace(day=1)
    MONTH_NAMES = ["", "1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"]
    while m <= window_end:
        col = max(1, (m - window_start).days + 1)
        next_m = _add_months(m, 1)
        col_end = min(total_days + 1, (next_m - window_start).days + 1)
        months.append({"label": f"{m.year}年{MONTH_NAMES[m.month]}", "col_start": col, "col_end": col_end})
        m = next_m

    today_col = (real_today - window_start).days + 1 if window_start <= real_today <= window_end else None

    return {
        "rows": rows,
        "stats": stats,
        "weeks": weeks,
        "months": months,
        "total_days": total_days,
        "today_col": today_col,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
    }


@app.get("/")
def dashboard(request: Request):
    store = get_store()
    houses = [h.to_dict() for h in store.list_houses()]
    active_count = len([h for h in houses if h["status"] == "occupied"])
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=request_context(
            request,
            houses=houses,
            stats={
                "house_count": len(houses),
                "active_count": active_count,
            },
        ),
    )


@app.get("/contracts")
def contract_manage(request: Request):
    store = get_store()
    status_filter = request.query_params.get("status", "all")
    groups: list[dict[str, Any]] = []
    for house in store.list_houses():
        h = house.to_dict()
        leases = list_lease_summaries(store, house.id)
        current = None
        history: list[dict[str, Any]] = []
        for lease in leases:
            if lease["status"] == "active":
                current = lease
            else:
                history.append(lease)
        if status_filter == "occupied" and current is None:
            continue
        if status_filter == "vacant" and current is not None:
            continue
        groups.append({
            "house": h,
            "current": current,
            "history": history,
        })
    return templates.TemplateResponse(
        request=request,
        name="contract_manage.html",
        context=request_context(request, groups=groups, filters={"status": status_filter}),
    )


@app.get("/houses/{house_id}/contracts/new")
def contract_create_page(request: Request, house_id: str):
    store = get_store()
    try:
        house = store.get_house(house_id).to_dict()
    except StoreError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return templates.TemplateResponse(
        request=request,
        name="contract_create.html",
        context=request_context(request, house=house),
    )


@app.get("/houses")
def house_manage(request: Request):
    store = get_store()
    house_list = []
    for h in store.list_houses():
        d = h.to_dict()
        d["media"] = list_house_media(store, h.id)
        house_list.append(d)
    return templates.TemplateResponse(
        request=request,
        name="house_manage.html",
        context=request_context(request, houses=house_list),
    )


@app.get("/houses/new")
def house_new(request: Request):
    return templates.TemplateResponse(request=request, name="house_form.html", context=request_context(request))


@app.post("/houses")
def house_create(
    address: str = Form(...),
    area: float = Form(0),
    layout: str | None = Form(None),
    notes: str = Form(""),
):
    store = get_store()
    house = store.create_house(address=address, area=area, layout=layout)
    if notes.strip():
        house.notes = notes.strip()
        store.save_house(house)
    return redirect_with_message("/houses", message=f"房屋 {house.id} 已创建")



@app.get("/houses/{house_id}/edit")
def house_edit_page(request: Request, house_id: str):
    store = get_store()
    try:
        house = store.get_house(house_id).to_dict()
    except StoreError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    media = list_house_media(store, house_id)
    all_tags = store.all_tags()
    return templates.TemplateResponse(
        request=request,
        name="house_edit.html",
        context=request_context(request, house=house, media=media, all_tags=all_tags),
    )


@app.post("/houses/{house_id}/edit")
def house_edit(
    request: Request,
    house_id: str,
    address: str = Form(...),
    area: float = Form(0),
    notes: str | None = Form(None),
):
    store = get_store()
    house = store.get_house(house_id)
    house.address = address
    house.area = area
    if notes is not None:
        house.notes = notes.strip()
    store.save_house(house)
    if is_htmx(request):
        return templates.TemplateResponse(
            request=request,
            name="partials/house_hero.html",
            context=request_context(request, house=house.to_dict()),
        )
    return redirect_with_message(f"/houses/{house_id}/edit", message="房屋信息已更新")


@app.post("/houses/{house_id}/delete")
def house_delete(request: Request, house_id: str):
    store = get_store()
    try:
        store.delete_house(house_id)
    except StoreError as error:
        return redirect_with_message("/houses", error=str(error))
    return redirect_with_message("/houses", message=f"房屋 {house_id} 已删除")


@app.post("/houses/{house_id}/toggle-enabled")
def house_toggle_enabled(request: Request, house_id: str):
    store = get_store()
    house = store.get_house(house_id)
    house.enabled = not house.enabled
    store.save_house(house)
    label = "已启用" if house.enabled else "已禁用"
    return redirect_with_message("/houses", message=f"房屋 {house_id} {label}")


@app.post("/houses/{house_id}/notes")
def house_notes_update(request: Request, house_id: str, notes: str = Form("")):
    store = get_store()
    house = store.get_house(house_id)
    house.notes = notes.strip()
    store.save_house(house)
    if is_htmx(request):
        html = f'<p class="notes-text">{house.notes}</p>' if house.notes else ""
        return HTMLResponse(html)
    return redirect_with_message(f"/houses/{house_id}", message="备注已保存")


@app.get("/houses/{house_id}/leases/{lease_id}")
def lease_detail_view(request: Request, house_id: str, lease_id: str):
    store = get_store()
    try:
        house = store.get_house(house_id).to_dict()
        lease = store.get_lease(house_id, lease_id).to_dict()
    except StoreError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    tenants = [tenant_view(item.to_dict()) for item in store.list_tenants(house_id, lease_id)]
    lease["tenants"] = tenants
    deposit = store.get_deposit(house_id, lease_id).to_dict()
    bills = [b.to_dict() for b in store.list_bills(house_id, lease_id)]
    payments = [p.to_dict() for p in store.list_payments(house_id, lease_id)]
    next_rent = calc_next_rent(lease, bills)
    timeline = build_payment_timeline(deposit, payments)
    unpaid_bills = [b for b in bills if b["status"] != "paid"]
    unpaid_total = sum(b["amount_due"] - b["amount_paid"] for b in unpaid_bills)
    checkout_summary = {
        "unpaid_count": len(unpaid_bills),
        "unpaid_total": unpaid_total,
        "deposit_held": deposit["amount_received"] if not deposit.get("refunded_at") else 0,
        "deposit_refunded": deposit.get("refunded_at") is not None,
    }
    return templates.TemplateResponse(
        request=request,
        name="lease_detail.html",
        context=request_context(
            request, house=house, lease=lease, deposit=deposit,
            bills=bills, payments=payments, next_rent=next_rent,
            timeline=timeline, checkout_summary=checkout_summary,
        ),
    )


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}


def guess_media_type(filename: str | None, content_type: str | None) -> str:
    if filename:
        ext = Path(filename).suffix.lower()
        if ext in IMAGE_EXTS:
            return "photo"
        if ext in VIDEO_EXTS:
            return "video"
    if content_type:
        if content_type.startswith("image/"):
            return "photo"
        if content_type.startswith("video/"):
            return "video"
    return "photo"


@app.post("/houses/{house_id}/media")
def house_media_create(
    request: Request,
    house_id: str,
    file: UploadFile = File(...),
):
    store = get_store()
    media_type = guess_media_type(file.filename, file.content_type)
    temp_path = save_upload(file)
    error_msg = None
    try:
        store.add_house_media(house_id, temp_path, media_type)
    except StoreError as error:
        error_msg = str(error)
    finally:
        temp_path.unlink(missing_ok=True)
    if is_htmx(request):
        media = list_house_media(store, house_id)
        return templates.TemplateResponse(
            request=request,
            name="partials/media_list.html",
            context={"request": request, "house": {"id": house_id}, "media": media, "error": error_msg, "message": "素材已上传" if not error_msg else None},
        )
    if error_msg:
        return redirect_with_message(f"/houses/{house_id}", error=error_msg)
    return redirect_with_message(f"/houses/{house_id}", message="素材已上传")


@app.post("/houses/{house_id}/leases")
async def lease_create(request: Request, house_id: str):
    store = get_store()
    form = await request.form()
    start_date = form["start_date"]
    monthly_rent = float(form["monthly_rent"])
    deposit_amount = float(form["deposit_amount"])
    payment_cycle = int(form.get("payment_cycle", 1))
    note = str(form.get("note", "")).strip()
    end_date = form.get("end_date") or None

    try:
        lease = store.create_lease(
            house_id=house_id,
            start_date=start_date,
            monthly_rent=monthly_rent,
            deposit_amount=deposit_amount,
            end_date=end_date,
            payment_cycle=payment_cycle,
            note=note,
        )
    except StoreError as error:
        if is_htmx(request):
            return HTMLResponse(f'<div class="notice error">{error}</div>')
        return redirect_with_message(f"/houses/{house_id}", error=str(error))

    # Primary tenant
    id_front = form.get("id_front")
    id_back = form.get("id_back")
    front_path = save_upload(id_front) if id_front and getattr(id_front, "filename", None) else None
    back_path = save_upload(id_back) if id_back and getattr(id_back, "filename", None) else None
    try:
        store.add_tenant(house_id, lease.id, form["tenant_name"], str(form.get("tenant_id_number", "")), form["tenant_phone"], True, front_path, back_path)
    finally:
        if front_path:
            front_path.unlink(missing_ok=True)
        if back_path:
            back_path.unlink(missing_ok=True)

    # Extra tenants
    i = 0
    while f"extra_name_{i}" in form:
        efront = form.get(f"extra_id_front_{i}")
        eback = form.get(f"extra_id_back_{i}")
        ef = save_upload(efront) if efront and getattr(efront, "filename", None) else None
        eb = save_upload(eback) if eback and getattr(eback, "filename", None) else None
        try:
            store.add_tenant(house_id, lease.id, form[f"extra_name_{i}"], str(form.get(f"extra_id_number_{i}", "")), form[f"extra_phone_{i}"], False, ef, eb)
        finally:
            if ef:
                ef.unlink(missing_ok=True)
            if eb:
                eb.unlink(missing_ok=True)
        i += 1

    target = f"/houses/{house_id}/leases/{lease.id}"
    if is_htmx(request):
        return HTMLResponse(status_code=200, headers={"HX-Redirect": f"{target}?message=合同已创建"})
    return redirect_with_message(target, message="合同已创建")


@app.post("/houses/{house_id}/leases/{lease_id}/checkout")
def lease_checkout(request: Request, house_id: str, lease_id: str, end_date: str = Form(...)):
    store = get_store()
    try:
        store.checkout_lease(house_id, lease_id, end_date)
    except StoreError as error:
        if is_htmx(request):
            return HTMLResponse(f'<div class="notice error">{error}</div>')
        return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}", error=str(error))
    return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}", message="已办理退租")


@app.post("/houses/{house_id}/leases/{lease_id}/rent")
def lease_rent_collect(
    request: Request,
    house_id: str,
    lease_id: str,
    period_num: int = Form(...),
    paid_date: str = Form(...),
    amount: float = Form(...),
    method: str = Form("transfer"),
    note: str = Form(""),
):
    store = get_store()
    bill_id = f"RENT-{period_num:03d}"
    try:
        store.get_bill(house_id, lease_id, bill_id)
        return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}", error=f"第{period_num}期租金已存在")
    except StoreError:
        pass
    store.generate_bill(house_id, lease_id, bill_id, due_date=paid_date, amount=amount)
    store.add_payment(house_id, lease_id, bill_id, amount, paid_date, method, note or None)
    return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}", message=f"第{period_num}期租金已入账 ¥{amount:.2f}")


@app.post("/houses/{house_id}/leases/{lease_id}/income")
def lease_income(
    request: Request,
    house_id: str,
    lease_id: str,
    paid_date: str = Form(...),
    amount: float = Form(...),
    method: str = Form("transfer"),
    note: str = Form(""),
):
    store = get_store()
    bill_id = f"CUSTOM-{paid_date}"
    try:
        bill = store.get_bill(house_id, lease_id, bill_id)
    except StoreError:
        bill = store.generate_bill(house_id, lease_id, bill_id, due_date=paid_date, amount=amount)
    store.add_payment(house_id, lease_id, bill.id, amount, paid_date, method, note or None)
    return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}", message=f"已入账 ¥{amount:.2f}")


@app.post("/houses/{house_id}/leases/{lease_id}/deposit/refund")
def deposit_refund(
    request: Request,
    house_id: str,
    lease_id: str,
    amount_refunded: float = Form(...),
    deduction_amount: float = Form(0),
    deduction_reason: str = Form(""),
    refunded_at: str = Form(...),
):
    store = get_store()
    try:
        store.refund_deposit(house_id, lease_id, amount_refunded, deduction_amount, deduction_reason or None, refunded_at)
    except StoreError as error:
        return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}", error=str(error))
    return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}", message=f"押金已退还 ¥{amount_refunded:.2f}")


@app.get("/houses/{house_id}/leases/{lease_id}/tenants")
def tenant_manage_view(request: Request, house_id: str, lease_id: str):
    store = get_store()
    try:
        house = store.get_house(house_id).to_dict()
        lease = store.get_lease(house_id, lease_id).to_dict()
    except StoreError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    tenants = [tenant_view(item.to_dict()) for item in store.list_tenants(house_id, lease_id)]
    lease["tenants"] = tenants
    return templates.TemplateResponse(
        request=request,
        name="tenant_manage.html",
        context=request_context(request, house=house, lease=lease),
    )


@app.post("/houses/{house_id}/leases/{lease_id}/tenants")
def tenant_create(
    request: Request,
    house_id: str,
    lease_id: str,
    name: str = Form(...),
    id_number: str = Form(""),
    phone: str = Form(...),
    primary: bool = Form(False),
    id_front: UploadFile | None = File(None),
    id_back: UploadFile | None = File(None),
):
    store = get_store()
    front_path = save_upload(id_front) if id_front and id_front.filename else None
    back_path = save_upload(id_back) if id_back and id_back.filename else None
    error_msg = None
    try:
        store.add_tenant(house_id, lease_id, name, id_number, phone, primary, front_path, back_path)
    except StoreError as error:
        error_msg = str(error)
    finally:
        if front_path:
            front_path.unlink(missing_ok=True)
        if back_path:
            back_path.unlink(missing_ok=True)
    if is_htmx(request):
        lease = store.get_lease(house_id, lease_id).to_dict()
        tenants = [tenant_view(item.to_dict()) for item in store.list_tenants(house_id, lease_id)]
        return templates.TemplateResponse(
            request=request,
            name="partials/tenant_list.html",
            context={"request": request, "house": {"id": house_id}, "lease": {**lease, "tenants": tenants}, "error": error_msg, "message": f"租客 {name} 已添加" if not error_msg else None},
        )
    if error_msg:
        return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}/tenants", error=error_msg)
    return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}/tenants", message=f"租客 {name} 已添加")


@app.get("/houses/{house_id}/leases/{lease_id}/tenants/{tenant_id}/id/{side}")
def tenant_id_photo(house_id: str, lease_id: str, tenant_id: str, side: str):
    if side not in {"front", "back"}:
        raise HTTPException(status_code=404)
    store = get_store()
    tenant = store.get_tenant(house_id, lease_id, tenant_id)
    filename = tenant.id_front_file if side == "front" else tenant.id_back_file
    if not filename:
        raise HTTPException(status_code=404, detail="未上传")
    path = store.lease_dir(house_id, lease_id) / "tenants" / tenant_id / filename
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.post("/houses/{house_id}/leases/{lease_id}/tenants/{tenant_id}/edit")
def tenant_edit(
    request: Request,
    house_id: str,
    lease_id: str,
    tenant_id: str,
    name: str = Form(...),
    id_number: str = Form(""),
    phone: str = Form(...),
    primary: bool = Form(False),
    id_front: UploadFile | None = File(None),
    id_back: UploadFile | None = File(None),
):
    store = get_store()
    front_path = save_upload(id_front) if id_front and id_front.filename else None
    back_path = save_upload(id_back) if id_back and id_back.filename else None
    error_msg = None
    try:
        store.update_tenant(house_id, lease_id, tenant_id, name, id_number, phone, primary, front_path, back_path)
    except StoreError as error:
        error_msg = str(error)
    finally:
        if front_path:
            front_path.unlink(missing_ok=True)
        if back_path:
            back_path.unlink(missing_ok=True)
    if is_htmx(request):
        lease = store.get_lease(house_id, lease_id).to_dict()
        tenants = [tenant_view(item.to_dict()) for item in store.list_tenants(house_id, lease_id)]
        return templates.TemplateResponse(
            request=request,
            name="partials/tenant_list.html",
            context={"request": request, "house": {"id": house_id}, "lease": {**lease, "tenants": tenants}, "error": error_msg, "message": f"租客 {name} 已更新" if not error_msg else None},
        )
    if error_msg:
        return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}/tenants", error=error_msg)
    return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}/tenants", message=f"租客 {name} 已更新")


@app.post("/houses/{house_id}/leases/{lease_id}/tenants/{tenant_id}/delete")
def tenant_delete(
    request: Request,
    house_id: str,
    lease_id: str,
    tenant_id: str,
):
    store = get_store()
    error_msg = None
    try:
        store.delete_tenant(house_id, lease_id, tenant_id)
    except StoreError as error:
        error_msg = str(error)
    if is_htmx(request):
        lease = store.get_lease(house_id, lease_id).to_dict()
        tenants = [tenant_view(item.to_dict()) for item in store.list_tenants(house_id, lease_id)]
        return templates.TemplateResponse(
            request=request,
            name="partials/tenant_list.html",
            context={"request": request, "house": {"id": house_id}, "lease": {**lease, "tenants": tenants}, "error": error_msg, "message": "租客已删除" if not error_msg else None},
        )
    if error_msg:
        return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}/tenants", error=error_msg)
    return redirect_with_message(f"/houses/{house_id}/leases/{lease_id}/tenants", message="租客已删除")


@app.get("/media/{house_id}/{kind}/{filename}")
def media_file(house_id: str, kind: str, filename: str):
    store = get_store()
    if kind not in {"photos", "videos"}:
        raise HTTPException(status_code=404, detail="Invalid media kind")
    path = store.house_dir(house_id) / "media" / kind / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(path)


@app.post("/houses/{house_id}/media/{kind}/{filename}/delete")
def house_media_delete(request: Request, house_id: str, kind: str, filename: str):
    store = get_store()
    try:
        store.delete_house_media(house_id, kind, filename)
    except StoreError as error:
        if is_htmx(request):
            return HTMLResponse(f'<div class="notice error">{error}</div>')
        return redirect_with_message(f"/houses/{house_id}/edit", error=str(error))
    if is_htmx(request):
        media = list_house_media(store, house_id)
        return templates.TemplateResponse(
            request=request,
            name="partials/media_list.html",
            context={"request": request, "house": {"id": house_id}, "media": media, "message": "素材已删除"},
        )
    return redirect_with_message(f"/houses/{house_id}/edit", message="素材已删除")


def _tag_editor_context(store: LemonStore, house_id: str, request: Request) -> dict[str, Any]:
    house = store.get_house(house_id)
    return {"request": request, "house": house.to_dict(), "all_tags": store.all_tags()}


@app.post("/houses/{house_id}/tags")
def house_tag_add(request: Request, house_id: str, tag: str = Form(...)):
    store = get_store()
    house = store.get_house(house_id)
    tag = tag.strip()
    if tag and tag not in house.tags:
        house.tags.append(tag)
        store.save_house(house)
    if is_htmx(request):
        return templates.TemplateResponse(
            request=request,
            name="partials/tag_editor.html",
            context=_tag_editor_context(store, house_id, request),
        )
    return redirect_with_message(f"/houses/{house_id}/edit", message=f"标签「{tag}」已添加")


@app.post("/houses/{house_id}/tags/delete")
def house_tag_delete(request: Request, house_id: str, tag: str = Form(...)):
    store = get_store()
    house = store.get_house(house_id)
    if tag in house.tags:
        house.tags.remove(tag)
        store.save_house(house)
    if is_htmx(request):
        return templates.TemplateResponse(
            request=request,
            name="partials/tag_editor.html",
            context=_tag_editor_context(store, house_id, request),
        )
    return redirect_with_message(f"/houses/{house_id}/edit", message=f"标签「{tag}」已删除")


@app.get("/tags")
def tag_manage(request: Request):
    store = get_store()
    all_tags = store.all_tags()
    tag_counts: dict[str, int] = {}
    for house in store.list_houses():
        for t in house.tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    tags = [{"name": t, "count": tag_counts.get(t, 0)} for t in all_tags]
    return templates.TemplateResponse(
        request=request,
        name="tag_manage.html",
        context=request_context(request, tags=tags),
    )


@app.post("/tags/create")
def tag_create(request: Request, name: str = Form(...)):
    store = get_store()
    name = name.strip()
    if not name:
        return redirect_with_message("/tags", error="标签名不能为空")
    store.create_tag(name)
    return redirect_with_message("/tags", message=f"标签「{name}」已创建")


@app.post("/tags/rename")
def tag_rename(request: Request, old_name: str = Form(...), new_name: str = Form(...)):
    store = get_store()
    new_name = new_name.strip()
    if not new_name:
        return redirect_with_message("/tags", error="标签名不能为空")
    count = store.rename_tag(old_name, new_name)
    return redirect_with_message("/tags", message=f"标签「{old_name}」已重命名为「{new_name}」，影响 {count} 套房屋")


@app.post("/tags/delete")
def tag_global_delete(request: Request, name: str = Form(...)):
    store = get_store()
    count = store.delete_tag(name)
    return redirect_with_message("/tags", message=f"标签「{name}」已删除，影响 {count} 套房屋")


def _patch_proactor_transport() -> None:
    """Suppress harmless ConnectionResetError on Windows ProactorEventLoop."""
    import sys

    if sys.platform != "win32":
        return
    try:
        import asyncio.proactor_events as pe

        _orig = pe._ProactorBasePipeTransport._call_connection_lost

        def _patched(self, *args: Any, **kwargs: Any) -> None:
            try:
                _orig(self, *args, **kwargs)
            except (ConnectionResetError, OSError):
                pass

        pe._ProactorBasePipeTransport._call_connection_lost = _patched
    except Exception:
        pass


def run() -> None:
    _patch_proactor_transport()
    host = os.environ.get("LEMON_HOME_HOST", "0.0.0.0")
    port = int(os.environ.get("LEMON_HOME_PORT", "8000"))
    uvicorn.run(
        "lemon_home.web:app",
        host=host,
        port=port,
        reload=True,
        reload_dirs=[str(BASE_DIR)],
    )


if __name__ == "__main__":
    run()
