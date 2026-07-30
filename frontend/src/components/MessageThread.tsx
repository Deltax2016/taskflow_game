import type { Message, MessageRole } from "../types";

const ROLE_LABELS: Record<MessageRole, string> = {
  user: "Пользователь",
  agent: "ИИ-агент",
  human: "Оператор",
  system: "Система",
};

// Лента сообщений тикета. Для ответа агента дополнительно показываем
// его уверенность и источники из базы знаний (лежат в meta).
export default function MessageThread({ messages }: { messages: Message[] }) {
  return (
    <div className="thread">
      {messages.map((msg) => {
        const confidence = msg.meta?.confidence as number | undefined;
        const sources = msg.meta?.sources as string[] | undefined;
        // Поля ниже кладёт только LangGraph-агент (AGENT_TYPE=langgraph) —
        // для simple/full их просто нет в meta, ничего не сломается.
        const modelTier = msg.meta?.model_tier as string | undefined;
        const stepsUsed = msg.meta?.steps_used as number | undefined;
        return (
          <div key={msg.id} className={`message message--${msg.role}`}>
            <div className="message__head">
              <span className="message__role">{ROLE_LABELS[msg.role]}</span>
              <span className="message__meta">
                {typeof confidence === "number" && (
                  <span className="message__confidence">
                    уверенность {(confidence * 100).toFixed(0)}%
                  </span>
                )}
                {modelTier && <span className="message__tier">тир: {modelTier}</span>}
                {typeof stepsUsed === "number" && (
                  <span className="message__steps">шагов: {stepsUsed}</span>
                )}
              </span>
            </div>
            <div className="message__body">{msg.content}</div>
            {sources && sources.length > 0 && (
              <div className="message__sources">Источники: {sources.join(", ")}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}
