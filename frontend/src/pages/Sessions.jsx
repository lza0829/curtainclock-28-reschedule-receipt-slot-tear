import { useEffect, useState } from "react";
import { api } from "../api";

function fmtMin(m) {
  const h = String(Math.floor(m / 60)).padStart(2, "0");
  const mm = String(m % 60).padStart(2, "0");
  return `${h}:${mm}`;
}

const statusLabel = { draft: "待开窗", live: "占用中", done: "已结束" };

// live 改挂入口：目标槽下拉 + 改挂按钮。改挂是占用索引的键迁移，
// 成功后回执新槽 = 索引现行键 = 本页展示槽，三处同源可互证。
function RescheduleCell({ row, slots, onDone, onError }) {
  const [target, setTarget] = useState(row.slot_code);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    onError("");
    try {
      const r = await api.reschedule(row.code, target);
      onDone(
        `已改挂 ${r.lease_no}：${r.old_slot} → ${r.new_slot}；在途租约 ${r.open_leases}`
      );
    } catch (e) {
      onError(e.message);
      await onDone("");
    } finally {
      setBusy(false);
    }
  }

  const occupiedBy = (code) => {
    const s = slots.find((x) => x.code === code);
    return s && s.occupied ? "（占用）" : "";
  };

  return (
    <span className="reschedule">
      <select
        value={target}
        disabled={busy}
        onChange={(e) => setTarget(e.target.value)}
      >
        {slots
          .filter((s) => s.active)
          .map((s) => (
            <option key={s.code} value={s.code}>
              {s.code}
              {s.code === row.slot_code ? "（现行）" : occupiedBy(s.code)}
            </option>
          ))}
      </select>
      <button
        className="secondary"
        disabled={busy || target === row.slot_code}
        onClick={submit}
      >
        改挂
      </button>
    </span>
  );
}

export default function Sessions() {
  const [rows, setRows] = useState([]);
  const [slots, setSlots] = useState([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function load() {
    setErr("");
    try {
      const [w, s] = await Promise.all([api.windows(), api.slots()]);
      setRows(w);
      setSlots(s);
    } catch (e) {
      setErr(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function openLease(code) {
    setMsg("");
    setErr("");
    try {
      const r = await api.openLease(code);
      const tail = r.degraded
        ? `（主槽冲突，已降级到候补槽 ${r.slot_code}）`
        : `（主槽 ${r.slot_code}）`;
      setMsg(`已开窗，租约 ${r.lease_no}${tail}；在途租约 ${r.open_leases}`);
      await load();
    } catch (e) {
      setErr(e.message);
      await load();
    }
  }

  async function afterReschedule(text) {
    if (text) setMsg(text);
    await load();
  }

  return (
    <section className="panel">
      <h2>批窗</h2>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="msg err">{err}</div>}
      <table>
        <thead>
          <tr>
            <th>批窗</th>
            <th>时段</th>
            <th>主槽 / 执行器</th>
            <th>就绪</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const rd = r.readiness || {};
            return (
              <tr key={r.code}>
                <td>
                  <div>{r.title}</div>
                  <small>{r.code}</small>
                </td>
                <td>
                  {r.day}
                  <br />
                  {fmtMin(r.start_min)}–{fmtMin(r.end_min)}
                </td>
                <td>
                  {r.slot_code}
                  <br />
                  {r.executor_code}
                </td>
                <td>
                  {rd.ready ? (
                    <span className="tag live">就绪</span>
                  ) : rd.degradable ? (
                    <span className="tag standby">
                      可降级 · 候补 {rd.standby_free?.join("、")}
                    </span>
                  ) : (
                    <span className="tag bad">
                      {(rd.reasons || []).join("；") || "未就绪"}
                    </span>
                  )}
                </td>
                <td>
                  <span className={`tag ${r.status}`}>
                    {statusLabel[r.status] || r.status}
                  </span>
                </td>
                <td>
                  {r.status === "live" ? (
                    <RescheduleCell
                      key={r.slot_code}
                      row={r}
                      slots={slots}
                      onDone={afterReschedule}
                      onError={(m) => {
                        setMsg("");
                        setErr(m);
                      }}
                    />
                  ) : (
                    <button
                      disabled={r.status !== "draft"}
                      onClick={() => openLease(r.code)}
                    >
                      开窗占用
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
