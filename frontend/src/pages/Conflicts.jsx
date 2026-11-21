import { useEffect, useState } from "react";
import { api } from "../api";

export default function Conflicts() {
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .conflicts()
      .then(setRows)
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <div className="msg err">{err}</div>;

  return (
    <section className="panel">
      <h2>冲突桶</h2>
      <table>
        <thead>
          <tr>
            <th>类型</th>
            <th>批窗</th>
            <th>资源</th>
            <th>原因</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={5}>暂无冲突</td>
            </tr>
          )}
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.kind}</td>
              <td>{r.window_code}</td>
              <td>{r.resource_code}</td>
              <td>{r.reason}</td>
              <td>{r.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
