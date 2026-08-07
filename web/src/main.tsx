import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import { SessionProvider, ToastProvider } from './lib/store'
import './styles.css'

const container = document.getElementById('root')
if (!container) throw new Error('Root element missing from index.html')

createRoot(container).render(
  <StrictMode>
    <ToastProvider>
      <SessionProvider>
        <App />
      </SessionProvider>
    </ToastProvider>
  </StrictMode>,
)
