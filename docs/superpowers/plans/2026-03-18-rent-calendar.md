# 收租总览甘特图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Gantt chart page at `/rent-calendar` showing all active leases' rent collection timeline with overdue/upcoming/paid status indicators.

**Architecture:** New route + helper function in `web.py`, new Jinja2 template with CSS Grid-based Gantt chart, new partial for HTMX filtering. Zero new JS dependencies — pure CSS tooltips and HTMX for interaction.

**Tech Stack:** FastAPI, Jinja2, HTMX, CSS Grid

**Spec:** `docs/superpowers/specs/2026-03-18-rent-calendar-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `src/lemon_home/web.py` | New route `GET /rent-calendar`, helper `build_rent_gantt()`, nav_active logic |
| Create | `src/lemon_home/templates/rent_calendar.html` | Full page: summary cards + gantt chart + filter buttons |
| Create | `src/lemon_home/templates/partials/gantt_body.html` | Gantt chart body (rows + bars), returned alone for HTMX filter |
| Modify | `src/lemon_home/templates/base.html` | Add "收租总览" nav tab |
| Modify | `src/lemon_home/static/app.css` | Add `.gantt-*` styles |

---

### Task 1: Add `build_rent_gantt()` helper function to web.py

**Files:**
- Modify: `src/lemon_home/web.py` (after `build_payment_timeline()` around line 234)

This is the core data logic. It enumerates all rent periods for all active leases within a time window and assigns a status to each period.

- [ ] **Step 1: Add the helper function**

Add after the `build_payment_timeline()` function (line 234) in `web.py`:

```python
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
```

- [ ] **Step 2: Verify no syntax errors**

Run: `cd src && python -c "from lemon_home.web import build_rent_gantt; print('OK')" && cd ..`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/lemon_home/web.py
git commit -m "feat: add build_rent_gantt() helper for rent calendar data"
```

---

### Task 2: Add `/rent-calendar` route and nav_active logic

**Files:**
- Modify: `src/lemon_home/web.py` (route + `request_context()`)

- [ ] **Step 1: Update `request_context()` to handle rent-calendar nav**

In `request_context()` (around line 81-98), add a condition for the rent-calendar path. Change:

```python
    if path == "/":
        nav_active = "dashboard"
    elif path.startswith("/contracts"):
        nav_active = "contracts"
    elif path == "/houses" or path == "/houses/new":
        nav_active = "houses"
    else:
        nav_active = ""
```

To:

```python
    if path == "/":
        nav_active = "dashboard"
    elif path.startswith("/rent-calendar"):
        nav_active = "rent-calendar"
    elif path.startswith("/contracts"):
        nav_active = "contracts"
    elif path == "/houses" or path == "/houses/new":
        nav_active = "houses"
    else:
        nav_active = ""
```

- [ ] **Step 2: Add the route**

Add after the `dashboard()` route (after line 253):

```python
@app.get("/rent-calendar")
def rent_calendar(request: Request):
    store = get_store()
    today_date = date.today()
    offset = int(request.query_params.get("offset", "0"))
    status_filter = request.query_params.get("status", "all")

    # Shift window by offset months, but keep real today for status logic
    window_center = _add_months(today_date, offset)
    gantt = build_rent_gantt(store, real_today=today_date, window_center=window_center, months_before=2, months_after=2)

    # Apply status filter to rows
    if status_filter == "urgent":
        gantt["rows"] = [r for r in gantt["rows"] if r["worst_status"] in ("overdue", "partial", "upcoming")]
    elif status_filter == "overdue":
        gantt["rows"] = [r for r in gantt["rows"] if r["worst_status"] in ("overdue", "partial")]

    if is_htmx(request):
        return templates.TemplateResponse(
            request=request,
            name="partials/gantt_body.html",
            context={"request": request, "gantt": gantt, "today": today_date.isoformat(), "offset": offset, "status_filter": status_filter},
        )

    return templates.TemplateResponse(
        request=request,
        name="rent_calendar.html",
        context=request_context(request, gantt=gantt, offset=offset, status_filter=status_filter),
    )
```

- [ ] **Step 3: Verify route loads without error**

Run: `python -c "from lemon_home.web import app; print([r.path for r in app.routes if hasattr(r, 'path') and 'rent' in r.path])"`
Expected: `['/rent-calendar']`

- [ ] **Step 4: Commit**

```bash
git add src/lemon_home/web.py
git commit -m "feat: add /rent-calendar route with filtering and HTMX support"
```

---

### Task 3: Add navigation tab in base.html

**Files:**
- Modify: `src/lemon_home/templates/base.html` (line 19, nav-tabs area)

- [ ] **Step 1: Add the nav tab**

In `base.html`, inside the `<nav class="nav-tabs">` block (line 17-20), add a new tab after the "合同管理" tab:

Change:

```html
          <a class="nav-tab {% if nav_active == 'contracts' %}active{% endif %}" href="/contracts">合同管理</a>
```

To:

```html
          <a class="nav-tab {% if nav_active == 'contracts' %}active{% endif %}" href="/contracts">合同管理</a>
          <a class="nav-tab {% if nav_active == 'rent-calendar' %}active{% endif %}" href="/rent-calendar">收租总览</a>
```

- [ ] **Step 2: Commit**

```bash
git add src/lemon_home/templates/base.html
git commit -m "feat: add '收租总览' nav tab to base template"
```

---

### Task 4: Create gantt_body.html partial template

**Files:**
- Create: `src/lemon_home/templates/partials/gantt_body.html`

This is the reusable gantt chart body, returned as a partial for HTMX filter requests and included by the full page template.

- [ ] **Step 1: Create the partial**

```html
<div id="gantt-body">
  {% if gantt.rows %}
  <div class="gantt-chart" style="--total-days: {{ gantt.total_days }};">
    {# Month header #}
    <div class="gantt-header-row gantt-months">
      <div class="gantt-label-cell"></div>
      <div class="gantt-timeline">
        {% for m in gantt.months %}
        <span class="gantt-month" style="grid-column: {{ m.col_start }} / {{ m.col_end }};">{{ m.label }}</span>
        {% endfor %}
      </div>
    </div>

    {# Week tick header #}
    <div class="gantt-header-row gantt-weeks">
      <div class="gantt-label-cell"></div>
      <div class="gantt-timeline">
        {% for w in gantt.weeks %}
        <span class="gantt-week-tick" style="grid-column: {{ w.col }};">{{ w.label }}</span>
        {% endfor %}
      </div>
    </div>

    {# Data rows #}
    {% for row in gantt.rows %}
    <div class="gantt-row">
      <div class="gantt-label-cell" title="{{ row.label }}">{{ row.label }}</div>
      <div class="gantt-timeline">
        {# Today line #}
        {% if gantt.today_col %}
        <div class="gantt-today" style="grid-column: {{ gantt.today_col }};"></div>
        {% endif %}

        {% for p in row.periods %}
        <a class="gantt-bar {{ p.status }}"
           href="/houses/{{ p.house_id }}/leases/{{ p.lease_id }}"
           style="grid-column: {{ p.col_start }} / {{ p.col_end }};"
           data-tooltip="第{{ p.num }}期 | {{ p.start }} ~ {{ p.end }} | ¥{{ '%.2f'|format(p.amount) }} | {{ p.status }}">
        </a>
        {% endfor %}
      </div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty-state">
    <p>当前筛选条件下没有收租记录。</p>
  </div>
  {% endif %}
</div>
```

- [ ] **Step 2: Commit**

```bash
git add src/lemon_home/templates/partials/gantt_body.html
git commit -m "feat: create gantt_body.html partial for rent calendar"
```

---

### Task 5: Create rent_calendar.html full page template

**Files:**
- Create: `src/lemon_home/templates/rent_calendar.html`

- [ ] **Step 1: Create the template**

```html
{% extends "base.html" %}
{% block title %}收租总览 - Lemon Home{% endblock %}
{% block content %}

{# Summary cards #}
<section class="stat-grid cols-3">
  <article class="stat-card" style="border-left: 4px solid #b33a1f;">
    <span>逾期</span>
    <strong style="color: #b33a1f;">{{ gantt.stats.overdue_count }} 笔 ¥{{ "%.2f"|format(gantt.stats.overdue_amount) }}</strong>
  </article>
  <article class="stat-card" style="border-left: 4px solid var(--accent);">
    <span>3 天内待收</span>
    <strong style="color: var(--accent);">{{ gantt.stats.upcoming_count }} 笔 ¥{{ "%.2f"|format(gantt.stats.upcoming_amount) }}</strong>
  </article>
  <article class="stat-card" style="border-left: 4px solid #1a5928;">
    <span>本月已收</span>
    <strong style="color: #1a5928;">¥{{ "%.2f"|format(gantt.stats.paid_amount) }}</strong>
  </article>
</section>

{# Filter bar + time navigation #}
<section class="filter-bar" style="justify-content: space-between;">
  <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
    <a class="filter-chip {% if status_filter == 'all' %}active{% endif %}"
       href="/rent-calendar?status=all&offset={{ offset }}"
       hx-get="/rent-calendar?status=all&offset={{ offset }}" hx-target="#gantt-body" hx-swap="outerHTML">全部</a>
    <a class="filter-chip {% if status_filter == 'urgent' %}active{% endif %}"
       href="/rent-calendar?status=urgent&offset={{ offset }}"
       hx-get="/rent-calendar?status=urgent&offset={{ offset }}" hx-target="#gantt-body" hx-swap="outerHTML">逾期+待收</a>
    <a class="filter-chip {% if status_filter == 'overdue' %}active{% endif %}"
       href="/rent-calendar?status=overdue&offset={{ offset }}"
       hx-get="/rent-calendar?status=overdue&offset={{ offset }}" hx-target="#gantt-body" hx-swap="outerHTML">仅逾期</a>
  </div>
  <div style="display: flex; gap: 0.5rem;">
    <a class="button button-sm" href="/rent-calendar?offset={{ offset - 1 }}&status={{ status_filter }}">← 上月</a>
    <a class="button button-sm" href="/rent-calendar?offset=0&status={{ status_filter }}">今天</a>
    <a class="button button-sm" href="/rent-calendar?offset={{ offset + 1 }}&status={{ status_filter }}">下月 →</a>
  </div>
</section>

{# Gantt chart #}
<section style="margin-top: 1.2rem;">
  {% include "partials/gantt_body.html" %}
</section>

{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add src/lemon_home/templates/rent_calendar.html
git commit -m "feat: create rent_calendar.html full page template"
```

---

### Task 6: Add Gantt chart CSS styles

**Files:**
- Modify: `src/lemon_home/static/app.css` (append at end, before the `@media` responsive block at line 1137)

- [ ] **Step 1: Add gantt styles**

Add before the `@media (max-width: 860px)` block:

```css
/* Gantt chart */
.gantt-chart {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow-x: auto;
  padding: 0.8rem 0;
}

.gantt-header-row,
.gantt-row {
  display: grid;
  grid-template-columns: 180px 1fr;
  align-items: center;
  min-height: 36px;
}

.gantt-row {
  border-top: 1px solid var(--line);
}

.gantt-row:hover {
  background: rgba(36, 82, 70, 0.03);
}

.gantt-label-cell {
  padding: 0.4rem 0.8rem;
  font-size: 0.88rem;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--ink);
}

.gantt-header-row .gantt-label-cell {
  color: var(--muted);
  font-size: 0.8rem;
}

.gantt-timeline {
  display: grid;
  grid-template-columns: repeat(var(--total-days), minmax(4px, 1fr));
  position: relative;
  height: 100%;
  align-items: center;
  padding: 2px 0;
}

.gantt-month {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--brand);
  text-align: center;
  padding: 0.3rem 0;
  border-left: 1px solid var(--line);
}

.gantt-week-tick {
  font-size: 0.7rem;
  color: var(--muted);
  text-align: center;
  border-left: 1px dotted var(--line);
  padding: 0.2rem 0;
}

.gantt-today {
  grid-row: 1;
  width: 2px;
  height: 100%;
  background: var(--accent);
  opacity: 0.6;
  z-index: 1;
  pointer-events: none;
}

.gantt-bar {
  display: block;
  height: 22px;
  border-radius: 6px;
  min-width: 6px;
  z-index: 2;
  position: relative;
  transition: opacity 0.15s;
}

.gantt-bar:hover {
  opacity: 0.8;
}

.gantt-bar.paid {
  background: #a8d5a2;
}

.gantt-bar.partial,
.gantt-bar.upcoming {
  background: #f5c882;
}

.gantt-bar.overdue {
  background: #e8917e;
}

.gantt-bar.future {
  background: #d5d5d5;
}

/* Tooltip */
.gantt-bar[data-tooltip]:hover::after {
  content: attr(data-tooltip);
  position: absolute;
  bottom: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  padding: 0.4rem 0.7rem;
  background: var(--ink);
  color: #fff;
  font-size: 0.78rem;
  border-radius: 8px;
  white-space: nowrap;
  z-index: 10;
  pointer-events: none;
}
```

- [ ] **Step 2: Add responsive rules for gantt**

Inside the existing `@media (max-width: 860px)` block, add:

```css
  .gantt-label-cell {
    min-width: 120px;
    font-size: 0.8rem;
  }

  .gantt-header-row,
  .gantt-row {
    grid-template-columns: 120px 1fr;
  }
```

- [ ] **Step 3: Commit**

```bash
git add src/lemon_home/static/app.css
git commit -m "feat: add gantt chart CSS styles for rent calendar"
```

---

### Task 7: End-to-end manual test

- [ ] **Step 1: Ensure test data exists**

Run: `python seed_data.py` (if data directory is empty)

- [ ] **Step 2: Start the web server**

Run: `python -m lemon_home.web`

- [ ] **Step 3: Verify the page loads**

Open `http://localhost:8000/rent-calendar` in browser. Check:
- Navigation tab "收租总览" is visible and highlighted
- Summary cards show counts and amounts
- Gantt rows display for each house with active lease
- Color coding: green (paid), orange (upcoming/partial), red (overdue), gray (future)
- "今天" vertical line appears at current date
- Hover on bars shows tooltip with period details
- Click a bar navigates to lease detail page
- Filter buttons (全部/逾期+待收/仅逾期) work via HTMX
- Time navigation (上月/今天/下月) shifts the window
- Page scrolls horizontally if needed

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address issues found during manual testing"
```
