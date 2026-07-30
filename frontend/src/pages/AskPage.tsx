import { useEffect, useState } from "react";

import { api } from "../api";
import MessageThread from "../components/MessageThread";
import StatusBadge from "../components/StatusBadge";
import type { Ticket } from "../types";

const STORAGE_KEY = "my_ticket_ids";

// Публичная страница: пользователь задаёт вопрос и видит ответ агента/оператора.
// Список своих обращений храним в localStorage (без авторизации — для демо).
export default function AskPage() {
  const [myIds, setMyIds] = useState<number[]>(() => loadIds());
  const [selected, setSelected] = useState<Ticket | null>(null);
  const [subject, setSubject] = useState("");
  const [question, setQuestion] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    saveIds(myIds);
  }, [myIds]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const ticket = await api.createTicket(subject, question);
      setMyIds((ids) => [ticket.id, ...ids.filter((id) => id !== ticket.id)]);
      setSelected(ticket);
      setSubject("");
      setQuestion("");
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function openTicket(id: number) {
    setError("");
    try {
      setSelected(await api.getTicket(id));
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleFollowUp(e: React.FormEvent) {
    e.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const ticket = await api.addFollowUp(selected.id, followUp);
      setSelected(ticket);
      setFollowUp("");
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="layout">
      {/* Левая колонка: новая заявка + мои обращения */}
      <section className="card">
        <h2>Задать вопрос</h2>
        <form onSubmit={handleCreate} className="form">
          <label className="field">
            <span>Тема</span>
            <input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Например: не приходят уведомления"
              minLength={3}
              required
            />
          </label>
          <label className="field">
            <span>Вопрос</span>
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Опишите вопрос подробнее…"
              rows={4}
              required
            />
          </label>
          <button className="btn btn--primary" disabled={busy}>
            {busy ? "Отправка…" : "Отправить"}
          </button>
        </form>

        {myIds.length > 0 && (
          <>
            <h3 className="mt">Мои обращения</h3>
            <ul className="list">
              {myIds.map((id) => (
                <li key={id}>
                  <button className="list__item" onClick={() => openTicket(id)}>
                    #{id}
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      {/* Правая колонка: выбранный тикет и диалог */}
      <section className="card">
        {error && <div className="alert">{error}</div>}
        {!selected && <p className="muted">Задайте вопрос или откройте обращение слева.</p>}
        {selected && (
          <>
            <div className="ticket-head">
              <h2>
                #{selected.id} · {selected.subject}
              </h2>
              <StatusBadge status={selected.status} />
            </div>
            <MessageThread messages={selected.messages} />

            {selected.status !== "closed" && (
              <form onSubmit={handleFollowUp} className="form mt">
                <textarea
                  value={followUp}
                  onChange={(e) => setFollowUp(e.target.value)}
                  placeholder="Уточнить или задать новый вопрос…"
                  rows={2}
                  required
                />
                <button className="btn" disabled={busy}>
                  {busy ? "Отправка…" : "Отправить уточнение"}
                </button>
              </form>
            )}
          </>
        )}
      </section>
    </div>
  );
}

function loadIds(): number[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function saveIds(ids: number[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
}
