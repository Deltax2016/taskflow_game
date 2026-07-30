import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import MessageThread from "../components/MessageThread";
import StatusBadge from "../components/StatusBadge";
import type { Ticket, TicketStatus, TicketSummary } from "../types";

type Filter = "queue" | "all";

// Human-in-the-Loop (AGENT_TYPE=langgraph): агент оставил черновик, но не
// рискнул отправить его сам — ждёт одобрения оператора. Признак — последнее
// сообщение тикета системное и несёт meta.requires_approval.
function pendingDraft(ticket: Ticket | null): { draft_answer?: string; confidence?: number } | null {
  if (!ticket || ticket.status !== "pending_human" || ticket.messages.length === 0) return null;
  const last = ticket.messages[ticket.messages.length - 1];
  if (last.role !== "system" || !last.meta?.requires_approval) return null;
  return last.meta as { draft_answer?: string; confidence?: number };
}

// Админка оператора: очередь тикетов, переданных человеку, и ответы на них.
export default function AdminPage() {
  const [filter, setFilter] = useState<Filter>("queue");
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [selected, setSelected] = useState<Ticket | null>(null);
  const [reply, setReply] = useState("");
  const [draftText, setDraftText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const draft = pendingDraft(selected);

  const refresh = useCallback(async () => {
    setError("");
    try {
      // «Очередь» = только тикеты, ожидающие оператора.
      const status: TicketStatus | undefined = filter === "queue" ? "pending_human" : undefined;
      setTickets(await api.adminListTickets(status));
    } catch (err) {
      setError(String(err));
    }
  }, [filter]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function openTicket(id: number) {
    setError("");
    try {
      const ticket = await api.adminGetTicket(id);
      setSelected(ticket);
      setDraftText(pendingDraft(ticket)?.draft_answer ?? "");
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleReply(e: React.FormEvent) {
    e.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const ticket = await api.adminReply(selected.id, reply);
      setSelected(ticket);
      setReply("");
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleClose() {
    if (!selected) return;
    setBusy(true);
    try {
      const ticket = await api.adminClose(selected.id);
      setSelected(ticket);
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  // Human-in-the-Loop: одобрить черновик (можно с правкой текста) или
  // отклонить его — в обоих случаях резюмирует граф LangGraph, остановленный
  // на human_gate (см. TicketService.resume_agent_draft).
  async function handleResolveDraft(approve: boolean) {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const edited = approve && draftText !== draft?.draft_answer ? draftText : undefined;
      const ticket = await api.adminResolveDraft(selected.id, approve, edited);
      setSelected(ticket);
      setDraftText(pendingDraft(ticket)?.draft_answer ?? "");
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="layout">
      <section className="card">
        <div className="tabs">
          <button
            className={`tab ${filter === "queue" ? "tab--active" : ""}`}
            onClick={() => setFilter("queue")}
          >
            Очередь
          </button>
          <button
            className={`tab ${filter === "all" ? "tab--active" : ""}`}
            onClick={() => setFilter("all")}
          >
            Все тикеты
          </button>
          <button className="btn btn--ghost" onClick={() => void refresh()}>
            ⟳
          </button>
        </div>

        {tickets.length === 0 && <p className="muted">Тикетов нет.</p>}
        <ul className="list">
          {tickets.map((t) => (
            <li key={t.id}>
              <button className="list__item list__item--wide" onClick={() => openTicket(t.id)}>
                <span>
                  #{t.id} · {t.subject}
                </span>
                <StatusBadge status={t.status} />
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        {error && <div className="alert">{error}</div>}
        {!selected && <p className="muted">Выберите тикет из очереди.</p>}
        {selected && (
          <>
            <div className="ticket-head">
              <h2>
                #{selected.id} · {selected.subject}
              </h2>
              <StatusBadge status={selected.status} />
            </div>
            <MessageThread messages={selected.messages} />

            {draft && (
              <div className="draft-panel mt">
                <div className="draft-panel__head">
                  Черновик агента ждёт решения
                  {typeof draft.confidence === "number" && (
                    <span className="message__confidence">
                      {" "}
                      · уверенность {(draft.confidence * 100).toFixed(0)}%
                    </span>
                  )}
                </div>
                <textarea
                  value={draftText}
                  onChange={(e) => setDraftText(e.target.value)}
                  rows={3}
                />
                <div className="row">
                  <button
                    className="btn btn--primary"
                    disabled={busy}
                    onClick={() => handleResolveDraft(true)}
                  >
                    {busy ? "Отправка…" : "Отправить как ответ агента"}
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost"
                    disabled={busy}
                    onClick={() => handleResolveDraft(false)}
                  >
                    Отклонить, отвечу сам
                  </button>
                </div>
              </div>
            )}

            {selected.status !== "closed" && (
              <form onSubmit={handleReply} className="form mt">
                <textarea
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  placeholder={draft ? "…или ответить вручную вместо черновика" : "Ответ оператора…"}
                  rows={3}
                  required
                />
                <div className="row">
                  <button className="btn btn--primary" disabled={busy}>
                    {busy ? "Отправка…" : "Ответить"}
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost"
                    disabled={busy}
                    onClick={handleClose}
                  >
                    Закрыть тикет
                  </button>
                </div>
              </form>
            )}
          </>
        )}
      </section>
    </div>
  );
}
