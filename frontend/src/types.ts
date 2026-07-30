// Типы зеркалят контракт бэкенда (app/schemas.py). Держим их в одном месте.

export type TicketStatus =
  | "open"
  | "assigned_to_agent"
  | "answered_by_agent"
  | "pending_human"
  | "answered_by_human"
  | "closed";

export type MessageRole = "user" | "agent" | "human" | "system";

export interface Message {
  id: number;
  role: MessageRole;
  content: string;
  meta: Record<string, unknown> | null;
  created_at: string;
}

export interface Ticket {
  id: number;
  subject: string;
  status: TicketStatus;
  created_at: string;
  updated_at: string;
  messages: Message[];
}

export interface TicketSummary {
  id: number;
  subject: string;
  status: TicketStatus;
  created_at: string;
  updated_at: string;
}

// Человекочитаемые подписи статусов для интерфейса.
export const STATUS_LABELS: Record<TicketStatus, string> = {
  open: "Новый",
  assigned_to_agent: "У агента",
  answered_by_agent: "Ответил агент",
  pending_human: "Ждёт оператора",
  answered_by_human: "Ответил оператор",
  closed: "Закрыт",
};
