import { useEffect, useState } from "react";
import { api } from "../api";

function fmtMin(m) {
  const h = String(Math.floor(m / 60)).padStart(2, "0");
  const mm = String(m % 60).padStart(2, "0");
  return `${h}:${mm}`;
}

const statusLabel = { draft: "待开窗", live: "占用中", done: "已结束" };

export default function Sessions() {
  const [rows, setRows] = useState([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function load() {
    setErr("");
    try {
      setRows(await api.windows());
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
                  <button
                    disabled={r.status !== "draft"}
                    onClick={() => openLease(r.code)}
                  >
                    开窗占用
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

