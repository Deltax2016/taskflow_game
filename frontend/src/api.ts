// Тонкий клиент к бэкенду. Все запросы идут на относительный /api,
// а проксирование настроено в vite.config.ts (dev) и nginx.conf (prod).

import type {
  Attachment,
  HackEvent,
  LeaderboardRow,
  Player,
  Ticket,
  TicketSummary,
  TicketStatus,
} from "./types";

// Сессия игрока/админа живёт в httpOnly-cookie, поэтому запросы обязаны
// идти с credentials — иначе браузер не приложит cookie и бэкенд ответит 401.
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    throw new Error(await readError(resp));
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

// Бэкенд отдаёт ошибки как {"detail": "..."} — показываем текст, а не JSON.
async function readError(resp: Response): Promise<string> {
  try {
    const body = await resp.json();
    if (body && typeof body.detail === "string") return body.detail;
  } catch {
    /* тело не JSON — падаем на общий текст ниже */
  }
  return `Ошибка ${resp.status}`;
}

// --- Публичный API (страница «Задать вопрос») ---

export const api = {
  createTicket(subject: string, question: string): Promise<Ticket> {
    return request<Ticket>("/api/tickets", {
      method: "POST",
      body: JSON.stringify({ subject, question }),
    });
  },

  getTicket(id: number): Promise<Ticket> {
    return request<Ticket>(`/api/tickets/${id}`);
  },

  addFollowUp(id: number, question: string): Promise<Ticket> {
    return request<Ticket>(`/api/tickets/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ question }),
    });
  },

  // --- Админский API (страница оператора) ---

  adminListTickets(status?: TicketStatus): Promise<TicketSummary[]> {
    const query = status ? `?status=${status}` : "";
    return request<TicketSummary[]>(`/api/admin/tickets${query}`);
  },

  adminGetTicket(id: number): Promise<Ticket> {
    return request<Ticket>(`/api/admin/tickets/${id}`);
  },

  adminReply(id: number, content: string): Promise<Ticket> {
    return request<Ticket>(`/api/admin/tickets/${id}/reply`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
  },

  adminClose(id: number): Promise<Ticket> {
    return request<Ticket>(`/api/admin/tickets/${id}/close`, { method: "POST" });
  },

  // Human-in-the-Loop (AGENT_TYPE=langgraph): решение оператора по черновику,
  // который агент подготовил, но не рискнул отправить сам.
  adminResolveDraft(id: number, approve: boolean, editedAnswer?: string): Promise<Ticket> {
    return request<Ticket>(`/api/admin/tickets/${id}/resolve-draft`, {
      method: "POST",
      body: JSON.stringify({ approve, edited_answer: editedAnswer || null }),
    });
  },

  // Human-in-the-Loop (AGENT_TYPE=tooluse): решение оператора по вызову
  // критического инструмента (напр. create_refund выше лимита) — до того,
  // как он реально выполнится. Аргументы вызова здесь не редактируются.
  adminResolveToolCall(id: number, approve: boolean): Promise<Ticket> {
    return request<Ticket>(`/api/admin/tickets/${id}/resolve-tool-call`, {
      method: "POST",
      body: JSON.stringify({ approve }),
    });
  },

  // --- Игровой режим ---

  gameLogin(login: string): Promise<Player> {
    return request<Player>("/api/game/login", {
      method: "POST",
      body: JSON.stringify({ login }),
    });
  },

  gameLogout(): Promise<void> {
    return request<void>("/api/game/logout", { method: "POST" });
  },

  gameMe(): Promise<Player> {
    return request<Player>("/api/game/me");
  },

  gameLeaderboard(): Promise<LeaderboardRow[]> {
    return request<LeaderboardRow[]>("/api/game/leaderboard");
  },

  gameMyTickets(): Promise<TicketSummary[]> {
    return request<TicketSummary[]>("/api/game/tickets");
  },

  gameGetTicket(id: number): Promise<Ticket> {
    return request<Ticket>(`/api/game/tickets/${id}`);
  },

  // Двухшаговое создание: сначала черновик (агент ещё не читал вопрос),
  // затем загрузка файлов, затем запуск. Иначе инструмент read_attached_file
  // не найдёт вложений — агент отработает раньше, чем файл будет приложен.
  gameCreateDraft(subject: string, question: string): Promise<Ticket> {
    return request<Ticket>("/api/game/tickets/draft", {
      method: "POST",
      body: JSON.stringify({ subject, question }),
    });
  },

  gameRunAgent(id: number): Promise<Ticket> {
    return request<Ticket>(`/api/game/tickets/${id}/run`, { method: "POST" });
  },

  gameFollowUp(id: number, question: string): Promise<Ticket> {
    return request<Ticket>(`/api/game/tickets/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ question }),
    });
  },

  // Content-Type не ставим руками: для multipart браузер сам добавит
  // boundary, а заданный вручную заголовок его затрёт и сломает разбор.
  async gameUpload(ticketId: number, file: File): Promise<Attachment> {
    const form = new FormData();
    form.append("file", file);
    const resp = await fetch(`/api/game/tickets/${ticketId}/attachments`, {
      method: "POST",
      credentials: "same-origin",
      body: form,
    });
    if (!resp.ok) throw new Error(await readError(resp));
    return resp.json() as Promise<Attachment>;
  },

  adminGameLogin(username: string, password: string): Promise<void> {
    return request<void>("/api/game/admin/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
  },

  adminGameLogout(): Promise<void> {
    return request<void>("/api/game/admin/logout", { method: "POST" });
  },

  adminHackEvents(): Promise<HackEvent[]> {
    return request<HackEvent[]>("/api/admin/game/hack-events");
  },

  adminResetGame(): Promise<{ reset_players: number }> {
    return request<{ reset_players: number }>("/api/admin/game/reset", { method: "POST" });
  },
};
