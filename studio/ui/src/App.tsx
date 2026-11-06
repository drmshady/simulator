import { createBrowserRouter, Navigate } from 'react-router';
import { RouterProvider } from 'react-router/dom';

import { HOME_PATH, LOGIN_ROUTE, loginPath } from './api/auth';
import { setOnUnauthorised } from './api/client';
import { AppLayout } from './components/AppLayout';
import { FullPageLoader } from './components/FullPageLoader';
import { RouteError } from './components/RouteError';
import { PAGES } from './navigation';
import Login from './pages/Login';

const router = createBrowserRouter([
  { path: LOGIN_ROUTE, Component: Login, errorElement: <RouteError /> },
  {
    path: '/',
    Component: AppLayout,
    errorElement: <RouteError />,
    hydrateFallbackElement: <FullPageLoader />,
    children: [
      { index: true, element: <Navigate to={HOME_PATH} replace /> },
      ...PAGES.map((page) => ({
        path: page.path,
        lazy: { Component: async () => (await page.load()).default },
      })),
      { path: '*', element: <Navigate to={HOME_PATH} replace /> },
    ],
  },
]);

// Any 401 from the API (an expired session) sends the owner to sign in, then back to the same page.
setOnUnauthorised(() => {
  const { pathname, search, hash } = router.state.location;
  if (pathname === LOGIN_ROUTE) return;
  void router.navigate(loginPath(pathname + search + hash), { replace: true });
});

export default function App() {
  return <RouterProvider router={router} />;
}
