// Тонкий клиент к бэкенду. Все запросы идут на относительный /api,
// а проксирование настроено в vite.config.ts (dev) и nginx.conf (prod).

import type { Ticket, TicketSummary, TicketStatus } from "./types";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`${resp.status}: ${detail}`);
  }
  return resp.json() as Promise<T>;
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
};
