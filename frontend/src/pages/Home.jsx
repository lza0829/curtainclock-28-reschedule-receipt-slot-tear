import { useEffect, useState } from "react";
import { api } from "../api";

export default function Home() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .dashboard()
      .then(setD)
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <div className="msg err">{err}</div>;
  if (!d) return <div className="panel">加载中…</div>;

  const ok = d.watermarks_consistent;

  return (
    <section className="panel">
      <h2>今日水位 · {d.fixture_day}</h2>
      <div className="stats">
        <div className="stat">
          在途租约
          <b>{d.open_leases}</b>
        </div>
        <div className="stat">
          冲突计数
          <b>{d.conflict_count}</b>
        </div>
        <div className="stat">
          占用中
          <b>{d.live_sessions}</b>
        </div>
        <div className="stat">
          待开窗
          <b>{d.draft_sessions}</b>
        </div>
        <div className="stat">
          降级占用
          <b>{d.degraded_leases}</b>
        </div>
      </div>
      <div className={`msg ${ok ? "" : "err"}`}>
        水位互证：
        {ok ? "一致" : "不一致"} · 在途租约水位 {d.open_leases} / 实槽{" "}
        {d.open_leases_actual}
        {" / 占用中批窗 "}
        {d.live_sessions} · 冲突水位 {d.conflict_count} / 实槽 {d.conflict_count_actual}
      </div>
      {d.reschedule_last && (
        <div
          className={`msg ${
            d.reschedule_state === "torn"
              ? "err"
              : d.reschedule_state === "settled"
                ? "muted"
                : ""
          }`}
        >
          最近改挂：{d.reschedule_last.window_code} ·{" "}
          {d.reschedule_last.old_slot} → {d.reschedule_last.new_slot}（租约{" "}
          {d.reschedule_last.lease_no}）· 索引重放
          {d.reschedule_state === "replayed" && `一致，现行槽 ${d.display_slot}`}
          {d.reschedule_state === "torn" &&
            `撕裂：回执新槽 ${d.reschedule_last.new_slot}，索引现行 ${d.display_slot}`}
          {d.reschedule_state === "settled" && "已关窗（历史回执）"}
        </div>
      )}
    </section>
  );
}
