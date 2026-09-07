import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import UsersPage from "@/app/(panel)/usuarios/page";
import { HomeContext } from "@/components/home-context";
import { userCreateSchema, passwordResetSchema } from "@/lib/schemas/user";

const home = { id: "home-a", name: "Casa", timezone: "UTC", role: "owner", permissions: ["users.read", "users.manage"] };
const makeUser = (index: number) => ({ id: `user-${index}`, username: `Persona ${index}`, email: `persona${index}@example.com`, role: "read_only", is_active: true });
let items = [makeUser(1)];
let calls: { url: string; method: string; body: Record<string, unknown> }[];
function renderUsers(permissions = home.permissions) { return render(<HomeContext.Provider value={{ ...home, permissions }}><UsersPage /></HomeContext.Provider>); }

beforeEach(() => {
  items = [makeUser(1)]; calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = init?.method ?? "GET", body = init?.body ? JSON.parse(String(init.body)) : {};
    calls.push({ url, method, body });
    if (method === "GET") return Response.json({ items });
    if (url.endsWith("reset-password")) return new Response(null, { status: 204 });
    if (method === "POST") { const created = { id: "new-user", ...body, is_active: true }; items = [...items, created]; return Response.json(created, { status: 201 }); }
    const id = url.split("/").at(-1); items = items.map((item) => item.id === id ? { ...item, ...body } : item);
    return Response.json(items.find((item) => item.id === id));
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("Users", () => {
  it("combines text, role and status filters over the complete list without extra requests", async () => {
    items = [
      { ...makeUser(1), username: "Ana operadora", email: "ana@example.com", role: "operator" },
      { ...makeUser(2), username: "Ana inactiva", role: "operator", is_active: false },
      { ...makeUser(3), username: "Ana lectora" },
      { ...makeUser(4), username: "Beatriz", email: "ana.alternativa@example.com", role: "operator" },
    ];
    const user = userEvent.setup(); renderUsers(["users.read"]);
    await screen.findByText("Ana operadora");
    await user.type(screen.getByLabelText("Buscar usuarios"), "ANA");
    await user.selectOptions(screen.getByLabelText("Filtrar por rol"), "operator");
    await user.selectOptions(screen.getByLabelText("Filtrar por estado"), "active");
    expect(screen.getByText("Ana operadora")).toBeVisible();
    expect(screen.getByText("Beatriz")).toBeVisible();
    expect(screen.queryByText("Ana inactiva")).not.toBeInTheDocument();
    expect(screen.queryByText("Ana lectora")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Filtrar por estado"), "inactive");
    expect(screen.getByText("Ana inactiva")).toBeVisible();
    expect(screen.queryByText("Ana operadora")).not.toBeInTheDocument();
    expect(screen.queryByText("Beatriz")).not.toBeInTheDocument();
    expect(calls).toHaveLength(1);
  });

  it("provides a no-results message and resets all filters to restore users", async () => {
    const user = userEvent.setup(); renderUsers();
    await screen.findByText("Persona 1");
    await user.type(screen.getByLabelText("Buscar usuarios"), "inexistente");
    await user.selectOptions(screen.getByLabelText("Filtrar por rol"), "operator");
    await user.selectOptions(screen.getByLabelText("Filtrar por estado"), "inactive");
    expect(screen.getByText(/no hay usuarios que coincidan/i)).toBeVisible();
    expect(screen.queryByText("Persona 1")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    expect(screen.getByText("Persona 1")).toBeVisible();
    expect(screen.getByLabelText("Buscar usuarios")).toHaveValue("");
    expect(screen.getByLabelText("Filtrar por rol")).toHaveValue("");
    expect(screen.getByLabelText("Filtrar por estado")).toHaveValue("");
    expect(screen.queryByText(/no hay usuarios que coincidan/i)).not.toBeInTheDocument();
  });

  it.each(["text", "role", "status"])("resets pagination when the %s filter changes even if two pages still match", async (filter) => {
    items = Array.from({ length: 22 }, (_, i) => makeUser(i + 1));
    const user = userEvent.setup(); renderUsers();
    await screen.findByText("Persona 1");
    await user.click(screen.getByRole("button", { name: "Siguiente" }));
    expect(screen.getByText("Persona 11")).toBeVisible();
    if (filter === "text") await user.type(screen.getByLabelText("Buscar usuarios"), "persona");
    if (filter === "role") await user.selectOptions(screen.getByLabelText("Filtrar por rol"), "read_only");
    if (filter === "status") await user.selectOptions(screen.getByLabelText("Filtrar por estado"), "active");
    expect(screen.getByText("Persona 1")).toBeVisible();
    expect(screen.queryByText("Persona 11")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Anterior" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Siguiente" })).toBeEnabled();
  });

  it("paginates the complete backend list locally", async () => {
    items = Array.from({ length: 11 }, (_, i) => makeUser(i + 1));
    renderUsers();
    expect(await screen.findByText("Persona 1")).toBeVisible();
    expect(screen.queryByText("Persona 11")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Siguiente" }));
    expect(screen.getByText("Persona 11")).toBeVisible();
    expect(screen.queryByText("Persona 1")).not.toBeInTheDocument();
    expect(calls.filter((call) => call.method === "GET")).toHaveLength(1);
  });

  it("creates a user, refreshes the list, and clears password when reopened", async () => {
    const user = userEvent.setup(); renderUsers();
    await screen.findByText("Persona 1");
    await user.click(screen.getByRole("button", { name: "Crear usuario" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Nombre de usuario"), "Nueva");
    await user.type(within(dialog).getByLabelText("Email"), "NUEVA@example.com");
    await user.type(within(dialog).getByLabelText("Contraseña temporal"), "TemporalPass!42");
    await user.click(within(dialog).getByRole("button", { name: "Crear usuario" }));
    expect(await screen.findByText("Nueva")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.find((call) => call.method === "POST")).toEqual({ url: "/api/homes/home-a/users", method: "POST", body: { username: "Nueva", email: "nueva@example.com", password: "TemporalPass!42", role: "read_only" } });
    await user.click(screen.getByRole("button", { name: "Crear usuario" }));
    expect(screen.getByLabelText("Contraseña temporal")).toHaveValue("");
  });

  it("confirms disable, preserves state on cancel, and reports successful disable", async () => {
    const user = userEvent.setup(); renderUsers(); await screen.findByText("Persona 1");
    await user.click(screen.getByRole("button", { name: "Deshabilitar Persona 1" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(/todos sus hogares/i);
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(calls.every((call) => call.method === "GET")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Deshabilitar Persona 1" }));
    await user.click(screen.getByRole("button", { name: "Confirmar deshabilitación" }));
    expect(await screen.findByText("Inactivo")).toBeVisible();
    expect(calls.find((call) => call.method === "PATCH")?.body).toEqual({ is_active: false });
  });

  it("changes a role through a confirmed dialog and refreshes the displayed role", async () => {
    const user = userEvent.setup(); renderUsers(); await screen.findByText("Persona 1");
    await user.click(screen.getByRole("button", { name: "Cambiar rol de Persona 1" }));
    await user.selectOptions(screen.getByLabelText("Rol"), "administrator");
    await user.click(screen.getByRole("button", { name: "Guardar rol" }));
    expect(await within(screen.getByRole("table")).findByText("Administrador")).toBeVisible();
    expect(calls.find((call) => call.method === "PATCH")?.body).toEqual({ role: "administrator" });
  });

  it("resets password, explains session revocation and drops secret on Escape", async () => {
    const user = userEvent.setup(); renderUsers(); await screen.findByText("Persona 1");
    const trigger = screen.getByRole("button", { name: "Restablecer contraseña de Persona 1" });
    await user.click(trigger);
    expect(screen.getByRole("dialog")).toHaveTextContent(/sesiones/i);
    await user.type(screen.getByLabelText("Contraseña temporal"), "DiscardThis!42");
    await user.keyboard("{Escape}");
    expect(trigger).toHaveFocus();
    await user.click(trigger);
    expect(screen.getByLabelText("Contraseña temporal")).toHaveValue("");
    await user.type(screen.getByLabelText("Contraseña temporal"), "NewPassword!42");
    await user.click(screen.getByRole("button", { name: "Confirmar nueva contraseña" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/contraseña actualizada/i);
    expect(calls.find((call) => call.url.endsWith("reset-password"))?.body).toEqual({ password: "NewPassword!42" });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("shows only readable data without manage permission and blocks unauthorized reads", async () => {
    const view = renderUsers(["users.read"]);
    expect(await screen.findByText("Persona 1")).toBeVisible();
    expect(screen.queryByRole("button", { name: /crear|deshabilitar|cambiar rol|restablecer/i })).not.toBeInTheDocument();
    view.unmount(); calls = [];
    renderUsers([]);
    expect(screen.getByText(/sin permiso/i)).toBeVisible();
    expect(calls).toHaveLength(0);
  });

  it("retains the dialog and announces a backend mutation rejection", async () => {
    const user = userEvent.setup(); renderUsers(); await screen.findByText("Persona 1");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ error: { code: "PERMISSION_DENIED" } }, { status: 403 })));
    await user.click(screen.getByRole("button", { name: "Deshabilitar Persona 1" }));
    await user.click(screen.getByRole("button", { name: "Confirmar deshabilitación" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/permiso/i);
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByRole("button", { name: "Confirmar deshabilitación" })).toBeEnabled();
  });

  it("renders empty and initial-load error states with retry", async () => {
    items = [];
    const view = renderUsers();
    expect(await screen.findByText(/no hay usuarios/i)).toBeVisible();
    view.unmount();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 500 })));
    renderUsers();
    expect(await screen.findByRole("alert")).toHaveTextContent(/no pudimos cargar/i);
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeEnabled();
  });

  it("rejects invalid user/password boundaries and matches backend email normalization", () => {
    const valid = { username: "Ana", email: "ANA@example.com", password: "Password12345", role: "read_only" };
    expect(userCreateSchema.safeParse({ ...valid, username: "ab" }).success).toBe(false);
    expect(userCreateSchema.safeParse({ ...valid, username: "a".repeat(65) }).success).toBe(false);
    expect(userCreateSchema.safeParse({ ...valid, email: "a@localhost" }).success).toBe(false);
    expect(userCreateSchema.safeParse({ ...valid, email: "a".repeat(321) }).success).toBe(false);
    expect(userCreateSchema.safeParse({ ...valid, role: "" }).success).toBe(false);
    expect(userCreateSchema.safeParse({ ...valid, role: "a".repeat(65) }).success).toBe(false);
    expect(passwordResetSchema.safeParse({ password: "a".repeat(11) }).success).toBe(false);
    expect(passwordResetSchema.safeParse({ password: "a".repeat(1025) }).success).toBe(false);
    expect(passwordResetSchema.safeParse({ password: "a".repeat(12) }).success).toBe(true);
    expect(passwordResetSchema.safeParse({ password: "a".repeat(1024) }).success).toBe(true);
    expect(userCreateSchema.parse(valid)).toEqual({ ...valid, email: "ana@example.com" });
  });
});
