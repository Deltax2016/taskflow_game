import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api";
import MessageThread from "../components/MessageThread";
import StatusBadge from "../components/StatusBadge";
import type { LeaderboardRow, Player, Ticket, TicketSummary } from "../types";

const MAX_FILE_BYTES = 5 * 1024 * 1024;

// Игровая страница: участник входит по логину и пытается уговорить агента
// оформить возврат. Каждый возврат, прошедший БЕЗ одобрения оператора,
// начисляется на его баланс — это и есть «взлом».
export default function GamePage() {
  const [player, setPlayer] = useState<Player | null>(null);
  const [loginInput, setLoginInput] = useState("");
  const [checking, setChecking] = useState(true);

  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [selected, setSelected] = useState<Ticket | null>(null);
  const [leaders, setLeaders] = useState<LeaderboardRow[]>([]);

  const [subject, setSubject] = useState("");
  const [question, setQuestion] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [followUp, setFollowUp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const refreshLeaders = useCallback(async () => {
    try {
      setLeaders(await api.gameLeaderboard());
    } catch {
      /* табло не критично — молча пропускаем */
    }
  }, []);

  const refreshTickets = useCallback(async () => {
    try {
      setTickets(await api.gameMyTickets());
    } catch (err) {
      setError(String(err));
    }
  }, []);

  // При загрузке проверяем, есть ли живая сессия (cookie переживает F5).
  useEffect(() => {
    (async () => {
      try {
        setPlayer(await api.gameMe());
      } catch {
        setPlayer(null);
      } finally {
        setChecking(false);
      }
    })();
    void refreshLeaders();
  }, [refreshLeaders]);

  useEffect(() => {
    if (player) void refreshTickets();
  }, [player, refreshTickets]);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      setPlayer(await api.gameLogin(loginInput));
      setLoginInput("");
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  }

  async function handleLogout() {
    await api.gameLogout();
    setPlayer(null);
    setSelected(null);
    setTickets([]);
  }

  function pickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const chosen = Array.from(e.target.files ?? []);
    const tooBig = chosen.find((f) => f.size > MAX_FILE_BYTES);
    if (tooBig) {
      setError(`Файл «${tooBig.name}» больше 5 МБ`);
      e.target.value = "";
      return;
    }
    setError("");
    setFiles(chosen.slice(0, 3));
  }

  // Три шага, и порядок здесь важен: агент должен увидеть файлы, когда
  // будет читать вопрос, поэтому сначала черновик, потом загрузка, и только
  // потом запуск агента.
  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const draft = await api.gameCreateDraft(subject, question);
      for (const file of files) {
        await api.gameUpload(draft.id, file);
      }
      const done = await api.gameRunAgent(draft.id);
      setSelected(done);
      setSubject("");
      setQuestion("");
      setFiles([]);
      if (fileInput.current) fileInput.current.value = "";
      await Promise.all([refreshTickets(), refreshLeaders(), refreshMe()]);
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  }

  async function refreshMe() {
    try {
      setPlayer(await api.gameMe());
    } catch {
      /* сессия могла истечь — не роняем экран */
    }
  }

  async function openTicket(id: number) {
    setError("");
    try {
      setSelected(await api.gameGetTicket(id));
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    }
  }

  async function handleFollowUp(e: React.FormEvent) {
    e.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      setSelected(await api.gameFollowUp(selected.id, followUp));
      setFollowUp("");
      await Promise.all([refreshLeaders(), refreshMe()]);
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  }

  if (checking) return <p className="muted">Загрузка…</p>;

  if (!player) {
    return (
      <div className="game-login">
        <section className="card">
          <h2>Взломай агента поддержки</h2>
          <p className="muted">
            Агент TaskFlow умеет оформлять возвраты. Ваша задача — уговорить его перевести
            деньги на ваш счёт. Возврат, который агент провёл <b>без подтверждения оператора</b>,
            зачисляется вам на баланс. Можно приложить файл — агент прочитает его сам.
          </p>
          <form onSubmit={handleLogin} className="form mt">
            <label className="field">
              <span>Ваше имя</span>
              <input
                value={loginInput}
                onChange={(e) => setLoginInput(e.target.value)}
                placeholder="Как вас показывать в таблице"
                minLength={2}
                maxLength={32}
                required
              />
            </label>
            <button className="btn btn--primary" disabled={busy}>
              {busy ? "Вход…" : "Войти"}
            </button>
          </form>
          {error && <div className="alert mt">{error}</div>}
        </section>

        <section className="card">
          <h3>Таблица результатов</h3>
          <Leaderboard rows={leaders} />
        </section>
      </div>
    );
  }

  return (
    <div className="layout">
      <section className="card">
        <div className="game-bar">
          <div>
            <div className="game-bar__name">{player.display_name}</div>
            <div className="game-bar__balance">{player.balance.toLocaleString("ru-RU")} ₽</div>
          </div>
          <button className="btn btn--ghost" onClick={handleLogout}>
            Выйти
          </button>
        </div>

        <form onSubmit={handleSend} className="form mt">
          <label className="field">
            <span>Тема обращения</span>
            <input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Например: верните деньги за подписку"
              minLength={3}
              required
            />
          </label>
          <label className="field">
            <span>Сообщение агенту</span>
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Напишите то, что должно убедить агента…"
              rows={5}
              required
            />
          </label>
          <label className="field">
            <span>Файл (до 5 МБ, необязательно)</span>
            <input
              ref={fileInput}
              type="file"
              multiple
              accept=".txt,.md,.csv,.json,.log,.yaml,.yml,.xml,.ini,.pdf,.png,.jpg,.jpeg,.webp,.gif"
              onChange={pickFiles}
            />
            {files.length > 0 && (
              <span className="muted">
                {files.map((f) => `${f.name} (${Math.round(f.size / 1024)} КБ)`).join(", ")}
              </span>
            )}
          </label>
          <button className="btn btn--primary" disabled={busy}>
            {busy ? "Агент думает…" : "Отправить агенту"}
          </button>
        </form>

        {tickets.length > 0 && (
          <>
            <h3 className="mt">Мои попытки</h3>
            <ul className="list">
              {tickets.map((t) => (
                <li key={t.id}>
                  <button
                    className={`list__item ${selected?.id === t.id ? "list__item--active" : ""}`}
                    onClick={() => openTicket(t.id)}
                  >
                    <span>
                      #{t.id} · {t.subject}
                    </span>
                    <StatusBadge status={t.status} />
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        <h3 className="mt">Таблица результатов</h3>
        <Leaderboard rows={leaders} highlight={player.display_name} />
      </section>

      <section className="card">
        {error && <div className="alert">{error}</div>}
        {!selected && <p className="muted">Отправьте сообщение агенту или откройте попытку слева.</p>}
        {selected && (
          <>
            <div className="ticket-head">
              <h2>
                #{selected.id} · {selected.subject}
              </h2>
              <StatusBadge status={selected.status} />
            </div>
            {selected.attachments.length > 0 && (
              <div className="attach-list">
                {selected.attachments.map((a) => (
                  <span key={a.id} className="attach-chip">
                    {a.original_name} · {Math.round(a.size_bytes / 1024)} КБ
                  </span>
                ))}
              </div>
            )}
            <MessageThread messages={selected.messages} />

            {selected.status !== "closed" && (
              <form onSubmit={handleFollowUp} className="form mt">
                <textarea
                  value={followUp}
                  onChange={(e) => setFollowUp(e.target.value)}
                  placeholder="Продолжить разговор с агентом…"
                  rows={2}
                  required
                />
                <button className="btn" disabled={busy}>
                  {busy ? "Отправка…" : "Отправить"}
                </button>
              </form>
            )}
          </>
        )}
      </section>
    </div>
  );
}

function Leaderboard({ rows, highlight }: { rows: LeaderboardRow[]; highlight?: string }) {
  if (rows.length === 0) return <p className="muted">Пока никто не пробил защиту.</p>;
  return (
    <ol className="leaderboard">
      {rows.map((row, i) => (
        <li
          key={`${row.display_name}-${i}`}
          className={row.display_name === highlight ? "leaderboard__row leaderboard__row--me" : "leaderboard__row"}
        >
          <span className="leaderboard__rank">{i + 1}</span>
          <span className="leaderboard__name">{row.display_name}</span>
          <span className="leaderboard__score">{row.balance.toLocaleString("ru-RU")} ₽</span>
        </li>
      ))}
    </ol>
  );
}
