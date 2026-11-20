import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './styles.css'
import './components/ui.css'
import './components/phase2.css'
import './components/phase3.css'
import './components/phase4.css'
import './components/dataset-detail-accordion.css'
import './typography.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter><App /></BrowserRouter>
  </React.StrictMode>,
)
