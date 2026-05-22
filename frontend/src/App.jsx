import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AuthProvider } from './hooks/useAuth.jsx';
import NavBar from './components/NavBar';
import HomePage from './pages/HomePage';

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <NavBar />
        <Routes>
          <Route path="/" element={<HomePage />} />
          {/* 추후 페이지 추가 예시:
          <Route path="/projects" element={<MyProjectsPage />} />
          <Route path="/repo/:repoId/overview" element={<OverviewPage />} />
          */}
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
