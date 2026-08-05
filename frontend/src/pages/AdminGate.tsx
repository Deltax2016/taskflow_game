import { useEffect, useState } from "react";

import { api } from "../api";
import AdminPage from "./AdminPage";

// Гейт над админкой оператора. В игровом режиме бэкенд закрывает /api/admin/*
// сессией администратора, поэтому здесь спрашиваем логин и пароль. Признак
// «уже вошли» — успешный ответ любой админской ручки: отдельного эндпоинта
// «проверь сессию» не заводим, чтобы не плодить контракт ради одного экрана.
export default function AdminGate() {
  const [authorized, setAuthorized] = useState(false);
  const [checking, setChecking] = useState(true);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        await api.adminListTickets();
        setAuthorized(true);
      } catch {
        setAuthorized(false);
      } finally {
        setChecking(false);
      }
    })();
  }, []);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.adminGameLogin(username, password);
      setAuthorized(true);
      setPassword("");
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  }

  if (checking) return <p className="muted">Проверка доступа…</p>;

  if (!authorized) {
    return (
      <div className="game-login">
        <section className="card">
          <h2>Вход администратора</h2>
          <p className="muted">Очередь оператора и управление игрой.</p>
          <form onSubmit={handleLogin} className="form mt">
            <label className="field">
              <span>Логин</span>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
              />
            </label>
            <label className="field">
              <span>Пароль</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </label>
            <button className="btn btn--primary" disabled={busy}>
              {busy ? "Вход…" : "Войти"}
            </button>
          </form>
          {error && <div className="alert mt">{error}</div>}
        </section>
      </div>
    );
  }

  return <AdminPage />;
}
