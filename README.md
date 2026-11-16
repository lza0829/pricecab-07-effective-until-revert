# 06-pricecab（价签柜）

Pricecab — 改价单按预约时刻生效，现行价签可查，并与销售夹具对账。

与 Feedstitch（多源归并）、Batchgate（作业编排）、Syncledger（双端对账）、Changecast（CDC 投影）、Proofpack（采样放行）主链路不同。  
不是电商订单/购物车，不是仓库进出库 CRUD，不是考勤 OA。夹具全本地。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5600 |
| API | http://localhost:10600 |

## 主链（0-1）

选商品建改价单（写明生效时刻，可选生效截止）→ 拨业务钟或点「应用到期」→ 现行价签更新 → 业务钟越过截止的已应用单走「回落到期」（全局一种回落目标：牌价 list_price 或截止前上一有效价）→ 销售对账看一致/不一致

- 回落与「应用到期」互斥：存在过截止未回落的单时，应用到期被拒（409）。
- 截止前作废会取消未来回落；过截止的单只能走回落批次，不能作废。
- 回落批次（RB-xxxx）与应用批次（AB-xxxx）同成同败写入价签本与价格历史。

## 测试

```bash
cd backend && python -m pytest tests/test_revert_flow.py -v
```

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。
