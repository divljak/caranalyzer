import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import CarFlippingAnalyzer from './pages/CarFlippingAnalyzer'
import './index.css'

const queryClient = new QueryClient()

const rootElement = document.getElementById('root')
if (rootElement) {
  createRoot(rootElement).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/analyzer" element={<CarFlippingAnalyzer />} />
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>
  )
}
