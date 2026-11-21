import { useEffect, useState } from "react";
import { api } from "../api";

function fmtMin(m) {
  const h = String(Math.floor(m / 60)).padStart(2, "0");
  const mm = String(m % 60).padStart(2, "0");
  return `${h}:${mm}`;
}

export default function Leases() {
  const [rows, setRows] = useState([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function load() {
    setRows(await api.leases());
  }

  useEffect(() => {
    load().catch((e) => setErr(e.message));
  }, []);

  async function closeOne(lease_no) {
    setMsg("");
    setErr("");
    try {
      const r = await api.closeLease(lease_no);
      setMsg(`已关窗释放 ${r.lease_no}；在途租约 ${r.open_leases}`);
      await load();
    } catch (e) {
      setErr(e.message);
    }
  }

  return (
    <section className="panel">
      <h2>租约</h2>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="msg err">{err}</div>}
      <table>
        <thead>
          <tr>
            <th>租约号</th>
            <th>批窗</th>
            <th>占用槽</th>
            <th>执行器</th>
            <th>时段</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.lease_no}>
              <td>{r.lease_no}</td>
              <td>{r.window_code}</td>
              <td>
                {r.slot_code}
                {r.degraded ? (
                  <span className="tag standby"> 降级自 {r.main_slot_code}</span>
                ) : null}
              </td>
              <td>{r.executor_code}</td>
              <td>
                {r.day} {fmtMin(r.start_min)}–{fmtMin(r.end_min)}
              </td>
              <td>{r.status === "open" ? "占用中" : "已释放"}</td>
              <td>
                <button
                  className="secondary"
                  disabled={r.status !== "open"}
                  onClick={() => closeOne(r.lease_no)}
                >
                  关窗释放
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
