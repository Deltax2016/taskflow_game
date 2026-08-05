import { NavLink, Outlet } from "react-router-dom";

const gameMode = import.meta.env.VITE_GAME_MODE === "true";

// Каркас приложения: шапка с навигацией + область страницы.
export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header__brand">
          <span className="app-header__logo">◆</span>{" "}
          {gameMode ? "TaskFlow · Взломай агента" : "TaskFlow · Поддержка"}
        </div>
        <nav className="app-header__nav">
          <NavLink to="/" end className="nav-link">
            {gameMode ? "Игра" : "Задать вопрос"}
          </NavLink>
          <NavLink to="/admin" className="nav-link">
            Админка оператора
          </NavLink>
        </nav>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
