import { Alert, Button, Center, Stack } from '@mantine/core';
import { isRouteErrorResponse, useRouteError } from 'react-router';

/** Shown when a page fails to load or render, e.g. a page file from before a rebuild. */
export function RouteError() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : 'Unknown error.';
  return (
    <Center mih="100vh" p="md">
      <Stack maw={520}>
        <Alert color="red" title="This page could not be shown" role="alert">
          {message}
        </Alert>
        <Button onClick={() => window.location.reload()}>Reload the studio</Button>
      </Stack>
    </Center>
  );
}
