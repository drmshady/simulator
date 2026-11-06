import { Alert, Button, Center, Paper, PasswordInput, Stack, Text, Title } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router';

import { login, loginErrorMessage, safeNextPath } from '../api/auth';

export default function Login() {
  const [password, setPassword] = useState('');
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const signIn = useMutation({
    mutationFn: login,
    onSuccess: async () => {
      // Drop answers cached while signed out (such as the 401 from the session check) before going back.
      queryClient.removeQueries();
      await navigate(safeNextPath(searchParams.get('next')), { replace: true });
    },
    onError: () => setPassword(''),
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password && !signIn.isPending) signIn.mutate(password);
  }

  return (
    <Center mih="100vh" p="md">
      <Paper withBorder shadow="sm" radius="md" p="xl" w="100%" maw={380}>
        <form onSubmit={submit} noValidate>
          <Stack>
            <div>
              <Title order={1} size="h2">
                Simulation Studio
              </Title>
              <Text size="sm" c="dimmed">
                Sign in with the owner password.
              </Text>
            </div>
            <PasswordInput
              label="Password"
              name="password"
              autoComplete="current-password"
              autoFocus
              required
              value={password}
              onChange={(event) => setPassword(event.currentTarget.value)}
            />
            {signIn.isError && (
              <Alert color="red" role="alert" title="Not signed in">
                {loginErrorMessage(signIn.error)}
              </Alert>
            )}
            <Button type="submit" loading={signIn.isPending} disabled={!password}>
              Sign in
            </Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
