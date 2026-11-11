// Rotas da interface. Os endereços são os mesmos da interface antiga (/movies, /tv/3,
// /settings/library/radarr), então links guardados continuam valendo.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom'
import { Movies, Shows } from './pages/Library'
import { Overview } from './pages/Overview'
import { Queue } from './pages/Queue'
import { SettingsPage } from './pages/settings/SettingsPage'
import { Show } from './pages/Show'
import { System } from './pages/System'
import { More, NotFound, Shell } from './shell/Shell'
import { FeedbackProvider } from './ui/feedback'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 1000, retry: 1 } },
})

const router = createBrowserRouter([
  {
    element: <Shell />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: 'dashboard', element: <Overview /> },
      { path: 'movies', element: <Movies /> },
      { path: 'tv', element: <Shows /> },
      { path: 'tv/:id', element: <Show /> },
      { path: 'queue', element: <Queue /> },
      { path: 'settings', element: <Navigate to="/settings/library" replace /> },
      { path: 'settings/library/:tab', element: <SettingsPage /> },
      { path: 'settings/:page', element: <SettingsPage /> },
      { path: 'system', element: <Navigate to="/system/tasks" replace /> },
      { path: 'system/:page', element: <System /> },
      { path: 'more', element: <More /> },
      { path: '*', element: <NotFound /> },
    ],
  },
])

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <FeedbackProvider>
        <RouterProvider router={router} />
      </FeedbackProvider>
    </QueryClientProvider>
  )
}
