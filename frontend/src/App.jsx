import { NavLink, Route, Routes } from "react-router-dom";
import Home from "./pages/Home.jsx";
import Sessions from "./pages/Sessions.jsx";
import Resources from "./pages/Resources.jsx";
import Leases from "./pages/Leases.jsx";
import Conflicts from "./pages/Conflicts.jsx";

export default function App() {
  return (
    <div className="shell">
      <header className="hero">
        <h1 className="brand">幕窗钟</h1>
        <p>
          批窗就绪后开窗占用算力槽与执行器，关窗释放；主槽冲突时在写锁内按优先级扫候补降级，
          冲突写入水位，数字同源可核。
        </p>
      </header>

      <nav className="nav">
        <NavLink to="/" end>
          总览
        </NavLink>
        <NavLink to="/windows">批窗</NavLink>
        <NavLink to="/resources">算力槽与执行器</NavLink>
        <NavLink to="/leases">租约</NavLink>
        <NavLink to="/conflicts">冲突</NavLink>
      </nav>

      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/windows" element={<Sessions />} />
        <Route path="/resources" element={<Resources />} />
        <Route path="/leases" element={<Leases />} />
        <Route path="/conflicts" element={<Conflicts />} />
      </Routes>
    </div>
  );
}
