import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/app-shell";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
};

describe("AppShell", () => {
  beforeEach(() => {
    replace.mockReset();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not expose navigation for routes that are not delivered", () => {
    render(
      <AppShell user={{ username: "Ana", permissions: ["devices.read", "users.manage"] }}>
        <h1>Dashboard</h1>
      </AppShell>,
    );

    expect(screen.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Dashboard" })).toBeVisible();
    expect(screen.queryByRole("link", { name: /seguridad/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /usuarios/i })).not.toBeInTheDocument();
  });

  it("closes the cookie session, shows progress, and returns to login", async () => {
    const user = userEvent.setup();
    const response = deferred<Response>();
    vi.mocked(fetch).mockReturnValue(response.promise);
    render(<AppShell user={{ username: "Ana", permissions: [] }}><h1>Dashboard</h1></AppShell>);

    await user.click(screen.getByRole("button", { name: /cerrar sesión/i }));
    expect(screen.getByRole("button", { name: /cerrando sesión/i })).toBeDisabled();

    response.resolve(new Response(null, { status: 204 }));
    expect(await screen.findByRole("button", { name: /cerrar sesión/i })).toBeEnabled();
    expect(fetch).toHaveBeenCalledWith("/api/auth/logout", expect.objectContaining({ method: "POST", credentials: "include" }));
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("keeps the user in place and explains when logout fails", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue(Response.json({ error: { code: "REQUEST_FAILED" } }, { status: 500 }));
    render(<AppShell user={{ username: "Ana", permissions: [] }}><h1>Dashboard</h1></AppShell>);

    await user.click(screen.getByRole("button", { name: /cerrar sesión/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/no pudimos cerrar sesión/i);
    expect(screen.getByRole("button", { name: /cerrar sesión/i })).toBeEnabled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("keeps the mobile drawer out of the accessibility tree until opened and restores focus on Escape", async () => {
    const user = userEvent.setup();
    render(<AppShell user={{ username: "Ana", permissions: [] }}><h1>Dashboard</h1></AppShell>);

    const trigger = screen.getByRole("button", { name: /abrir navegación/i });
    expect(screen.queryByRole("dialog", { name: /navegación principal/i })).not.toBeInTheDocument();

    await user.click(trigger);
    const drawer = screen.getByRole("dialog", { name: /navegación principal/i });
    const close = screen.getByRole("button", { name: /cerrar navegación/i });
    expect(close).toHaveFocus();

    within(drawer).getByRole("link", { name: "Dashboard" }).focus();
    await user.keyboard("{Tab}");
    expect(close).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(drawer).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
