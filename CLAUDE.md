# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Lemon Home 是一个公寓租赁管理系统，提供 CLI（面向 agent）和 Web UI（面向房东）两套界面。数据全部以 JSON 文件存储在本地，无数据库依赖。面向中国租房市场，界面和提示均为中文。

## Commands

```bash
# 安装（开发模式）
pip install -e .

# 启动 Web 服务（默认 0.0.0.0:8000）
lemon-home-web
# 或
python -m lemon_home.web

# CLI 使用
lemon-home house add --address "..." --area 80
lemon-home house list [--json]
lemon-home lease create --house-id H0001 --start-date 2025-01-01 --monthly-rent 3000 --deposit 6000

# 生成测试数据
python seed_data.py
```

环境变量：`LEMON_HOME_DATA_DIR`（默认 `./data`）、`LEMON_HOME_HOST`、`LEMON_HOME_PORT`

## Architecture

```
cli.py  ──┐
           ├──▶  store.py (LemonStore)  ──▶  data/ (JSON files on disk)
web.py  ──┘           ▲
                      │
                 models.py (dataclasses: House, Lease, Tenant, Bill, Payment, DepositRecord)
```

- **models.py** — 纯数据结构，dataclass + `to_dict()` 序列化
- **store.py** — 唯一的数据访问层。文件锁保证并发安全，自增 ID（H/L/T/P/M + 4位数字），JSONL 追加写入历史日志
- **web.py** — FastAPI + Jinja2 + HTMX。包含路由、模板过滤器（status_cn/cycle_cn/method_cn）、业务辅助函数。表单交互通过 HTMX 局部刷新
- **cli.py** — Typer 子命令。支持 `--json` 机器可读输出、`--full` 显示未脱敏信息

## Data Storage Layout

```
data/houses/H0001/
  ├── house.json
  ├── history.jsonl
  ├── media/{photos,videos}/
  └── leases/L0001/
        ├── lease.json, deposit.json, history.jsonl
        ├── tenants/T0001/{tenant.json, id_front.jpg, id_back.jpg}
        ├── bills/{RENT-001.json, ...}
        └── payments/{P0001.json, ...}
```

目录结构即数据层级，ID 即文件夹名。

## Web UI Conventions

- 模板在 `templates/`，可复用片段在 `templates/partials/`
- 静态资源在 `static/`（app.css、htmx.min.js、glightbox）
- HTMX 用于表单提交和局部更新，GLightbox 用于图片画廊
- CSS 变量定义在 `:root`，设计风格为暖色调圆角卡片
- 用户界面不暴露内部 ID（H0001、L0005 等），用地址、日期等有意义的信息替代
- 敏感信息（身份证号、手机号）在展示时自动脱敏
