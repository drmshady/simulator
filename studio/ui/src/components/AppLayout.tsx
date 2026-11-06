import { Alert, AppShell, Burger, Button, Center, NavLink, Stack } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { Link, Navigate, Outlet, useLocation } from 'react-router';

import { getSession, loginPath, SESSION_QUERY_KEY } from '../api/auth';
import { isStudioError } from '../api/client';
import { useStudioEventStream } from '../api/events';
import { PAGES } from '../navigation';
import { FullPageLoader } from './FullPageLoader';
import { SystemBar } from './SystemBar';
import { TeachingNotice } from './TeachingNotice';

function SignedInShell() {
  const [navOpened, { toggle, close }] = useDisclosure(false);
  const { pathname } = useLocation();
  const connection = useStudioEventStream(true);

  return (
    <AppShell
      header={{ height: 60 }}
      footer={{ height: { base: 72, sm: 36 } }}
      navbar={{ width: 200, breakpoint: 'sm', collapsed: { mobile: !navOpened } }}
      padding="md"
    >
      <AppShell.Header>
        <SystemBar
          connection={connection}
          burger={
            <Burger opened={navOpened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Show or hide navigation" />
          }
        />
      </AppShell.Header>

      <AppShell.Navbar p="xs" component="nav" aria-label="Pages">
        {PAGES.map((page) => {
          const to = `/${page.path}`;
          return (
            <NavLink
              key={page.path}
              component={Link}
              to={to}
              label={page.label}
              active={pathname === to || pathname.startsWith(`${to}/`)}
              onClick={close}
            />
          );
        })}
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>

      <AppShell.Footer px="md" py={8}>
        <TeachingNotice variant="compact" />
      </AppShell.Footer>
    </AppShell>
  );
}

/** Route element for every signed-in page: checks the session, then shows the shell around the page. */
export function AppLayout() {
  const location = useLocation();
  const session = useQuery({
    queryKey: SESSION_QUERY_KEY,
    queryFn: ({ signal }) => getSession(signal),
    staleTime: 60_000,
  });

  // A failed refetch keeps the last good answer, so a short network blip does not throw the owner out.
  if (session.data) return <SignedInShell />;
  if (session.isPending) return <FullPageLoader label="Checking sign-in…" />;
  if (isStudioError(session.error) && session.error.status === 401) {
    return <Navigate to={loginPath(location.pathname + location.search + location.hash)} replace />;
  }
  return (
    <Center mih="100vh" p="md">
      <Stack maw={520}>
        <Alert color="red" title="Cannot open the studio" role="alert">
          {session.error.message}
        </Alert>
        <Button onClick={() => void session.refetch()} loading={session.isFetching}>
          Try again
        </Button>
      </Stack>
    </Center>
  );
}
