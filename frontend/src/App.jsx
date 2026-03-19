import React from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import HomePage from "./pages/HomePage";
import IdeasPage from "./pages/ideas";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/ideas" element={<IdeasPage />} />
      </Routes>
    </BrowserRouter>
  );
}
