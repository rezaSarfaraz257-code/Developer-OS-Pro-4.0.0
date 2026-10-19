import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './ide/IDEStudio2026.css'
import './ProductPolish2026.css'
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import './ScrollbarBehavior.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
