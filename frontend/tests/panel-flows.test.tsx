import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import PanelLayout from "@/app/(panel)/layout";
import AgentPage from "@/app/(panel)/agente/page";
import UsersPage from "@/app/(panel)/usuarios/page";
import { HomeContext } from "@/components/home-context";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
const home = { id: "home-a", name: "Casa", timezone: "UTC", role: "owner", permissions: ["agent.view", "agent.edit", "users.read", "users.manage"] };
let agent = {
  name: "Portero",
  system_prompt: "Hola",
  language: "es-AR",
  voice: "default",
  voice_speed: 1,
  realtime_model: "env-default",
  openai_session_options: {},
  enabled: false,
  updated_at: null as string | null,
};
let writes: unknown[];
beforeEach(() => {
  writes = [];
  agent = { ...agent, name: "Portero", openai_session_options: {}, updated_at: null };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/auth/me") return Response.json({ id: "me", username: "Ana", email: "ana@example.com", homes: [home, { ...home, id: "home-b", name: "Oficina", permissions: ["users.read"] }] });
    if (path.endsWith("/agent")) {
      if (init?.method === "PUT") { const body = JSON.parse(String(init.body)); writes.push(body); agent = { ...body, updated_at: "2026-09-05T12:00:00Z" }; }
      return Response.json(agent);
    }
    return Response.json({ items: [{ id: "user1", username: path.includes("home-a") ? "Persona de Casa" : "Persona de Oficina", email: "person@example.com", role: "read_only", is_active: true }] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

it("loads and saves agent via the typed API and preserves configuration after remount", async () => {
  const user = userEvent.setup();
  const view = render(<HomeContext.Provider value={home}><AgentPage /></HomeContext.Provider>);
  const name = await screen.findByLabelText("Nombre del agente");
  await user.clear(name);
  await user.type(name, "Recepción");
  await user.click(screen.getByRole("button", { name: "Guardar cambios" }));
  expect(await screen.findByRole("status")).toHaveTextContent(/guardada/i);
  expect(writes).toEqual([
    {
      name: "Recepción",
      system_prompt: "Hola",
      language: "es-AR",
      voice: "default",
      voice_speed: 1,
      realtime_model: "env-default",
      openai_session_options: {
        audio: {
          input: {
            noise_reduction: null,
            turn_detection: { type: "server_vad", create_response: true, interrupt_response: true },
          },
        },
        max_output_tokens: "inf",
      },
      enabled: false,
    },
  ]);
  view.unmount();
  render(<HomeContext.Provider value={home}><AgentPage /></HomeContext.Provider>);
  expect(await screen.findByLabelText("Nombre del agente")).toHaveValue("Recepción");
});

it("does not fetch agent for a home without viewing permission", () => {
  render(<HomeContext.Provider value={{ ...home, permissions: [] }}><AgentPage /></HomeContext.Provider>);
  expect(screen.getByText(/sin permiso para ver el agente/i)).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
});

it("scopes navigation and actions to the selected home", async () => {
  const user = userEvent.setup();
  render(<PanelLayout><UsersPage /></PanelLayout>);
  expect(await screen.findByText("Persona de Casa")).toBeVisible();
  expect(screen.getByRole("link", { name: "Agente" })).toHaveAttribute("href", "/agente");
  expect(screen.getByRole("link", { name: "Usuarios" })).toHaveAttribute("href", "/usuarios");
  expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/dashboard");
  await user.selectOptions(screen.getByLabelText("Vivienda activa"), "home-b");
  expect(await screen.findByText("Persona de Oficina")).toBeVisible();
  expect(screen.queryByText("Persona de Casa")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Agente" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Crear usuario" })).not.toBeInTheDocument();
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/homes/home-b/users", expect.anything()));
});
