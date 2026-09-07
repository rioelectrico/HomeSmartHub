import { apiFetch } from "@/lib/api";
import type { LoginValues } from "@/lib/schemas/auth";
import type { CurrentUser } from "@/types/api";

export async function login(credentials: LoginValues): Promise<CurrentUser> {
  await apiFetch<void>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(credentials),
  });
  return getCurrentUser();
}

export function getCurrentUser(): Promise<CurrentUser> {
  return apiFetch<CurrentUser>("/api/auth/me");
}

export async function logout(): Promise<void> {
  await apiFetch<void>("/api/auth/logout", { method: "POST" });
}
