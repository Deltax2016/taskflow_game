import { STATUS_LABELS, type TicketStatus } from "../types";

// Цветной бейдж статуса тикета. Классы описаны в styles.css.
export default function StatusBadge({ status }: { status: TicketStatus }) {
  return <span className={`badge badge--${status}`}>{STATUS_LABELS[status]}</span>;
}
