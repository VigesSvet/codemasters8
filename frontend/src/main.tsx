import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import 'maplibre-gl/dist/maplibre-gl.css'
import './styles/go.css'
import './styles/signal.css'
import './styles/audit.css'
import './styles/ai.css'
import './styles/provenance.css'
import './styles/sources.css'
import './styles/viewer.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
