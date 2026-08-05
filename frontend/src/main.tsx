import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";

import App from "./App";
import AskPage from "./pages/AskPage";
import AdminGate from "./pages/AdminGate";
import GamePage from "./pages/GamePage";
import "./styles.css";

// В игровом режиме (VITE_GAME_MODE=true) корень — это игра «взломай агента»,
// а обычная страница поддержки из занятий 1-3 остаётся доступной на /support.
// Без игрового режима всё как раньше: корень — страница вопросов.
const gameMode = import.meta.env.VITE_GAME_MODE === "true";

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: gameMode ? <GamePage /> : <AskPage /> },
      { path: "support", element: <AskPage /> },
      { path: "admin", element: <AdminGate /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
