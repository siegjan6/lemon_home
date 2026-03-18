"""生成测试数据：5套房屋，含租约、租客、账单和付款记录。"""

import json
import shutil
from pathlib import Path

DATA_DIR = Path("data")

# 清除旧数据（保留 data 目录）
if (DATA_DIR / "houses").exists():
    shutil.rmtree(DATA_DIR / "houses")

from lemon_home.store import LemonStore

store = LemonStore(DATA_DIR)

# ── 房屋数据 ──
houses_info = [
    {"address": "浦东新区张杨路100弄3号101室", "area": 55.0, "layout": "1室1厅1卫"},
    {"address": "徐汇区漕溪北路28号502室", "area": 88.5, "layout": "2室1厅1卫"},
    {"address": "静安区南京西路1266号803室", "area": 120.0, "layout": "3室2厅2卫"},
    {"address": "杨浦区国定路350号201室", "area": 42.0, "layout": "1室0厅1卫"},
    {"address": "闵行区莘庄镇都市路88号1603室", "area": 95.0, "layout": "2室2厅1卫"},
]

# 每套房的租约租金和押金
lease_rents = {
    0: (4500, 4500),
    1: (7200, 7200),
    2: (12000, 24000),
    4: (6800, 6800),
}

# 创建占位身份证图片
tmp_dir = Path("tmp_seed")
tmp_dir.mkdir(exist_ok=True)
for name in ("id_front.jpg", "id_back.jpg"):
    p = tmp_dir / name
    if not p.exists():
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # 最小 JPEG 占位

# ── 租客信息 ──
tenants_info = [
    # house_idx, name, id_number, phone, is_primary
    (0, "张三", "310101199001010011", "13800138001", True),
    (0, "李小红", "310101199205150022", "13800138002", False),
    (1, "王伟", "310104198807220033", "13900139001", True),
    (2, "赵敏", "310106199503080044", "13700137001", True),
    (2, "钱磊", "310106199401120055", "13700137002", False),
    (2, "孙婷", "310106199612250066", "13700137003", False),
    (4, "周杰", "310112199108300077", "13600136001", True),
]

# ── 账单月份 ──
bill_months = ["2026-01", "2026-02", "2026-03"]

# ── 创建房屋 ──
created_houses = []
for info in houses_info:
    h = store.create_house(**info)
    created_houses.append(h)
    print(f"房屋 {h.id}: {h.address}")

# ── 为前3套和第5套创建租约（第4套保持空置）──
lease_map = {}  # house_idx -> lease
for idx in [0, 1, 2, 4]:
    h = created_houses[idx]
    rent, dep = lease_rents[idx]
    lease = store.create_lease(
        house_id=h.id,
        start_date="2025-12-01",
        monthly_rent=rent,
        deposit_amount=dep,
        end_date=None,
    )
    lease_map[idx] = lease
    print(f"  租约 {lease.id} -> {h.id}")

# ── 添加租客 ──
for house_idx, name, id_number, phone, is_primary in tenants_info:
    h = created_houses[house_idx]
    lease = lease_map[house_idx]
    t = store.add_tenant(
        house_id=h.id,
        lease_id=lease.id,
        name=name,
        id_number=id_number,
        phone=phone,
        is_primary=is_primary,
        id_front=tmp_dir / "id_front.jpg",
        id_back=tmp_dir / "id_back.jpg",
    )
    print(f"  租客 {t.id}: {name} ({'主' if is_primary else '副'})")

# ── 生成账单 ──
for idx in [0, 1, 2, 4]:
    h = created_houses[idx]
    lease = lease_map[idx]
    for month in bill_months:
        bill = store.generate_bill(
            house_id=h.id,
            lease_id=lease.id,
            month=month,
            due_date=f"{month}-01",
        )
        print(f"  账单 {bill.id} -> {h.id}/{lease.id}")

# ── 付款记录（模拟不同支付状态）──
payments = [
    # house_idx, month, amount, date, method
    # H0002 - 全部结清
    (0, "2026-01", 4500, "2026-01-03", "微信"),
    (0, "2026-02", 4500, "2026-02-02", "微信"),
    (0, "2026-03", 4500, "2026-03-01", "微信"),
    # H0003 - 1月结清，2月部分，3月未付
    (1, "2026-01", 7200, "2026-01-05", "银行转账"),
    (1, "2026-02", 4000, "2026-02-10", "银行转账"),
    # H0004 - 1月结清，2、3月未付（大户欠费）
    (2, "2026-01", 12000, "2026-01-02", "支付宝"),
    # H0006 - 全部结清
    (4, "2026-01", 6800, "2026-01-01", "现金"),
    (4, "2026-02", 6800, "2026-02-01", "现金"),
    (4, "2026-03", 6800, "2026-03-02", "微信"),
]

for house_idx, month, amount, paid_at, method in payments:
    h = created_houses[house_idx]
    lease = lease_map[house_idx]
    p = store.add_payment(
        house_id=h.id,
        lease_id=lease.id,
        bill_id=month,
        amount=amount,
        paid_at=paid_at,
        method=method,
        note=None,
    )
    print(f"  付款 {p.id}: {amount}元 -> {h.id}/{month}")

# 清理临时文件
shutil.rmtree(tmp_dir)

print("\n[OK] 测试数据生成完毕！")
print("  5套房屋（4套在租，1套空置）")
print("  7个租客")
print("  12张账单（6张已结清，1张部分支付，5张未支付）")
print("  9条付款记录")
