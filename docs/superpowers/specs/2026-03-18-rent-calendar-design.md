# 收租总览甘特图 设计文档

## 概述

在 Lemon Home Web UI 中新增独立页面"收租总览"，以甘特图形式展示所有在租房屋的收租时间线，帮助房东提前发现待收和逾期租金，避免漏收。

## 需求摘要

- **用户场景：** 房东打开网页，一眼看出哪些房子的租金快到期/已逾期，无需逐个点进合同
- **规模：** 10-50 套房屋
- **提醒逻辑：** 到期前 3 天标记为"待收"（橙色）
- **可视化形式：** 甘特图（每行一个房屋，横轴为时间）
- **位置：** 独立页面，导航栏新增入口，路由 `/rent-calendar`
- **无主动推送：** 纯被动查看

## 数据逻辑

### 收租期计算

新增 `build_rent_gantt()` 函数，参考 `calc_next_rent()` 的推算思路但独立实现完整的期数枚举：根据合同 `start_date` + `payment_cycle`，从第 1 期开始循环推算每一期的起止日期，直到超出时间窗口或超出合同 `end_date`（如有）为止。

**到期日定义：** 每期的 `period_start`（即该期租金起始日）即为该期的到期日。例如月付合同起始日为 1 月 1 日，则第 2 期到期日为 2 月 1 日——这天之前应完成收款。

**合同 end_date 为空：** 视为无固定期限，期数生成到时间窗口结束即停止。

**一个房屋多个 active 合同：** 每个合同独占一行，行标签显示为「地址（合同 N）」以区分。

### 状态判定（以当天日期为基准）

| 状态 | 标识 | 条件 | 颜色 |
|------|------|------|------|
| 已收 | `paid` | 该期已有 RENT 账单且 status=paid | 绿色 |
| 部分收 | `partial` | 该期已有 RENT 账单且 status=partial | 橙色（同待收） |
| 待收 | `upcoming` | 未收/无账单，到期日（period_start）在今天之后 ≤3 天（含今天） | 橙色 |
| 逾期 | `overdue` | 未收/无账单，到期日（period_start）早于今天 | 红色 |
| 未来 | `future` | 未收/无账单，到期日在 3 天之后 | 灰色 |

### 时间窗口

默认展示当前月 ±2 个月（共约 5 个月），横轴以周为刻度单位。页面提供「上一月 / 下一月」按钮，通过 `offset` 参数平移时间窗口。

## 页面结构

### 1. 顶部摘要条

三个统计卡片，水平排列：

- **逾期 N 笔 ¥X**（红色）
- **3 天内待收 N 笔 ¥X**（橙色）
- **本月已收 ¥X**（绿色）— "本月"指 period_start 落在当月的已收账单金额

### 2. 甘特图主体

- **纵轴：** 每行一个房屋，显示地址（不显示内部 ID）
- **排序：** 有逾期的排最上 → 有待收的其次 → 全部已收的最下
- **横轴：** 5 个月时间轴，周为刻度，"今天"画竖虚线
- **色块：** 每个收租期一个横条，宽度 = payment_cycle 对应的天数，颜色按状态
- **Hover：** CSS tooltip 显示「第 N 期 | 起止日期 | ¥金额 | 状态」
- **点击：** 跳转到对应合同详情页 `/houses/{house_id}/leases/{lease_id}`

### 3. 底部筛选

按钮组：全部 / 仅逾期+待收 / 仅逾期。HTMX 局部刷新甘特图区域。

**只展示 active 状态的合同。**

## 技术实现

### 后端（web.py）

- 新增路由 `GET /rent-calendar`
  - Query 参数：`status`（可选，筛选）、`offset`（可选，月偏移，默认 0）
- 新增辅助函数 `build_rent_gantt(store, today, months_before=2, months_after=2)`
  - 遍历所有房屋的 active 合同
  - 对每个合同，计算从第 1 期到时间窗口结束的所有收租期
  - 对每一期判定状态（paid/upcoming/overdue/future）
  - 返回结构化数据：`[{house, address, periods: [{num, start, end, amount, status}]}]`
- 导航激活标识：`request_context()` 中增加 `rent-calendar` 判断

### 模板（templates/rent_calendar.html）

- 继承 `base.html`
- CSS Grid 布局：`grid-template-columns` 按时间窗口天数动态生成
- 每个收租期色块为 `<a>` 元素，通过 `grid-column` 定位到对应日期列
- Tooltip 用 CSS `::after` + `data-*` 属性实现，无 JS
- 筛选按钮：`hx-get="/rent-calendar?status=overdue"` + `hx-target="#gantt-body"` 局部替换
- 甘特图主体区域抽取为 `partials/gantt_body.html`，主模板 include 它；HTMX 筛选时后端检测 `is_htmx()` 仅返回 partial

### 样式（static/app.css）

新增 `.gantt-*` 系列样式：

- `.gantt-container` — 整体容器，横向可滚动
- `.gantt-row` — 每行（房屋）
- `.gantt-bar` — 收租期色块
- `.gantt-bar.paid` / `.upcoming` / `.overdue` / `.future` — 状态颜色
- `.gantt-today` — 今天竖线
- `.gantt-tooltip` — hover 提示

颜色复用现有 CSS 变量体系，新增：`--color-overdue`, `--color-upcoming`, `--color-future`

### 导航栏（base.html）

新增"收租总览"链接，指向 `/rent-calendar`。

### 不变更的文件

- `models.py` — 无改动
- `store.py` — 无改动
- `cli.py` — 无改动

## 约束

- 零新 JS 依赖
- 与现有暖色调圆角卡片设计风格一致
- 敏感信息不在此页面暴露
- 仅展示 active 合同
