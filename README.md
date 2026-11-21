# Curtainclock（幕窗钟）

骨架：**批窗定义 → 算力槽/执行器资源依赖就绪 → 开窗租约占用 → 关窗释放与冲突水位互证**

- 前端：http://127.0.0.1:6100 （React + Vite）
- API：http://127.0.0.1:11100

## 并发口径（全局唯一）：开窗写锁内一次扫落

主槽冲突后走**候补降级**（本题默认允许降级占用，不裁决硬拒绝/降级总开关）。
候补防并发双占采用 **写锁臂**：

- 不设预占表。所有占用类写入都经 `app.db.transaction()` 的 `BEGIN IMMEDIATE` 全局写锁。
- `open_lease` 在同一把写锁、同一连接、同一事务内完成：读批窗 → 就绪复检 →
  按优先级（`standby_priority` 升序、`capacity` 降序、`code` 升序）扫同主题候补 →
  直接插入正式 `open` 租约。**检查与插入同锁；锁外不先读后写。**
- 后到者在自己的 `BEGIN IMMEDIATE` 拿到锁时，必然看到先到者已提交的 open，
  候补扫描会将其排除，因此并发两路降级抢同一候补**不会双 open**：
  后到者改选下一个空闲候补，候补耗尽则失败（409，可查原因）。
- 数据库层兜底（第二道防线）：`ux_leases_one_open_per_window` 唯一索引 +
  `trg_lease_slot_overlap` / `trg_lease_executor_overlap` 触发器，
  即便应用层退化也不会留下双开。
- 水位与实槽同源互证：`open_leases`/`conflict_count` 水位应恒等于实际计数，
  且 `live_sessions` 与 open 租约数一致；`/api/dashboard` 返回
  `watermarks_consistent` 供页面显示。

## live 改挂口径（全局唯一）：以占用索引现行键为准

改挂不是「先关窗再开」，也不是只写回执快照——而是**写锁内一次原子迁移占用索引键**：

- `POST /api/reschedule` 在 `transaction()` 写锁内完成：读 open 租约 →
  对新槽做冲突预检（与开窗同一套 ready 口径，排除自己）→
  **同一条 UPDATE** 释放旧键、占用新键（原子，无半截）→
  批窗展示字段（`windows.slot_code`）跟随索引 → 写回执。
- **回执只是索引的投影**：总览的「最近改挂」必须能由占用索引重放——
  索引现行键≠回执新槽时亮 `torn`，绝不宣称成功，也绝不用回执漂白
  `watermarks_consistent`；租约已关窗则落 `settled`（历史回执）。
- **水位按净变迁**：一键换一键，`open_leases` 净零不动；
  预检冲突失败落冲突行（`conflict_count` 净 +1），其余失败整体回滚——
  不留双键、不留假回执、不动展示字段。
- **并发改挂串行**：`BEGIN IMMEDIATE` 排队，后到者看到先到者已提交的现行键
  再迁；双目标并发最终只落一键，同目标并发幂等；改挂与关窗交错时，
  关窗先提交则改挂整体 409。
- 数据库层兜底同开窗：`trg_lease_slot_overlap_upd` /
  `trg_lease_executor_overlap_upd` 更新触发器把第二道防线延伸到迁移路径。

候补池在 seed 中给出：`S-ETL-SB1`（优先级 10）、`S-ETL-SB2`（20）、`S-BI-SB1`（10）。

## 本地冒烟（推荐）

```powershell
cd D:\Asolo11\claude-workspace\curtainclock\source
# 本机若无 3.11：py -V:Astral/CPython3.11.15 -m venv .venv
py -V:Astral/CPython3.11.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
$env:PYTHONPATH = "$PWD\backend"
Start-Process .\.venv\Scripts\uvicorn.exe -ArgumentList 'app.main:app','--host','127.0.0.1','--port','11100' -WorkingDirectory "$PWD\backend"
cd frontend
# 前端需 Node ≥18；不足时用 docker compose 起 web
npm install
npm run dev
```

浏览器打开 http://127.0.0.1:6100 ：开一节课 → 同算力槽冲突 → 关窗释放。

### 自检脚本

- `_smoke_api.py`：主槽开窗 → 同算力槽冲突**降级到候补** → 关窗，水位一致。
- `_concurrency_check.py`：多路并发降级抢同一候补池，验证「成功数=空闲候补数、
  失败可查、无双重占、同窗至多一笔 open」。
- `_stress_check.py`：多轮高并发压测，验证无 5xx、水位始终一致。
- `_reschedule_check.py`：live 改挂夹具——回执新槽=索引现行=批窗展示、
  旧槽可开/新槽挡/水位不双计、失败回滚不留双键、关窗交错整体回滚、
  并发双改挂收敛、撕裂注入下总览亮 torn 不漂白。

三脚本都只依赖标准库，对运行中的 API 发请求：

```bash
python _smoke_api.py                      # 默认连 11100
python _concurrency_check.py http://127.0.0.1:11100/api
python _stress_check.py      http://127.0.0.1:11100/api
python _reschedule_check.py  http://127.0.0.1:11100/api  # 第二参数可给库文件路径做撕裂注入
```

可用环境变量 `CURTAINCLOCK_DB` 把后端指向独立库文件，便于并行自检互不干扰。

## Docker

```powershell
docker compose up -d --build
```
